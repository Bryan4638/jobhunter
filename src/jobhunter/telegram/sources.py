"""Chats monitorizados: registro en memoria, listener de mensajes nuevos y catch-up."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from telethon import TelegramClient, events, utils
from telethon.errors import RPCError
from telethon.tl.custom import Message
from telethon.tl.types import (
    InlineButtonTypeUrl,
    InlineButtonTypeUrlAuth,
    MessageEntityTextUrl,
    ReplyInlineMarkup,
    User,
)

from jobhunter.ingestion.ingestor import Ingestor
from jobhunter.ingestion.models import IncomingMessage
from jobhunter.storage.repositories import SourceChat, SourceChatRepo

log = logging.getLogger(__name__)


class SourceRegistry:
    """Chats activos. Se refresca cada vez que el propietario cambia /grupos."""

    def __init__(self, repo: SourceChatRepo) -> None:
        self._repo = repo
        self._chats: dict[int, SourceChat] = {}

    async def refresh(self) -> None:
        self._chats = {c.id: c for c in await self._repo.list() if c.enabled}
        log.info("Monitorizando %d chats", len(self._chats))

    def get(self, chat_id: int) -> SourceChat | None:
        return self._chats.get(chat_id)

    def chats(self) -> list[SourceChat]:
        return list(self._chats.values())

    def __contains__(self, chat_id: object) -> bool:
        return chat_id in self._chats

    def __len__(self) -> int:
        return len(self._chats)


@dataclass
class RuntimeState:
    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    messages_seen: int = 0
    last_message_at: datetime | None = None
    caught_up: int = 0
    me_display: str = ""
    bot_username: str = ""


def button_urls(message: Message) -> list[str]:
    """Enlaces de los botones bajo el mensaje (p. ej. «Ir a la oferta 🔗»)."""
    markup = getattr(message, "reply_markup", None)
    if not isinstance(markup, ReplyInlineMarkup):
        return []
    urls = []
    for row in markup.rows:
        for button in getattr(row, "buttons", []):
            kind = getattr(button, "type", None)
            if isinstance(kind, InlineButtonTypeUrl | InlineButtonTypeUrlAuth):
                urls.append(kind.url)
    return urls


async def to_incoming(message: Message, chat: SourceChat) -> IncomingMessage:
    sender = await message.get_sender()
    hidden = tuple(
        dict.fromkeys(
            [
                *(e.url for e in (message.entities or []) if isinstance(e, MessageEntityTextUrl)),
                *button_urls(message),
            ]
        )
    )
    return IncomingMessage(
        chat_id=chat.id,
        chat_username=chat.username,
        message_id=message.id,
        sender_id=getattr(sender, "id", None) or message.sender_id,
        sender_username=getattr(sender, "username", None),
        sender_name=utils.get_display_name(sender) if sender else None,
        # Canales, administradores anónimos y bots no reciben DMs como "autor del post".
        sender_is_user=isinstance(sender, User) and not sender.bot,
        text=message.message or "",
        hidden_urls=hidden,
        posted_at=message.date or datetime.now(UTC),
    )


def register_listener(
    userbot: TelegramClient,
    registry: SourceRegistry,
    ingestor: Ingestor,
    state: RuntimeState,
) -> None:
    @userbot.on(events.NewMessage(func=lambda e: e.chat_id in registry))
    async def on_new_message(event: events.NewMessage.Event) -> None:
        chat = registry.get(event.chat_id)
        if chat is None:
            return
        state.messages_seen += 1
        state.last_message_at = datetime.now(UTC)
        try:
            await ingestor.ingest(await to_incoming(event.message, chat))
        except Exception:
            log.exception("Error guardando el mensaje %s del chat %s", event.id, event.chat_id)


async def catch_up(
    userbot: TelegramClient,
    registry: SourceRegistry,
    sources: SourceChatRepo,
    ingestor: Ingestor,
    limit: int,
) -> int:
    """Recupera los mensajes publicados mientras el bot estaba apagado."""
    total = 0
    for chat in registry.chats():
        if limit <= 0:
            break
        if chat.last_message_id is None:
            # Chat sin cursor (añadido antes de la fase 1): fijarlo en su último mensaje.
            try:
                latest = await userbot.get_messages(chat.id, limit=1)
            except RPCError as exc:
                log.warning("No pude leer el último mensaje de %s: %s", chat.title, exc)
                continue
            if latest:
                await sources.touch(chat.id, latest[0].id, latest[0].date)
            continue
        try:
            # Los `limit` más recientes posteriores al último visto, procesados en orden.
            messages = [
                m
                async for m in userbot.iter_messages(
                    chat.id, min_id=chat.last_message_id, limit=limit
                )
            ]
        except RPCError as exc:
            log.warning("Catch-up de %s falló: %s", chat.title, exc)
            continue
        for message in reversed(messages):
            if message.action is not None:  # mensajes de servicio (alguien se unió, etc.)
                continue
            try:
                if await ingestor.ingest(await to_incoming(message, chat)):
                    total += 1
            except Exception:
                log.exception("Error en catch-up del mensaje %s de %s", message.id, chat.title)
    log.info("Catch-up: %d mensajes recuperados", total)
    return total


@dataclass(frozen=True, slots=True)
class ScanReport:
    read: int
    new: int  # pendientes de analizar
    filtered: int
    duplicate: int
    already: int  # ya estaban guardados


async def scan_history(
    userbot: TelegramClient,
    registry: SourceRegistry,
    ingestor: Ingestor,
    days: int,
    per_chat_limit: int = 300,
) -> ScanReport:
    """Recorre los últimos ``days`` días de cada chat y los pasa por la ingesta.

    Telethon espera solo ante FloodWait cortos; los mensajes ya guardados se saltan.
    """
    since = datetime.now(UTC) - timedelta(days=days)
    read = new = filtered = duplicate = already = 0
    for chat in registry.chats():
        try:
            messages = []
            async for m in userbot.iter_messages(chat.id, limit=per_chat_limit):
                if m.date and m.date < since:
                    break
                messages.append(m)
        except RPCError as exc:
            log.warning("Escaneo de %s falló: %s", chat.title, exc)
            continue
        for message in reversed(messages):
            if message.action is not None:
                continue
            read += 1
            try:
                result = await ingestor.ingest(await to_incoming(message, chat))
            except Exception:
                log.exception("Error escaneando el mensaje %s de %s", message.id, chat.title)
                continue
            if result is None:
                already += 1
            elif result.status == "new":
                new += 1
            elif result.status == "filtered":
                filtered += 1
            else:
                duplicate += 1
        await asyncio.sleep(1)  # pausa entre chats
    log.info("Escaneo de %d días: %d leídos, %d nuevos", days, read, new)
    return ScanReport(read, new, filtered, duplicate, already)
