"""Mensaje de estado fijado en tu chat con el contador de usuarios nuevos (docs/06, ADR-009).

Se edita en el sitio al arrancar, tras cada envío y a medianoche. Si lo borras, se crea
y se fija de nuevo.
"""

from __future__ import annotations

import logging

from telethon import TelegramClient
from telethon.errors import MessageIdInvalidError, MessageNotModifiedError, RPCError

from jobhunter.sending.dispatcher import Counter, Dispatcher, day_label
from jobhunter.storage.repositories import SettingsRepo

log = logging.getLogger(__name__)

MESSAGE_KEY = "status_message.bot_message_id"


def counter_text(counter: Counter, day: str, dry_run: bool) -> str:
    lines = [
        f"📊 <b>Hoy · {day}</b>",
        f"👤 Usuarios nuevos: <b>{counter.new_today}/{counter.limit}</b>   {counter.indicator}",
        f"💬 DMs: {counter.dms_today}   ·   "
        f"📧 Emails: {counter.emails_today}/{counter.email_limit}",
    ]
    if counter.flagged:
        lines.append("⛔ Telegram limitó tu cuenta hoy (PeerFlood): DMs bloqueados")
    if dry_run:
        lines.append("🧪 Modo simulación: no se envía nada a reclutadores")
    lines.append("🔄 Se reinicia a las 00:00")
    return "\n".join(lines)


class StatusBoard:
    def __init__(
        self,
        bot: TelegramClient,
        owner_id: int,
        dispatcher: Dispatcher,
        settings: SettingsRepo,
        dry_run: bool,
        pin: bool,
    ) -> None:
        self._bot = bot
        self._owner = owner_id
        self._dispatcher = dispatcher
        self._settings = settings
        self._dry_run = dry_run
        self._pin = pin

    async def text(self) -> str:
        counter = await self._dispatcher.counter()
        return counter_text(counter, day_label(self._dispatcher.tz), self._dry_run)

    async def refresh(self) -> None:
        text = await self.text()
        msg_id = await self._settings.get(MESSAGE_KEY)
        if isinstance(msg_id, int):
            try:
                await self._bot.edit_message(self._owner, msg_id, text)
                return
            except MessageNotModifiedError:
                return
            except (MessageIdInvalidError, ValueError):
                log.info("El mensaje de estado ya no existe; creo uno nuevo")
            except RPCError as exc:
                log.warning("No pude actualizar el mensaje de estado: %s", exc)
                return
        try:
            msg = await self._bot.send_message(self._owner, text)
            await self._settings.set(MESSAGE_KEY, msg.id)
            if self._pin:
                await self._bot.pin_message(self._owner, msg, notify=False)
        except (ValueError, RPCError) as exc:
            log.warning("No pude crear o fijar el mensaje de estado: %s", exc)
