"""Envíos con gateways falsos: contador de usuarios nuevos, límites, simulación e idempotencia."""

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from jobhunter.analysis.channel_selector import ChannelOption
from jobhunter.config import AppConfig
from jobhunter.sending.dispatcher import Counter, Dispatcher, day_bounds
from jobhunter.sending.telegram_sender import Recipient, RecipientError, SendError
from jobhunter.storage.db import Database
from jobhunter.storage.offers import OfferRepo
from jobhunter.storage.profiles import ProfileRepo
from jobhunter.storage.repositories import SettingsRepo
from jobhunter.storage.sending import ApplicationRepo, ContactRepo
from tests.test_pipeline import active_profile, add_raw
from tests.test_rules_guard import extraction

TZ = ZoneInfo("America/Havana")


@dataclass
class FakeDM:
    prior: set[int] = field(default_factory=set)  # usuarios con chat previo
    sent: list[tuple[Any, str]] = field(default_factory=list)
    fail: SendError | None = None

    async def resolve(self, target: str) -> Recipient:
        if target == "@ghost":
            raise RecipientError("El usuario @ghost no existe.")
        uid = abs(hash(target)) % 10_000_000
        return Recipient(uid, target.lstrip("@"), target, False, f"entity:{target}")

    async def has_prior_chat(self, recipient: Recipient) -> bool:
        return recipient.user_id in self.prior

    async def send(self, to: Any, text: str, cv_path: Path | None, filename: str) -> int:
        if self.fail:
            raise self.fail
        self.sent.append((to, text))
        return len(self.sent)


@dataclass
class FakeEmail:
    configured: bool = True
    own_address: str | None = "yo@mail.com"
    sent: list[tuple[str, str]] = field(default_factory=list)

    async def send(
        self, to: str, subject: str, body: str, cv_path: Any, filename: str, name: Any
    ) -> str:
        self.sent.append((to, subject))
        return f"<id{len(self.sent)}@mail.com>"


def config(tmp_path: Path, limit: int = 2) -> AppConfig:
    cv = tmp_path / "cv.pdf"
    cv.write_bytes(b"%PDF-1.4")
    return AppConfig.model_validate(
        {
            "cv": {"path": str(cv)},
            "sending": {
                "telegram_dm": {
                    "max_new_users_per_day": limit,
                    "min_delay_seconds": 0,
                    "max_delay_seconds": 0,
                },
                "email": {"max_per_day": 1},
            },
        }
    )


async def setup(db: Database, tmp_path: Path, limit: int = 2, email: FakeEmail | None = None):
    cfg = config(tmp_path, limit)
    dm, mail = FakeDM(), email or FakeEmail()
    offers = OfferRepo(db)
    d = Dispatcher(
        config=cfg, tz=TZ, dm=dm, email=mail, contacts=ContactRepo(db),
        applications=ApplicationRepo(db), offers=offers, profiles=ProfileRepo(db),
        settings=SettingsRepo(db),
    )  # fmt: skip
    await active_profile(db)
    return d, dm, mail, offers


_n = 0


async def offer_with_draft(db: Database, offers: OfferRepo, kind: str, target: str):
    global _n
    _n += 1
    raw_id = await add_raw(db, f"Oferta Node.js remoto {_n}", msg_id=_n + 1000)
    from jobhunter.analysis.contact_validator import ValidContacts

    offer_id = await offers.create(
        raw_id, extraction(), ValidContacts(), [], False, [], "m", "notified"
    )
    draft = await offers.add_draft(
        offer_id, ChannelOption(kind, target, target), "Asunto" if kind == "email" else None,
        "Hola, me interesa.", "llm", [], "m",
    )  # fmt: skip
    return offer_id, draft


async def send(d: Dispatcher, offer_id: int, draft: Any, dry_run: bool = False):
    app_id = await d.applications.create(
        offer_id, draft.id, draft.channel, draft.target, "cv", dry_run, None
    )
    return await d.process(app_id)


