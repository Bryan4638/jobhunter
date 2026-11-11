"""/cv, /perfil y subida del CV en PDF.

Callbacks: ``cv:ok:<id>`` confirmar · ``cv:fix:<id>`` corregir · ``cv:re:<id>`` regenerar ·
``cv:no:<id>`` descartar.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from html import escape

from telethon import Button, events
from telethon.errors import MessageNotModifiedError

from jobhunter.context import AppContext
from jobhunter.llm.base import LLMError
from jobhunter.profile.cv_loader import CVError
from jobhunter.storage.profiles import ProfileRecord
from jobhunter.telegram.auth import command, owner_callback, owner_message

log = logging.getLogger(__name__)

SENIORITY_LABEL = {
    "junior": "Junior",
    "mid": "Mid / Semi-senior",
    "senior": "Senior",
    "lead": "Lead",
}
NO_LLM = "⚠️ Falta <code>GEMINI_API_KEY</code> en <code>.env</code>: no puedo analizar el CV."
MAX_MESSAGE = 4000


def _list(items: list[str], limit: int = 15) -> str:
    shown = ", ".join(escape(i) for i in items[:limit])
    return shown + (f" (+{len(items) - limit})" if len(items) > limit else "") if items else "—"


def profile_card(record: ProfileRecord, title: str) -> str:
    p = record.profile
    years = f"{p.years_experience:g}"
    lines = [
        f"{title}\n",
        f"<b>{escape(p.full_name)}</b>",
        f"<i>{escape(p.headline)}</i>",
        f"🎚 {SENIORITY_LABEL.get(p.seniority, p.seniority)} · {years} años de experiencia",
        f"🧰 <b>Principales:</b> {_list(p.core_skills)}",
        f"🔧 <b>Secundarias:</b> {_list(p.secondary_skills, 20)}",
        f"🌐 <b>Idiomas:</b> {_list(p.languages)}",
    ]
    if p.location:
        lines.append(f"📍 {escape(p.location)}")
    if p.highlights:
        lines.append("⭐ <b>Logros:</b>")
        lines += [f"• {escape(h)}" for h in p.highlights]
    contact = " · ".join(escape(x) for x in (p.contact_email, p.contact_phone) if x)
    if contact:
        lines.append(f"📇 {contact}")
    if p.links:
        lines.append("🔗 " + " · ".join(escape(link) for link in p.links))
    text = "\n".join(lines)
    return text if len(text) <= MAX_MESSAGE else text[: MAX_MESSAGE - 1] + "…"


def draft_buttons(profile_id: int) -> list[list[Button]]:
    return [
        [Button.inline("✅ Confirmar", f"cv:ok:{profile_id}".encode())],
        [
            Button.inline("✏️ Corregir", f"cv:fix:{profile_id}".encode()),
            Button.inline("🔄 Regenerar", f"cv:re:{profile_id}".encode()),
        ],
        [Button.inline("❌ Descartar", f"cv:no:{profile_id}".encode())],
    ]


DRAFT_TITLE = "👤 <b>Perfil extraído de tu CV</b> — revísalo:"


async def handle_cv_correction(
    ctx: AppContext, event: events.NewMessage.Event, profile_id: int, text: str
) -> None:
    assert ctx.profiles is not None
    status = await event.respond("⏳ Aplicando la corrección…")
    try:
        record = await ctx.profiles.correct(profile_id, text)
    except ValueError as exc:
        ctx.conversation.clear()
        await status.edit(f"❌ {escape(str(exc))}")
        return
    except LLMError:
        log.exception("Error corrigiendo el perfil %s", profile_id)
        await status.edit("❌ La IA no respondió. Vuelve a escribir la corrección o /cancelar.")
        return
    ctx.conversation.clear()
    await status.edit(profile_card(record, DRAFT_TITLE), buttons=draft_buttons(record.id))


def register(ctx: AppContext) -> None:
    bot = ctx.bot
    is_owner = owner_message(ctx.owner_id)

    @bot.on(events.NewMessage(pattern=command("cv"), func=is_owner))
    async def cv_cmd(event: events.NewMessage.Event) -> None:
        if ctx.profiles is None:
            await event.respond(NO_LLM)
            return
        active = await ctx.profiles.active()
        current = (
            f"Tu CV activo: <b>{escape(active.profile.full_name)}</b> — "
            f"{escape(active.profile.headline)}\n\n"
            if active
            else ""
        )
        await event.respond(
            f"{current}📄 Envíame tu CV en <b>PDF</b> (máx. {ctx.config.cv.max_size_mb} MB). "
            "Lo analizaré con IA y te mostraré el perfil extraído para que lo confirmes."
        )

    ctx.menu_actions["cv"] = cv_cmd

    @bot.on(events.NewMessage(pattern=command("perfil"), func=is_owner))
    async def profile_cmd(event: events.NewMessage.Event) -> None:
        if ctx.profiles is None:
            await event.respond(NO_LLM)
            return
        active = await ctx.profiles.active()
        if active is None:
            await event.respond("Aún no tienes un perfil. Envíame tu CV en PDF con /cv.")
            return
        await event.respond(profile_card(active, "👤 <b>Tu perfil activo</b>"))

    ctx.menu_actions["perfil"] = profile_cmd

    @bot.on(events.NewMessage(func=lambda e: is_owner(e) and e.message.document is not None))
    async def document(event: events.NewMessage.Event) -> None:
        if ctx.profiles is None:
            await event.respond(NO_LLM)
            return
        doc = event.message.document
        name = (event.message.file.name or "").lower()
        if doc.mime_type != "application/pdf" and not name.endswith(".pdf"):
            await event.respond("Solo acepto el CV en formato <b>PDF</b>.")
            return
        max_bytes = ctx.config.cv.max_size_mb * 1024 * 1024
        if doc.size > max_bytes:
            await event.respond(f"El PDF supera {ctx.config.cv.max_size_mb} MB.")
            return

        status = await event.respond("📄 CV recibido. Leyéndolo y analizándolo con IA…")
        ctx.profiles.incoming_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        path = ctx.profiles.incoming_dir / f"cv_{stamp}.pdf"
        await event.message.download_media(file=str(path))
        try:
            record = await ctx.profiles.build_from_pdf(path)
        except CVError as exc:
            path.unlink(missing_ok=True)
            await status.edit(f"❌ {escape(str(exc))}")
            return
        except LLMError:
            log.exception("Error analizando el CV")
            path.unlink(missing_ok=True)
            await status.edit(
                "❌ La IA no respondió (puede estar saturada). Reenvíame el PDF en unos minutos."
            )
            return
        await status.edit(profile_card(record, DRAFT_TITLE), buttons=draft_buttons(record.id))

    @bot.on(events.CallbackQuery(pattern=rb"^cv:", func=owner_callback(ctx.owner_id)))
    async def on_callback(event: events.CallbackQuery.Event) -> None:
        if ctx.profiles is None:
            await event.answer("Falta GEMINI_API_KEY", alert=True)
            return
        _, action, raw_id = event.data.decode().split(":")
        profile_id = int(raw_id)
        try:
            if action == "ok":
                record = await ctx.profiles.confirm(profile_id)
                ctx.conversation.clear()
                await event.edit(
                    profile_card(record, "✅ <b>Perfil confirmado y activo</b>")
                    + "\n\nSi ya había ofertas analizadas, puntúalas con este perfil: /reevaluar"
                )
                await event.answer("Perfil activo")
                if ctx.worker:
                    ctx.worker.wake()  # analizar lo que esperaba al CV
            elif action == "no":
                await ctx.profiles.discard(profile_id)
                ctx.conversation.clear()
                await event.edit("❌ CV descartado. Tu perfil activo no ha cambiado.")
                await event.answer()
            elif action == "fix":
                record = await ctx.profiles.get(profile_id)
                if record is None or record.status != "draft":
                    raise ValueError("Ese perfil ya no está pendiente de confirmar.")
                ctx.conversation.expect("cv_fix", profile_id)
                await event.answer()
                await event.respond(
                    "✏️ Escríbeme qué hay que corregir, por ejemplo:\n"
                    "• <i>tengo 4 años de experiencia, no 6</i>\n"
                    "• <i>añade Docker y AWS a las principales</i>\n"
                    "• <i>mi nivel de inglés es B2</i>\n\n"
                    "O /cancelar."
                )
            elif action == "re":
                await event.answer("Regenerando…")
                await event.edit("⏳ Regenerando el perfil…")
                record = await ctx.profiles.regenerate(profile_id)
                await event.edit(
                    profile_card(record, DRAFT_TITLE), buttons=draft_buttons(record.id)
                )
            else:
                await event.answer()
        except ValueError as exc:
            await event.answer(str(exc), alert=True)
        except LLMError:
            log.exception("Error de IA en callback de CV")
            await event.answer("La IA no respondió. Inténtalo en unos minutos.", alert=True)
            record = await ctx.profiles.get(profile_id)
            if record and record.status == "draft":
                try:
                    await event.edit(
                        profile_card(record, DRAFT_TITLE), buttons=draft_buttons(record.id)
                    )
                except MessageNotModifiedError:
                    pass
