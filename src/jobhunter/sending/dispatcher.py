"""Envíos aprobados: comprobaciones, límites, espaciado, simulación y resultado (docs/03 §2).

Reglas que el código hace cumplir (no la IA):
- Nunca se envía dos veces la misma oferta (envío real ya hecho o en curso).
- DM a un **usuario nuevo** (sin chat previo) solo si el contador del día < límite. El
  contador se calcula siempre desde ``contacts`` (no se desincroniza tras un reinicio).
- PeerFlood bloquea los DMs el resto del día. Espaciado aleatorio entre DMs reales.
- En simulación (``dry_run``) el DM va a tus «Mensajes guardados» y el email a tu propia
  dirección; el contador no cambia.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import random
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal, Protocol
from zoneinfo import ZoneInfo

from jobhunter.analysis.writer import full_body, signature
from jobhunter.config import AppConfig
from jobhunter.sending.telegram_sender import Recipient, RecipientError, SendError
from jobhunter.storage.offers import Draft, OfferRepo
from jobhunter.storage.profiles import ProfileRepo
from jobhunter.storage.repositories import SettingsRepo
from jobhunter.storage.sending import Application, ApplicationRepo, ContactRepo

log = logging.getLogger(__name__)

FLOOD_KEY = "sending.peer_flood_day"
WEEKDAYS = ("lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo")
MONTHS = ("ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic")


class DMGateway(Protocol):
    async def resolve(self, target: str) -> Recipient: ...
    async def has_prior_chat(self, recipient: Recipient) -> bool: ...
    async def send(self, to: Any, text: str, cv_path: Path | None, filename: str) -> int: ...


class EmailGateway(Protocol):
    @property
    def configured(self) -> bool: ...
    @property
    def own_address(self) -> str | None: ...
    async def send(
        self,
        to: str,
        subject: str,
        body: str,
        cv_path: Path | None,
        filename: str,
        name: str | None,
    ) -> str: ...


def day_bounds(tz: ZoneInfo, now: datetime | None = None) -> tuple[datetime, datetime]:
    local = (now or datetime.now(UTC)).astimezone(tz)
    start = local.replace(hour=0, minute=0, second=0, microsecond=0)
    return start, start + timedelta(days=1)


def day_label(tz: ZoneInfo, now: datetime | None = None) -> str:
    local = (now or datetime.now(UTC)).astimezone(tz)
    return f"{WEEKDAYS[local.weekday()]} {local.day} {MONTHS[local.month - 1]}"


@dataclass(frozen=True, slots=True)
class Counter:
    new_today: int
    limit: int
    dms_today: int
    emails_today: int
    email_limit: int
    flagged: bool
    warn_ratio: float = 0.8

    @property
    def remaining(self) -> int:
        return max(0, self.limit - self.new_today)

    @property
    def indicator(self) -> str:
        if self.flagged or self.new_today >= self.limit:
            return "🔴"
        return "🟡" if self.new_today >= self.limit * self.warn_ratio else "🟢"


@dataclass(slots=True)
class Preflight:
    channel: str
    target: str
    recipient_label: str
    dry_run: bool
    counter: Counter
    is_new: bool | None = None  # solo DM
    blocked: Literal["limit", "error"] | None = None
    reason: str | None = None
    notes: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.blocked is None


ResultHook = Callable[[Application], Awaitable[None]]


class Dispatcher:
    def __init__(
        self,
        *,
        config: AppConfig,
        tz: ZoneInfo,
        dm: DMGateway,
        email: EmailGateway,
        contacts: ContactRepo,
        applications: ApplicationRepo,
        offers: OfferRepo,
        profiles: ProfileRepo,
        settings: SettingsRepo,
    ) -> None:
        self._cfg = config
        self._tz = tz
        self._dm = dm
        self._email = email
        self.contacts = contacts
        self.applications = applications
        self._offers = offers
        self._profiles = profiles
        self._settings = settings
        self._queue: asyncio.Queue[int] = asyncio.Queue()
        self._lock = asyncio.Lock()
        self.on_result: ResultHook | None = None

    @property
    def tz(self) -> ZoneInfo:
        return self._tz

    # --- contador -----------------------------------------------------------------
    async def _flagged_today(self) -> bool:
        return await self._settings.get(FLOOD_KEY) == datetime.now(self._tz).date().isoformat()

    async def counter(self) -> Counter:
        start, end = day_bounds(self._tz)
        dm_cfg = self._cfg.sending.telegram_dm
        return Counter(
            new_today=await self.contacts.new_users_between(start, end),
            limit=dm_cfg.max_new_users_per_day,
            dms_today=await self.applications.count_sent("telegram_dm", start, end),
            emails_today=await self.applications.count_sent("email", start, end),
            email_limit=self._cfg.sending.email.max_per_day,
            flagged=await self._flagged_today(),
            warn_ratio=dm_cfg.warn_ratio,
        )

    async def is_known(self, target: str) -> bool | None:
        """Para la tarjeta: True si ya hubo contacto; None si no se sabe sin consultar Telegram."""
        contact = None
        if target.startswith("@"):
            contact = await self.contacts.by_username(target)
        elif target.startswith("id:") and target[3:].isdigit():
            contact = await self.contacts.by_user(int(target[3:]))
        return True if contact and contact.known else None

    # --- comprobaciones previas ---------------------------------------------------
    @property
    def cv_path(self) -> Path:
        return self._cfg.cv.path

    async def preflight(self, offer_id: int, draft: Draft, dry_run: bool) -> Preflight:
        counter = await self.counter()
        target = draft.target or ""
        pre = Preflight(draft.channel, target, target, dry_run, counter)

        def block(kind: Literal["limit", "error"], reason: str) -> Preflight:
            pre.blocked, pre.reason = kind, reason
            return pre

        if draft.channel not in ("telegram_dm", "email") or not target:
            return block("error", "Esta oferta se aplica a mano (formulario o enlace).")
        if (done := await self.applications.blocking_for_offer(offer_id)) is not None:
            when = (done.sent_at or done.approved_at)[:16].replace("T", " ")
            return block("error", f"Ya aplicaste a esta oferta ({when} UTC, {done.target}).")
        if not await asyncio.to_thread(self.cv_path.exists):
            return block("error", "No encuentro tu CV en PDF. Súbelo con /cv.")

        if draft.channel == "telegram_dm":
            try:
                recipient = await self._dm.resolve(target)
            except RecipientError as exc:
                return block("error", str(exc))
            pre.recipient_label = recipient.label
            contact = await self.contacts.by_user(recipient.user_id)
            known = recipient.is_self or (contact is not None and contact.known)
            if not known:
                known = await self._dm.has_prior_chat(recipient)
            pre.is_new = not known
            limit_reason = None
            if counter.flagged:
                limit_reason = "Telegram limitó tu cuenta hoy (PeerFlood): no se envían más DMs."
            elif pre.is_new and counter.new_today >= counter.limit:
                limit_reason = (
                    f"⛔ Límite de usuarios nuevos alcanzado ({counter.new_today}/{counter.limit})."
                )
            if dry_run:
                pre.notes.append(
                    f"🧪 Simulación: el mensaje y el CV irán a tus «Mensajes guardados», "
                    f"no a {recipient.label}."
                )
                if limit_reason:
                    pre.notes.append(f"En un envío real se bloquearía: {limit_reason}")
            elif limit_reason:
                return block("limit", limit_reason)
        else:
            if not dry_run and not self._email.configured:
                return block(
                    "error",
                    "El email no está configurado: añade SMTP_USER, SMTP_PASSWORD y SMTP_FROM "
                    "a .env y reinicia el bot.",
                )
            if not dry_run and counter.emails_today >= counter.email_limit:
                return block(
                    "limit",
                    f"⛔ Límite diario de emails alcanzado "
                    f"({counter.emails_today}/{counter.email_limit}).",
                )
            if dry_run:
                own = self._email.own_address
                pre.notes.append(
                    f"🧪 Simulación: el email irá a tu dirección ({own}), no a {target}."
                    if self._email.configured and own
                    else "🧪 Simulación: el email no está configurado; solo se registra, "
                    "no se envía nada."
                )
        return pre

    # --- cola ---------------------------------------------------------------------
    async def estimated_wait(self, channel: str, dry_run: bool) -> float:
        if channel != "telegram_dm" or dry_run:
            return 0.0
        last = await self.applications.last_dm_sent_at()
        if not last:
            return 0.0
        sent = datetime.fromisoformat(last.replace("Z", "+00:00"))
        ready = sent + timedelta(seconds=self._cfg.sending.telegram_dm.min_delay_seconds)
        return max(0.0, (ready - datetime.now(UTC)).total_seconds())

    def enqueue(self, app_id: int) -> None:
        self._queue.put_nowait(app_id)

    async def recover(self) -> None:
        """Tras un reinicio: lo que estaba enviándose se marca fallido (sin reintentar a ciegas)
        y lo que esperaba en cola se vuelve a encolar."""
        for app_id in await self.applications.ids_with_status("sending"):
            await self.applications.update(
                app_id,
                status="failed",
                error="Interrumpido por un reinicio: comprueba si llegó antes de reintentar.",
            )
        for app_id in await self.applications.ids_with_status("queued"):
            self.enqueue(app_id)

    async def run(self) -> None:
        await self.recover()
        while True:
            app_id = await self._queue.get()
            try:
                app = await self.process(app_id)
            except Exception:
                log.exception("Error inesperado enviando la aplicación #%s", app_id)
                await self.applications.update(app_id, status="failed", error="Error inesperado")
                app = await self.applications.get(app_id)
            if app is not None and self.on_result is not None:
                with contextlib.suppress(Exception):
                    await self.on_result(app)

    # --- envío --------------------------------------------------------------------
    async def _wait_spacing(self) -> None:
        dm_cfg = self._cfg.sending.telegram_dm
        last = await self.applications.last_dm_sent_at()
        if not last:
            return
        sent = datetime.fromisoformat(last.replace("Z", "+00:00"))
        delay = random.uniform(
            dm_cfg.min_delay_seconds, max(dm_cfg.min_delay_seconds, dm_cfg.max_delay_seconds)
        )
        wait = (sent + timedelta(seconds=delay) - datetime.now(UTC)).total_seconds()
        if wait > 0:
            log.info("Espaciado entre DMs: espero %.0f s", wait)
            await asyncio.sleep(wait)

    async def _fail(self, app: Application, error: str) -> Application:
        await self.applications.update(app.id, status="failed", error=error[:500])
        if not app.dry_run:
            await self._offers.set_status(app.offer_id, "send_failed")
        log.warning("Aplicación #%s fallida: %s", app.id, error)
        result = await self.applications.get(app.id)
        assert result is not None
        return result

    async def process(self, app_id: int) -> Application | None:
        async with self._lock:
            app = await self.applications.get(app_id)
            if app is None or app.status != "queued":
                return app
            await self.applications.update(app_id, status="sending")
            draft = await self._offers.draft(app.draft_id)
            if draft is None:
                return await self._fail(app, "El borrador ya no existe.")
            profile = await self._profiles.active()
            sig = signature(self._cfg, profile.profile if profile else None)
            body = full_body(draft.body, sig)
            cv = self.cv_path if await asyncio.to_thread(self.cv_path.exists) else None
            if cv is None:
                return await self._fail(app, "No encuentro tu CV en PDF. Súbelo con /cv.")
            filename = self._cfg.cv.attachment_filename
            dm_cfg = self._cfg.sending.telegram_dm
            try:
                if app.channel == "telegram_dm":
                    await self._send_dm(app, body, cv if dm_cfg.send_cv_as_file else None, filename)
                else:
                    name = profile.profile.full_name if profile else None
                    external = await self._send_email(app, draft, body, cv, filename, name)
                    await self.applications.update(app.id, status="sent", external_id=external)
            except (SendError, RecipientError) as exc:
                if isinstance(exc, SendError) and exc.flagged:
                    await self._settings.set(FLOOD_KEY, datetime.now(self._tz).date().isoformat())
                return await self._fail(app, str(exc))
            if not app.dry_run:
                await self._offers.set_status(app.offer_id, "applied")
            log.info(
                "Aplicación #%s enviada por %s a %s%s",
                app.id, app.channel, app.target, " (simulación)" if app.dry_run else "",
            )  # fmt: skip
            return await self.applications.get(app.id)

    async def _send_dm(self, app: Application, body: str, cv: Path | None, filename: str) -> None:
        recipient = await self._dm.resolve(app.target)
        if app.dry_run:
            text = f"🧪 SIMULACIÓN — destinatario real: {recipient.label}\n\n{body}"
            msg_id = await self._dm.send("me", text, cv, filename)
            await self.applications.update(app.id, status="sent", external_id=str(msg_id))
            return

        counter = await self.counter()
        if counter.flagged:
            raise SendError("Telegram limitó tu cuenta hoy (PeerFlood): no se envían más DMs.")
        contact = await self.contacts.by_user(recipient.user_id)
        if contact is None:
            prior = await self._dm.has_prior_chat(recipient)
            contact = await self.contacts.ensure(
                recipient.user_id, recipient.username, recipient.display, prior
            )
        is_new = not contact.known and not recipient.is_self
        # Segunda comprobación del límite: pudo cambiar mientras esperaba en la cola.
        if is_new and counter.new_today >= counter.limit:
            raise SendError(
                f"Límite de usuarios nuevos alcanzado ({counter.new_today}/{counter.limit})."
            )
        await self._wait_spacing()
        msg_id = await self._dm.send(recipient.entity, body, cv, filename)
        await self.contacts.record_dm(contact.id, first_contact=is_new)
        await self.applications.update(
            app.id,
            status="sent",
            external_id=str(msg_id),
            contact_id=contact.id,
            was_new_contact=int(is_new),
        )

    async def _send_email(
        self,
        app: Application,
        draft: Draft,
        body: str,
        cv: Path,
        filename: str,
        name: str | None,
    ) -> str:
        subject = draft.subject or "Candidatura"
        if app.dry_run:
            own = self._email.own_address
            if not self._email.configured or not own:
                return "simulado (email no configurado)"
            return await self._email.send(
                own, f"[SIMULACIÓN → {app.target}] {subject}", body, cv, filename, name
            )
        counter = await self.counter()
        if counter.emails_today >= counter.email_limit:
            raise SendError(f"Límite diario de emails alcanzado ({counter.email_limit}).")
        return await self._email.send(app.target, subject, body, cv, filename, name)