async def test_new_user_counter_and_limit(db: Database, tmp_path: Path) -> None:
    d, _dm, _, offers = await setup(db, tmp_path, limit=2)
    for user in ("@ana", "@bob"):
        oid, draft = await offer_with_draft(db, offers, "telegram_dm", user)
        pre = await d.preflight(oid, draft, dry_run=False)
        assert pre.ok and pre.is_new
        app = await send(d, oid, draft)
        assert app is not None and app.status == "sent" and app.was_new_contact
    assert (await d.counter()).new_today == 2

    # Tercer usuario nuevo: bloqueado por el límite (antes y en el momento de enviar)
    oid, draft = await offer_with_draft(db, offers, "telegram_dm", "@carla")
    pre = await d.preflight(oid, draft, dry_run=False)
    assert pre.blocked == "limit" and "2/2" in (pre.reason or "")
    app = await send(d, oid, draft)
    assert app is not None and app.status == "failed" and "Límite" in (app.error or "")
    offer = await offers.get(oid)
    assert offer is not None and offer.status == "send_failed"

    # Un usuario ya contactado no cuenta y sí se le puede escribir
    oid, draft = await offer_with_draft(db, offers, "telegram_dm", "@ana")
    pre = await d.preflight(oid, draft, dry_run=False)
    assert pre.ok and pre.is_new is False
    app = await send(d, oid, draft)
    assert app is not None and app.status == "sent" and not app.was_new_contact
    assert (await d.counter()).new_today == 2 and (await d.counter()).dms_today == 3


async def test_prior_chat_does_not_count(db: Database, tmp_path: Path) -> None:
    d, dm, _, offers = await setup(db, tmp_path, limit=1)
    oid, draft = await offer_with_draft(db, offers, "telegram_dm", "@vieja_amiga")
    dm.prior.add((await dm.resolve("@vieja_amiga")).user_id)
    pre = await d.preflight(oid, draft, dry_run=False)
    assert pre.ok and pre.is_new is False
    await send(d, oid, draft)
    assert (await d.counter()).new_today == 0


async def test_never_sends_twice(db: Database, tmp_path: Path) -> None:
    d, _dm, _, offers = await setup(db, tmp_path)
    oid, draft = await offer_with_draft(db, offers, "telegram_dm", "@ana")
    await send(d, oid, draft)
    pre = await d.preflight(oid, draft, dry_run=False)
    assert pre.blocked == "error" and "Ya aplicaste" in (pre.reason or "")
    offer = await offers.get(oid)
    assert offer is not None and offer.status == "applied"


async def test_dry_run_goes_to_saved_messages(db: Database, tmp_path: Path) -> None:
    d, dm, mail, offers = await setup(db, tmp_path, limit=0)
    oid, draft = await offer_with_draft(db, offers, "telegram_dm", "@ana")
    pre = await d.preflight(oid, draft, dry_run=True)
    assert pre.ok and any("Simulación" in n for n in pre.notes)  # limit 0 no bloquea simulación
    app = await send(d, oid, draft, dry_run=True)
    assert app is not None and app.status == "sent" and app.dry_run
    assert dm.sent[0][0] == "me" and "SIMULACIÓN" in dm.sent[0][1]
    assert (await d.counter()).new_today == 0 and (await d.counter()).dms_today == 0
    offer = await offers.get(oid)
    assert offer is not None and offer.status == "notified"  # se puede enviar de verdad luego
    assert (await d.preflight(oid, draft, dry_run=False)).blocked != "error"

    oid2, draft2 = await offer_with_draft(db, offers, "email", "jobs@acme.com")
    app = await send(d, oid2, draft2, dry_run=True)
    assert app is not None and app.status == "sent"
    assert mail.sent == [("yo@mail.com", "[SIMULACIÓN → jobs@acme.com] Asunto")]


async def test_email_limit_and_config(db: Database, tmp_path: Path) -> None:
    d, _, _mail, offers = await setup(db, tmp_path)
    oid, draft = await offer_with_draft(db, offers, "email", "jobs@acme.com")
    app = await send(d, oid, draft)
    assert app is not None and app.status == "sent" and app.external_id == "<id1@mail.com>"
    oid2, draft2 = await offer_with_draft(db, offers, "email", "rrhh@beta.com")
    pre = await d.preflight(oid2, draft2, dry_run=False)
    assert pre.blocked == "limit"  # max_per_day = 1

    d2, _, _, offers2 = await setup(db, tmp_path, email=FakeEmail(configured=False))
    oid3, draft3 = await offer_with_draft(db, offers2, "email", "x@y.com")
    pre = await d2.preflight(oid3, draft3, dry_run=False)
    assert pre.blocked == "error" and "SMTP" in (pre.reason or "")


