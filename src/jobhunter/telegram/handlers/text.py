"""Texto libre del propietario: se interpreta solo si el bot está esperando algo."""

from __future__ import annotations

from telethon import events

from jobhunter.context import AppContext
from jobhunter.telegram.auth import command, owner_message
from jobhunter.telegram.handlers.cv import handle_cv_correction
from jobhunter.telegram.handlers.offers import handle_draft_edit


def register(ctx: AppContext) -> None:
    bot = ctx.bot
    is_owner = owner_message(ctx.owner_id)

    def is_free_text(e: events.NewMessage.Event) -> bool:
        m = e.message
        return (
            is_owner(e)
            and bool(m.message)
            and not m.message.startswith("/")
            and m.fwd_from is None
            and m.media is None
        )

    @bot.on(events.NewMessage(func=is_free_text))
    async def free_text(event: events.NewMessage.Event) -> None:
        pending = ctx.conversation.pending
        if pending is None:
            await event.respond("No esperaba ningún texto ahora. Usa /menu para ver las opciones.")
            return
        if pending.kind == "cv_fix":
            await handle_cv_correction(ctx, event, pending.ref, event.message.message)
        elif pending.kind == "draft_edit":
            await handle_draft_edit(ctx, event, pending.ref)

    @bot.on(events.NewMessage(pattern=command("cancelar"), func=is_owner))
    async def cancel(event: events.NewMessage.Event) -> None:
        had = ctx.conversation.clear()
        await event.respond("Cancelado." if had else "No había nada pendiente.")
