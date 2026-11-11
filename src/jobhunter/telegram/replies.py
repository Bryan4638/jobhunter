"""Fase 4: avisarte cuando un reclutador al que escribiste por DM te responde.

El bot **nunca responde automáticamente**: solo te avisa (una vez por aplicación).
"""

from __future__ import annotations

import logging
from html import escape

from telethon import TelegramClient, events, utils
from telethon.errors import RPCError

from jobhunter.storage.sending import ApplicationRepo

log = logging.getLogger(__name__)

SNIPPET = 300


def register_reply_listener(
    userbot: TelegramClient, bot: TelegramClient, owner_id: int, applications: ApplicationRepo
) -> None:
    @userbot.on(events.NewMessage(incoming=True, func=lambda e: e.is_private))
    async def on_private(event: events.NewMessage.Event) -> None:
        if not event.sender_id or event.sender_id == owner_id:
            return
        try:
            pending = await applications.awaiting_reply(event.sender_id)
            if not pending:
                return
            for app in pending:
                await applications.update(app.id, replied_at=event.date.isoformat())
            sender = await event.get_sender()
            who = (
                f"@{sender.username}"
                if getattr(sender, "username", None)
                else (utils.get_display_name(sender) if sender else "El reclutador")
            )
            titles = ", ".join(f"«{a.offer_title or 'oferta'}»" for a in pending)
            text = event.message.message or "(mensaje sin texto)"
            if len(text) > SNIPPET:
                text = text[:SNIPPET] + "…"
            await bot.send_message(
                owner_id,
                f"💬 <b>{escape(who)} te respondió</b> sobre {escape(titles)}:\n\n"
                f"<i>{escape(text)}</i>\n\nContéstale desde tu Telegram; yo no respondo por ti.",
            )
        except (ValueError, RPCError):
            log.exception("No pude avisarte de la respuesta de %s", event.sender_id)