async def test_peer_flood_blocks_rest_of_day(db: Database, tmp_path: Path) -> None:
    d, dm, _, offers = await setup(db, tmp_path, limit=10)
    dm.fail = SendError("PeerFlood", flagged=True)
    oid, draft = await offer_with_draft(db, offers, "telegram_dm", "@ana")
    app = await send(d, oid, draft)
    assert app is not None and app.status == "failed"
    assert (await d.counter()).flagged
    dm.fail = None
    oid2, draft2 = await offer_with_draft(db, offers, "telegram_dm", "@bob")
    assert (await d.preflight(oid2, draft2, dry_run=False)).blocked == "limit"


async def test_recipient_errors_and_missing_cv(db: Database, tmp_path: Path) -> None:
    d, _, _, offers = await setup(db, tmp_path)
    oid, draft = await offer_with_draft(db, offers, "telegram_dm", "@ghost")
    pre = await d.preflight(oid, draft, dry_run=False)
    assert pre.blocked == "error" and "no existe" in (pre.reason or "")
    (tmp_path / "cv.pdf").unlink()
    oid2, draft2 = await offer_with_draft(db, offers, "telegram_dm", "@ana")
    assert "CV" in ((await d.preflight(oid2, draft2, dry_run=False)).reason or "")


async def test_recover_after_restart(db: Database, tmp_path: Path) -> None:
    d, _, _, offers = await setup(db, tmp_path)
    oid, draft = await offer_with_draft(db, offers, "telegram_dm", "@ana")
    a1 = await d.applications.create(oid, draft.id, "telegram_dm", "@ana", "cv", False, None)
    a2 = await d.applications.create(oid, draft.id, "telegram_dm", "@ana", "cv", False, None)
    await d.applications.update(a1, status="sending")
    await d.recover()
    app1 = await d.applications.get(a1)
    assert app1 is not None and app1.status == "failed" and "reinicio" in (app1.error or "")
    assert d._queue.get_nowait() == a2


def test_counter_indicator_and_day_bounds() -> None:
    def c(n: int, flagged: bool = False) -> Counter:
        return Counter(n, 10, 0, 0, 30, flagged)

    assert (c(3).indicator, c(8).indicator, c(10).indicator, c(1, True).indicator) == (
        "🟢", "🟡", "🔴", "🔴",
    )  # fmt: skip
    # 02:00 UTC = 21:00 o 22:00 del día 2 en La Habana (sea cual sea su horario de verano)
    start, end = day_bounds(TZ, datetime(2026, 11, 3, 2, 0, tzinfo=UTC))
    assert start.day == 2 and end - start == timedelta(days=1)


def test_suggestions_from_dismissals() -> None:
    from jobhunter.telegram.handlers.sending import suggestions

    assert suggestions({"salary": 3}, has_min_salary=False)[0].endswith(
        "fija un mínimo con /salario."
    )
    assert suggestions({"salary": 1, "stack": 1}, has_min_salary=True) == []


def test_app_module_imports() -> None:
    import jobhunter.app  # noqa: F401  (detecta errores de import en el cableado)


@pytest.mark.parametrize("port,tls", [(587, False), (465, True)])
def test_email_message(port: int, tls: bool) -> None:
    from jobhunter.sending.email_sender import build_email

    msg = build_email("yo@mail.com", "Yo", "jobs@acme.com", "Asunto", "Hola", b"%PDF", "CV.pdf")
    assert msg["To"] == "jobs@acme.com" and "Yo" in msg["From"] and msg["Message-ID"]
    attachments = list(msg.iter_attachments())
    assert attachments[0].get_filename() == "CV.pdf"


def test_sender_address_formats() -> None:
    from jobhunter.config import Settings
    from jobhunter.sending.email_sender import sender_address

    base = {"tg_api_id": 1, "tg_api_hash": "x", "smtp_user": "yo@gmail.com"}
    s = Settings.model_validate(base | {"smtp_from": "Ana B <yo@gmail.com>"})
    assert sender_address(s) == ("Ana B", "yo@gmail.com")
    s = Settings.model_validate(base | {"smtp_from": "otro@gmail.com"})
    assert sender_address(s) == (None, "otro@gmail.com")
    s = Settings.model_validate(base)
    assert sender_address(s) == (None, "yo@gmail.com")
