"""Ofertas: tarjetas, botones, /ofertas, /descartadas, /escanear, /resumen, /reevaluar,
/pausa y /reanudar. El envío (✅ Enviar) está en ``handlers/sending.py``.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta

from telethon import Button, events
from telethon.errors import MessageNotModifiedError, RPCError

from jobhunter.analysis.writer import signature
from jobhunter.context import AppContext
from jobhunter.llm.base import LLMError
from jobhunter.storage.offers import AUTO_DISCARDED, OPEN_STATUSES
from jobhunter.telegram.auth import args_of, command, owner_callback, owner_message
from jobhunter.telegram.cards import channel_buttons, offer_buttons, offer_card, offers_list
from jobhunter.telegram.sources import scan_history

log = logging.getLogger(__name__)

DISMISS_REASONS = {
    "salary": "💰 Salario",
    "stack": "🧰 Stack",
    "company": "🏢 Empresa",
    "modality": "📍 Modalidad",
    "other": "Otro",
}


def dismiss_buttons(offer_id: int) -> list[list[Button]]:
    items = [
        Button.inline(label, f"of:dr:{offer_id}:{code}") for code, label in DISMISS_REASONS.items()
    ]
    return [items[:3], items[3:]]


NO_LLM = "⚠️ Falta <code>GEMINI_API_KEY</code> en <code>.env</code>: el análisis está desactivado."
MAX_SCAN_DAYS = 14
MAX_RESCORE_DAYS = 30
MAX_RESCORE = 40  # cada oferta es una llamada a la IA
RESCORE_STATUSES = (
    "low_score", "pending_digest", "notified", "snoozed", "rejected_rules",
)  # fmt: skip
DIGEST_WINDOW_DAYS = 7


async def render_offer(
    ctx: AppContext, offer_id: int, note: str | None = None
) -> tuple[str, list[list[Button]]] | None:
    offer = await ctx.offers.get(offer_id)
    if offer is None:
        return None
    match = await ctx.offers.match(offer_id)
    draft = await ctx.offers.latest_draft(offer_id)
    profile = await ctx.profiles.active() if ctx.profiles else None
    sig = signature(ctx.config, profile.profile if profile else None)
    hint = await dm_hint(ctx, match.channel.kind, match.channel.target) if match else None
    text = offer_card(offer, match, draft, sig, ctx.config.cv.attachment_filename, note, hint)
    return text, offer_buttons(offer, match, draft)


async def dm_hint(ctx: AppContext, kind: str, target: str | None) -> str | None:
    """Contador en la línea del canal (la comprobación exacta se hace al enviar)."""
    if kind != "telegram_dm" or not target:
        return None
    counter = await ctx.dispatcher.counter()
    if counter.flagged:
        return "⛔ DMs bloqueados hoy (PeerFlood)"
    if await ctx.dispatcher.is_known(target):
        return "ya conocido (no cuenta)"
    hint = f"👤 usuario nuevo (hoy: {counter.new_today}/{counter.limit})"
    if counter.remaining <= 2:
        hint += f" ⚠️ te quedan {counter.remaining}"
    return hint


async def send_card(ctx: AppContext, offer_id: int) -> None:
    """Notificación inmediata de una oferta (la llama el worker)."""
    rendered = await render_offer(ctx, offer_id)
    if rendered is None:
        return
    text, buttons = rendered
    try:
        msg = await ctx.bot.send_message(
            ctx.owner_id, text, buttons=buttons or None, link_preview=False
        )
    except (ValueError, RPCError):
        log.exception("No pude enviarte la tarjeta de la oferta #%s", offer_id)
        return
    await ctx.offers.remember_message(msg.id, offer_id, "card")


async def send_digest(ctx: AppContext, *, manual: bool = False) -> bool:
    """Resumen de las ofertas que esperan revisión. Devuelve False si no había nada."""
    since = datetime.now(UTC) - timedelta(days=DIGEST_WINDOW_DAYS)
    items = await ctx.offers.summaries(
        ("pending_digest", "snoozed"), ctx.config.digest.max_items, since
    )
    if not items:
        if manual or not ctx.config.digest.skip_if_empty:
            await ctx.bot.send_message(
                ctx.owner_id, "📋 No hay ofertas pendientes para el resumen."
            )
        return False
    day_ago = datetime.now(UTC) - timedelta(days=1)
    counts = await ctx.offers.counts_since(day_ago)
    discarded = sum(counts.get(s, 0) for s in AUTO_DISCARDED)
    footer = f"Últimas 24 h: {discarded} ofertas descartadas automáticamente (/descartadas)."
    text, buttons = offers_list(
        f"📋 <b>Resumen — {len(items)} ofertas para revisar</b>", items, footer
    )
    msg = await ctx.bot.send_message(ctx.owner_id, text, buttons=buttons or None)
    await ctx.offers.remember_message(msg.id, None, "digest")
    return True


async def _ensure_draft(ctx: AppContext, offer_id: int) -> None:
    """Redacción perezosa: las ofertas del resumen se redactan al abrirlas."""
    if ctx.analyzer is None or ctx.profiles is None:
        return
    offer = await ctx.offers.get(offer_id)
    profile = await ctx.profiles.active()
    if offer is None or profile is None or offer.status in ("rejected_rules", "low_score"):
        return
    try:
        await ctx.analyzer.ensure_draft(offer_id, profile)
    except LLMError:
        log.warning("No pude redactar el borrador de la oferta #%s", offer_id)


async def _edit(event: events.CallbackQuery.Event, text: str, buttons: object) -> None:
    try:
        await event.edit(text, buttons=buttons or None, link_preview=False)
    except MessageNotModifiedError:
        pass


def register(ctx: AppContext) -> None:
    bot = ctx.bot
    is_owner = owner_message(ctx.owner_id)

    @bot.on(events.NewMessage(pattern=command("pausa"), func=is_owner))
    async def pause_cmd(event: events.NewMessage.Event) -> None:
        await ctx.prefs.set_paused(True)
        await event.respond(
            "⏸ <b>Avisos inmediatos en pausa.</b> Sigo leyendo y analizando: las ofertas que "
            "merezcan tarjeta irán al resumen diario y a /ofertas. Reanuda con /reanudar."
        )

    ctx.menu_actions["pausa"] = pause_cmd

    @bot.on(events.NewMessage(pattern=command("reanudar"), func=is_owner))
    async def resume_cmd(event: events.NewMessage.Event) -> None:
        await ctx.prefs.set_paused(False)
        await event.respond("▶️ Avisos inmediatos reanudados.")

    ctx.menu_actions["reanudar"] = resume_cmd

    @bot.on(events.NewMessage(pattern=command("reevaluar"), func=is_owner))
    async def rescore_cmd(event: events.NewMessage.Event) -> None:
        if ctx.analyzer is None or ctx.profiles is None:
            await event.respond(NO_LLM)
            return
        profile = await ctx.profiles.active()
        if profile is None:
            await event.respond("Primero sube tu CV con /cv.")
            return
        args = args_of(event)
        days = max(1, min(int(args) if args.isdigit() else 7, MAX_RESCORE_DAYS))
        since = datetime.now(UTC) - timedelta(days=days)
        ids = await ctx.offers.ids_for_rescore(RESCORE_STATUSES, since, MAX_RESCORE)
        if not ids:
            await event.respond(f"No hay ofertas de los últimos {days} días que reevaluar.")
            return
        status = await event.respond(
            f"🔁 Reevaluando {len(ids)} ofertas de los últimos {days} días con tu perfil y "
            "preferencias actuales…"
        )
        results: dict[str, int] = {}
        failed = 0
        for offer_id in ids:
            try:
                new = await ctx.analyzer.rescore(offer_id, profile)
            except LLMError:
                failed += 1
                continue
            if new:
                results[new] = results.get(new, 0) + 1
        to_review = sum(results.get(s, 0) for s in ("pending_digest", "notified", "snoozed"))
        lines = [
            f"🔁 <b>Reevaluación terminada</b> ({len(ids)} ofertas)",
            f"• 📋 Para revisar: {to_review} (en /ofertas)",
            f"• 📉 Puntuación baja: {results.get('low_score', 0)}",
            f"• 🚫 Descartadas por reglas: {results.get('rejected_rules', 0)}",
        ]
        if failed:
            lines.append(f"• ⚠️ {failed} no se pudieron reevaluar (IA saturada): repite más tarde")
        await status.edit("\n".join(lines))

    ctx.menu_actions["reevaluar"] = rescore_cmd

    @bot.on(events.NewMessage(pattern=command("ofertas"), func=is_owner))
    async def offers_cmd(event: events.NewMessage.Event) -> None:
        args = args_of(event)
        limit = int(args) if args.isdigit() else 10
        items = await ctx.offers.summaries(OPEN_STATUSES, max(1, min(limit, 30)))
        pending = await ctx.raw_messages.count_pending()
        footer = f"⏳ {pending} mensajes en cola de análisis." if pending else ""
        footer += ("\n" if footer else "") + "Descartadas automáticamente: /descartadas"
        if not items:
            await event.respond("No hay ofertas pendientes de revisar.\n\n" + footer)
            return
        text, buttons = offers_list(f"💼 <b>Ofertas por revisar ({len(items)})</b>", items, footer)
        await event.respond(text, buttons=buttons or None)

    ctx.menu_actions["ofertas"] = offers_cmd

    @bot.on(events.NewMessage(pattern=command("descartadas"), func=is_owner))
    async def discarded_cmd(event: events.NewMessage.Event) -> None:
        since = datetime.now(UTC) - timedelta(days=DIGEST_WINDOW_DAYS)
        items = await ctx.offers.summaries(AUTO_DISCARDED, 15, since)
        if not items:
            await event.respond("No hay ofertas descartadas en los últimos 7 días.")
            return
        text, buttons = offers_list(
            "🚫 <b>Descartadas automáticamente (7 días)</b>\n"
            "Por tus reglas (modalidad, idioma, seniority…) o por puntuación baja.",
            items,
        )
        await event.respond(text, buttons=buttons or None)

    ctx.menu_actions["descartadas"] = discarded_cmd

    @bot.on(events.NewMessage(pattern=command("resumen"), func=is_owner))
    async def digest_cmd(event: events.NewMessage.Event) -> None:
        await send_digest(ctx, manual=True)

    ctx.menu_actions["resumen"] = digest_cmd

    @bot.on(events.NewMessage(pattern=command("escanear"), func=is_owner))
    async def scan_cmd(event: events.NewMessage.Event) -> None:
        args = args_of(event)
        days = int(args) if args.isdigit() else 3
        days = max(1, min(days, MAX_SCAN_DAYS))
        if not len(ctx.registry):
            await event.respond("No hay grupos monitorizados. Añade uno con /grupo_add.")
            return
        status = await event.respond(
            f"🔎 Escaneando los últimos {days} días de {len(ctx.registry)} chats…"
        )
        report = await scan_history(ctx.userbot, ctx.registry, ctx.ingestor, days)
        await status.edit(
            f"🔎 <b>Escaneo de {days} días terminado</b>\n"
            f"• Mensajes leídos: {report.read}\n"
            f"• 🆕 Nuevos para analizar: {report.new}\n"
            f"• 🚫 Filtrados: {report.filtered} · ♻️ Duplicados: {report.duplicate}\n"
            f"• Ya estaban guardados: {report.already}\n\n"
            + (
                "Los analizo ahora; las mejores ofertas te llegarán como tarjeta y el resto "
                "estará en /ofertas."
                if report.new
                else "No hay mensajes nuevos que analizar."
            )
        )
        if ctx.worker:
            ctx.worker.wake()

    ctx.menu_actions["escanear"] = scan_cmd

    @bot.on(events.CallbackQuery(pattern=rb"^of:", func=owner_callback(ctx.owner_id)))
    async def on_callback(event: events.CallbackQuery.Event) -> None:
        parts = event.data.decode().split(":")
        action, offer_id = parts[1], int(parts[2])
        arg = parts[3] if len(parts) > 3 else None
        offer = await ctx.offers.get(offer_id)
        if offer is None:
            await event.answer("Esa oferta ya no existe.", alert=True)
            return

        async def refresh(note: str | None = None, *, new_message: bool = False) -> None:
            rendered = await render_offer(ctx, offer_id, note)
            if rendered is None:
                return
            text, buttons = rendered
            if new_message:
                msg = await event.respond(text, buttons=buttons or None, link_preview=False)
                await ctx.offers.remember_message(msg.id, offer_id, "card")
            else:
                await _edit(event, text, buttons)

        try:
            if action == "v":  # ver desde una lista o el resumen: tarjeta nueva
                if offer.status in ("pending_digest", "snoozed"):
                    await ctx.offers.set_status(offer_id, "notified")
                await event.answer()
                await _ensure_draft(ctx, offer_id)
                await refresh(new_message=True)
            elif action == "b":  # volver a la tarjeta desde el menú de canales
                await event.answer()
                await refresh()
            elif action == "l":
                await ctx.offers.set_status(offer_id, "snoozed")
                await event.answer("Vuelve en el próximo resumen")
                await refresh()
            elif action == "d":
                await ctx.offers.set_status(offer_id, "dismissed")
                await event.answer("Descartada")
                rendered = await render_offer(ctx, offer_id, "🗑 <b>Descartada</b> · ¿por qué?")
                if rendered:
                    await _edit(event, rendered[0], dismiss_buttons(offer_id))
            elif action == "dr":
                if arg in DISMISS_REASONS:
                    await ctx.offers.set_dismiss_reason(offer_id, arg)
                await event.answer("Gracias, lo tendré en cuenta en /stats")
                label = DISMISS_REASONS.get(arg or "", "")
                await refresh(f"🗑 <b>Descartada</b>{f' · {label}' if label else ''}")
            elif action == "a":
                await ctx.offers.set_status(offer_id, "applied")
                await event.answer("Marcada como aplicada")
                await refresh()
            elif action == "s":
                from jobhunter.telegram.handlers.sending import start_send

                await start_send(ctx, event, offer_id)
            elif action == "c":
                match = await ctx.offers.match(offer_id)
                if match is None:
                    await event.answer("Esta oferta no tiene puntuación.", alert=True)
                    return
                await event.answer()
                await _edit(
                    event,
                    "📨 <b>Elige el canal</b>\nAl cambiarlo se reescribe el borrador "
                    "(el formato de email y de DM es distinto).",
                    channel_buttons(offer_id, match),
                )
            elif action in ("w", "r", "ch"):
                await _regenerate(ctx, event, offer_id, action, arg, refresh)
            elif action == "e":
                if await ctx.offers.latest_draft(offer_id) is None:
                    await event.answer("Primero genera el borrador.", alert=True)
                    return
                ctx.conversation.expect("draft_edit", offer_id)
                await event.answer()
                await event.respond(
                    "✏️ Envíame el <b>nuevo texto</b> del mensaje (sin firma: se añade sola), "
                    "o una <b>instrucción</b> que empiece por <code>!</code>, por ejemplo:\n"
                    "• <code>!más corto</code>\n"
                    "• <code>!menciona mi experiencia con microservicios</code>\n\n"
                    "O /cancelar."
                )
            else:
                await event.answer()
        except LLMError:
            log.exception("Error de IA en la oferta #%s", offer_id)
            await event.answer("La IA no respondió. Inténtalo en unos minutos.", alert=True)
            await refresh()


async def _regenerate(
    ctx: AppContext,
    event: events.CallbackQuery.Event,
    offer_id: int,
    action: str,
    arg: str | None,
    refresh: Callable[[], Awaitable[None]],
) -> None:
    if ctx.analyzer is None or ctx.profiles is None:
        await event.answer("Falta GEMINI_API_KEY", alert=True)
        return
    profile = await ctx.profiles.active()
    if profile is None:
        await event.answer("Primero sube tu CV con /cv.", alert=True)
        return
    match = await ctx.offers.match(offer_id)
    if match is None:
        await event.answer("Esta oferta no tiene puntuación.", alert=True)
        return
    channel = None
    if action == "ch":
        idx = int(arg or 0)
        if idx >= len(match.options):
            await event.answer("Opción no válida.", alert=True)
            return
        channel = match.options[idx]
        await ctx.offers.set_channel(match.id, channel, "elegido por ti")
    await event.answer("Redactando…")
    await event.edit("⏳ Redactando el mensaje…")
    await ctx.analyzer.new_draft(offer_id, profile, channel=channel)
    await refresh()


async def handle_draft_edit(ctx: AppContext, event: events.NewMessage.Event, offer_id: int) -> None:
    """Texto libre tras ✏️: reemplaza el cuerpo o, si empieza por «!», lo revisa la IA."""
    text = event.message.message.strip()
    match = await ctx.offers.match(offer_id)
    current = await ctx.offers.latest_draft(offer_id)
    if match is None or current is None:
        ctx.conversation.clear()
        await event.respond("Esa oferta ya no tiene borrador.")
        return
    channel = match.channel
    if text.startswith("!"):
        if ctx.analyzer is None or ctx.profiles is None:
            await event.respond(NO_LLM)
            return
        profile = await ctx.profiles.active()
        if profile is None:
            await event.respond("Primero sube tu CV con /cv.")
            return
        status = await event.respond("⏳ Aplicando tu instrucción…")
        try:
            await ctx.analyzer.new_draft(offer_id, profile, channel=channel, instruction=text[1:])
        except LLMError:
            log.exception("Error revisando el borrador de la oferta #%s", offer_id)
            await status.edit("❌ La IA no respondió. Vuelve a enviar la instrucción o /cancelar.")
            return
        await status.delete()
    else:
        await ctx.offers.add_draft(offer_id, channel, current.subject, text, "user_edit", [], None)
    ctx.conversation.clear()
    rendered = await render_offer(ctx, offer_id, "✏️ <b>Borrador actualizado</b>")
    if rendered:
        msg = await event.respond(rendered[0], buttons=rendered[1] or None, link_preview=False)
        await ctx.offers.remember_message(msg.id, offer_id, "card")
