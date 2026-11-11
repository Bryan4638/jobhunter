"""Envío: confirmación final, resultado, /contador, /aplicadas y /stats (docs/06).

Callbacks (``snd:<acción>:<id>[:<arg>]``):
    ok:<oferta>:<borrador>  confirmar envío      no:<oferta>     cancelar
    mail:<oferta>           cambiar a email      later:<oferta>  posponer a mañana
    retry:<oferta>          reintentar
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from html import escape

from telethon import Button, events
from telethon.errors import MessageNotModifiedError, RPCError

from jobhunter.analysis.writer import full_body, signature
from jobhunter.config import effective_dry_run
from jobhunter.context import AppContext
from jobhunter.llm.base import LLMError
from jobhunter.sending.dispatcher import Preflight, day_bounds
from jobhunter.storage.sending import Application
from jobhunter.telegram.auth import command, owner_callback, owner_message
from jobhunter.telegram.cards import CHANNEL_NAME

log = logging.getLogger(__name__)

MAX_MESSAGE = 4096
REASON_LABEL = {
    "salary": "salario",
    "stack": "stack",
    "company": "empresa",
    "modality": "modalidad",
    "other": "otro",
}


def _local(ctx: AppContext, iso: str | None) -> str:
    if not iso:
        return "—"
    dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    return dt.astimezone(ctx.settings.zoneinfo).strftime("%d/%m %H:%M")


def _new_user_line(pre: Preflight) -> str:
    c = pre.counter
    if pre.is_new is None:
        return ""
    if not pre.is_new:
        return "👥 Ya habéis hablado antes: <b>no cuenta</b> como usuario nuevo."
    nth = c.new_today + 1
    warn = f" ⚠️ te quedarán {c.limit - nth}" if c.limit - nth <= 2 else ""
    return f"👤 Será el usuario nuevo <b>nº {nth} de {c.limit}</b> de hoy.{warn}"


async def start_send(ctx: AppContext, event: events.CallbackQuery.Event, offer_id: int) -> None:
    """✅ Enviar → comprobaciones → confirmación final (o bloqueo con alternativas)."""
    offer = await ctx.offers.get(offer_id)
    draft = await ctx.offers.latest_draft(offer_id)
    if offer is None or draft is None:
        await event.answer("Esta oferta no tiene borrador.", alert=True)
        return
    await event.answer("Comprobando…")
    dry_run = effective_dry_run(ctx.settings, ctx.config)
    pre = await ctx.dispatcher.preflight(offer_id, draft, dry_run)

    if not pre.ok:
        buttons = []
        if pre.blocked == "limit":
            match = await ctx.offers.match(offer_id)
            if match and any(o.kind == "email" for o in match.options):
                buttons.append([Button.inline("📧 Enviar por email", f"snd:mail:{offer_id}")])
            buttons.append([Button.inline("⏰ Mañana", f"snd:later:{offer_id}")])
        buttons.append([Button.inline("Cerrar", f"snd:no:{offer_id}")])
        await event.respond(f"❌ {escape(pre.reason or 'No se puede enviar.')}", buttons=buttons)
        return

    profile = await ctx.profiles.active() if ctx.profiles else None
    body = full_body(draft.body, signature(ctx.config, profile.profile if profile else None))
    lines = ["📤 <b>Vas a enviar</b>"]
    channel = "DM de Telegram" if draft.channel == "telegram_dm" else "Email"
    lines.append(f"Canal: <b>{channel}</b> → <b>{escape(pre.recipient_label)}</b>")
    if user_line := _new_user_line(pre):
        lines.append(user_line)
    if draft.subject:
        lines.append(f"Asunto: <b>{escape(draft.subject)}</b>")
    lines.append(f"\n{escape(body)}\n")
    lines.append(f"📎 Adjunto: {escape(ctx.config.cv.attachment_filename)}")
    lines += [escape(n) for n in pre.notes]
    wait = await ctx.dispatcher.estimated_wait(draft.channel, dry_run)
    if wait > 0:
        lines.append(f"⏳ Por el espaciado entre DMs saldrá en ~{int(wait)} s.")
    text = "\n".join(lines)
    if len(text) > MAX_MESSAGE:
        text = text[: MAX_MESSAGE - 1] + "…"
    buttons = [
        [Button.inline("✅ Confirmar envío", f"snd:ok:{offer_id}:{draft.id}")],
        [Button.inline("Cancelar", f"snd:no:{offer_id}")],
    ]
    if not ctx.config.sending.require_final_confirmation:
        msg = await event.respond(text)
        await _enqueue(ctx, offer_id, draft.id, draft.channel, draft.target or "", dry_run, msg)
        return
    msg = await event.respond(text, buttons=buttons, link_preview=False)
    await ctx.offers.remember_message(msg.id, offer_id, "confirm")


async def _enqueue(
    ctx: AppContext,
    offer_id: int,
    draft_id: int,
    channel: str,
    target: str,
    dry_run: bool,
    message: object,
) -> None:
    msg_id = getattr(message, "id", None)
    app_id = await ctx.applications.create(
        offer_id, draft_id, channel, target, str(ctx.config.cv.path), dry_run, msg_id
    )
    if not dry_run:
        await ctx.offers.set_status(offer_id, "approved")
    ctx.dispatcher.enqueue(app_id)
    await update_cards(ctx, offer_id, "📤 <b>Enviando…</b>", keep_buttons=False)


async def update_cards(ctx: AppContext, offer_id: int, note: str, *, keep_buttons: bool) -> None:
    """Edita en el sitio las tarjetas de la oferta. Sin botones mientras se envía y una vez
    enviada, para que no se pueda pulsar ninguno por error."""
    from jobhunter.telegram.handlers.offers import render_offer  # evita import circular

    rendered = await render_offer(ctx, offer_id, note)
    if rendered is None:
        return
    text, buttons = rendered
    for card_id in await ctx.offers.card_messages(offer_id):
        try:
            await ctx.bot.edit_message(
                ctx.owner_id, card_id, text,
                buttons=(buttons or None) if keep_buttons else None, link_preview=False,
            )  # fmt: skip
        except MessageNotModifiedError:
            pass
        except (ValueError, RPCError):
            continue


async def on_result(ctx: AppContext, app: Application) -> None:
    """Edita la confirmación con el resultado, actualiza la tarjeta y el contador fijado."""
    counter = await ctx.dispatcher.counter()
    sim = " 🧪 (simulación)" if app.dry_run else ""
    buttons = None
    if app.status == "sent":
        what = "mensaje + CV" if app.channel == "telegram_dm" else "email con el CV"
        via = CHANNEL_NAME[app.channel]
        text = f"✅ <b>Enviado</b>{sim} por {via} a {escape(app.target)} ({what})"
        if app.channel == "telegram_dm":
            text += f"\n👤 Usuarios nuevos hoy: {counter.new_today}/{counter.limit}"
        else:
            text += f"\n📧 Emails hoy: {counter.emails_today}/{counter.email_limit}"
        note = f"✅ <b>Enviada</b>{sim} {_local(ctx, app.sent_at)} por {CHANNEL_NAME[app.channel]}"
    else:
        text = f"❌ <b>No se pudo enviar</b>{sim}: {escape(app.error or 'error desconocido')}"
        buttons = [
            [
                Button.inline("🔁 Reintentar", f"snd:retry:{app.offer_id}"),
                Button.inline("📨 Cambiar canal", f"of:c:{app.offer_id}"),
            ]
        ]
        note = "❌ <b>Falló el último envío</b>"
    try:
        if app.bot_message_id:
            await ctx.bot.edit_message(ctx.owner_id, app.bot_message_id, text, buttons=buttons)
        else:
            await ctx.bot.send_message(ctx.owner_id, text, buttons=buttons)
    except (ValueError, RPCError):
        await ctx.bot.send_message(ctx.owner_id, text, buttons=buttons)

    # Tarjeta de la oferta: enviada → sin botones (evita pulsar uno por error);
    # si falló, vuelven los botones para reintentar o cambiar de canal.
    if app.status == "sent" and app.dry_run:
        note += "\n🧪 Para repetir la prueba, ábrela de nuevo desde /ofertas."
    await update_cards(ctx, app.offer_id, note, keep_buttons=app.status != "sent")
    await ctx.status_board.refresh()
    if app.status == "sent" and not app.dry_run and app.channel == "telegram_dm":
        await add_to_folder(ctx, app)


async def add_to_folder(ctx: AppContext, app: Application) -> None:
    """Reclutador → carpeta de Telegram «Aplicadas» (solo organización; nunca bloquea)."""
    if ctx.folder is None or not ctx.folder.enabled or app.contact_id is None:
        return
    contact = await ctx.contacts.by_id(app.contact_id)
    if contact is None:
        return
    _, error = await ctx.folder.add([contact.tg_user_id])
    if error:
        await ctx.bot.send_message(
            ctx.owner_id,
            f"⚠️ El mensaje se envió, pero no pude añadir a {escape(app.target)} a la carpeta "
            f"«{escape(ctx.folder.name)}»: {escape(error)}.",
        )


def register(ctx: AppContext) -> None:
    bot = ctx.bot
    is_owner = owner_message(ctx.owner_id)

    @bot.on(events.CallbackQuery(pattern=rb"^snd:", func=owner_callback(ctx.owner_id)))
    async def on_callback(event: events.CallbackQuery.Event) -> None:
        parts = event.data.decode().split(":")
        action, offer_id = parts[1], int(parts[2])
        if action == "no":
            await event.answer()
            await event.edit("Envío cancelado.")
        elif action == "ok":
            draft_id = int(parts[3])
            latest = await ctx.offers.latest_draft(offer_id)
            if latest is None or latest.id != draft_id:
                await event.answer(
                    "El borrador cambió desde esta vista previa. Pulsa ✅ Enviar otra vez.",
                    alert=True,
                )
                return
            dry_run = effective_dry_run(ctx.settings, ctx.config)
            # Comprobación final justo antes de encolar (límite, duplicado, CV…).
            pre = await ctx.dispatcher.preflight(offer_id, latest, dry_run)
            if not pre.ok:
                await event.answer(pre.reason or "No se puede enviar.", alert=True)
                await event.edit(f"❌ {escape(pre.reason or 'No se puede enviar.')}")
                return
            await event.answer("En cola")
            wait = await ctx.dispatcher.estimated_wait(latest.channel, dry_run)
            msg = await event.get_message()
            await event.edit(
                "📤 Enviando…" if wait <= 0 else f"⏳ En cola: saldrá en ~{int(wait)} s."
            )
            await _enqueue(
                ctx, offer_id, latest.id, latest.channel, latest.target or "", dry_run, msg
            )
        elif action == "retry":
            await start_send(ctx, event, offer_id)
        elif action == "later":
            await ctx.offers.set_status(offer_id, "snoozed")
            await event.answer("Vuelve en el próximo resumen")
            await event.edit("⏰ Pospuesta: vuelve en el próximo resumen.")
        elif action == "mail":
            await _switch_to_email(ctx, event, offer_id)
        else:
            await event.answer()

    @bot.on(events.NewMessage(pattern=command("contador"), func=is_owner))
    async def counter_cmd(event: events.NewMessage.Event) -> None:
        tz = ctx.settings.zoneinfo
        start, end = day_bounds(tz)
        counter = await ctx.dispatcher.counter()
        entries = await ctx.contacts.new_users_list(start, end)
        lines = [
            f"👤 <b>Usuarios nuevos contactados hoy: {counter.new_today}/{counter.limit}</b> "
            f"{counter.indicator}"
        ]
        for n, e in enumerate(entries, start=1):
            title = f" — {escape(e.offer_title)}" if e.offer_title else ""
            lines.append(f"  {n}. {escape(e.who)}{title} — {_local(ctx, e.at)[-5:]}")
        days = []
        for back in range(6, -1, -1):
            d_start = start - timedelta(days=back)
            days.append(
                str(await ctx.contacts.new_users_between(d_start, d_start + timedelta(days=1)))
            )
        lines.append(f"\nÚltimos 7 días: {' · '.join(days)}")
        lines.append(
            f"💬 DMs hoy: {counter.dms_today} · 📧 Emails hoy: "
            f"{counter.emails_today}/{counter.email_limit}"
        )
        if counter.flagged:
            lines.append("⛔ Telegram limitó tu cuenta hoy (PeerFlood): DMs bloqueados.")
        await event.respond("\n".join(lines))
        await ctx.status_board.refresh()

    ctx.menu_actions["contador"] = counter_cmd

    @bot.on(events.NewMessage(pattern=command("aplicadas"), func=is_owner))
    async def applied_cmd(event: events.NewMessage.Event) -> None:
        apps = await ctx.applications.recent(15)
        if not apps:
            await event.respond("Aún no has enviado ninguna aplicación.")
            return
        icon = {"sent": "✅", "failed": "❌", "queued": "⏳", "sending": "📤", "cancelled": "🚫"}
        lines = ["📨 <b>Últimas aplicaciones</b>"]
        for a in apps:
            sim = " 🧪" if a.dry_run else ""
            reply = " · 💬 respondió" if a.replied_at else ""
            lines.append(
                f"{icon.get(a.status, '•')}{sim} {_local(ctx, a.sent_at or a.approved_at)} · "
                f"{CHANNEL_NAME.get(a.channel, a.channel)} → {escape(a.target)}\n"
                f"   {escape(a.offer_title or 'Oferta')}{reply}"
                + (f"\n   <i>{escape(a.error)}</i>" if a.status == "failed" and a.error else "")
            )
        lines.append("\n🧪 = simulación (no llegó al reclutador)")
        await event.respond("\n".join(lines))

    ctx.menu_actions["aplicadas"] = applied_cmd

    @bot.on(events.NewMessage(pattern=command("stats"), func=is_owner))
    async def stats_cmd(event: events.NewMessage.Event) -> None:
        await event.respond(await stats_text(ctx))

    ctx.menu_actions["stats"] = stats_cmd


async def stats_text(ctx: AppContext) -> str:
    since7 = datetime.now(UTC) - timedelta(days=7)
    since30 = datetime.now(UTC) - timedelta(days=30)
    msgs = await ctx.raw_messages.counts_since(since7)
    offers = await ctx.offers.counts_since(since7)
    totals = await ctx.applications.totals()

    def total(channel: str, dry: int) -> int:
        return totals.get(f"{channel}:sent:{dry}", 0)

    lines = [
        "📈 <b>Estadísticas</b>",
        "\n<b>Últimos 7 días</b>",
        f"• Mensajes leídos: {sum(msgs.values())} (filtrados {msgs.get('filtered', 0)}, "
        f"duplicados {msgs.get('duplicate', 0)}, no eran oferta {msgs.get('not_job', 0)})",
        f"• Ofertas analizadas: {sum(offers.values())} · descartadas por reglas "
        f"{offers.get('rejected_rules', 0)} · puntuación baja {offers.get('low_score', 0)}",
        "\n<b>Aplicaciones (total)</b>",
        f"• 💬 DMs: {total('telegram_dm', 0)} · 📧 Emails: {total('email', 0)}"
        f" · 🧪 simulaciones: {total('telegram_dm', 1) + total('email', 1)}",
    ]
    if ctx.llm:
        u = ctx.llm.usage
        lines.append(f"\n🤖 IA desde el arranque: {u.calls} llamadas · {u.tokens:,} tokens")
    reasons = await ctx.offers.dismiss_reasons_since(since30)
    if reasons:
        shown = " · ".join(f"{REASON_LABEL.get(k, k)} {v}" for k, v in reasons.items())
        lines.append(f"\n🗑 Descartes (30 días): {shown}")
        lines += suggestions(reasons, await ctx.prefs.min_salary() is not None)
    return "\n".join(lines)


def suggestions(reasons: dict[str, int], has_min_salary: bool) -> list[str]:
    """Aprender de tus descartes: sugerencias sencillas, nunca cambios automáticos."""
    tips = []
    if reasons.get("salary", 0) >= 3:
        tips.append(
            "💡 Descartas mucho por salario: "
            + ("sube tu mínimo con /salario." if has_min_salary else "fija un mínimo con /salario.")
        )
    if reasons.get("stack", 0) >= 3:
        tips.append(
            "💡 Descartas mucho por stack: sube el umbral con /umbral para ver solo lo que encaja."
        )
    if reasons.get("modality", 0) >= 3:
        tips.append(
            "💡 Descartas mucho por modalidad: revisa si la oferta decía remoto (/descartadas)."
        )
    return tips


async def _switch_to_email(
    ctx: AppContext, event: events.CallbackQuery.Event, offer_id: int
) -> None:
    match = await ctx.offers.match(offer_id)
    option = next((o for o in match.options if o.kind == "email"), None) if match else None
    if match is None or option is None:
        await event.answer("Esta oferta no tiene email.", alert=True)
        return
    if (
        ctx.analyzer is None
        or ctx.profiles is None
        or (profile := await ctx.profiles.active()) is None
    ):
        await event.answer("Falta el CV o la IA.", alert=True)
        return
    await ctx.offers.set_channel(match.id, option, "elegido por ti (límite de DMs)")
    await event.answer("Redactando el email…")
    await event.edit("⏳ Redactando el email…")
    try:
        await ctx.analyzer.new_draft(offer_id, profile, channel=option)
    except LLMError:
        await event.edit("❌ La IA no respondió. Inténtalo en unos minutos.")
        return
    from jobhunter.telegram.handlers.offers import render_offer

    rendered = await render_offer(ctx, offer_id, "📧 Canal cambiado a email")
    if rendered:
        await event.edit(rendered[0], buttons=rendered[1] or None, link_preview=False)
        await ctx.offers.remember_message((await event.get_message()).id, offer_id, "card")
