from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True, slots=True)
class IncomingMessage:
    """Mensaje de un grupo, ya independiente de Telethon."""

    chat_id: int
    chat_username: str | None
    message_id: int
    sender_id: int | None
    sender_username: str | None
    sender_name: str | None
    sender_is_user: bool
    text: str
    hidden_urls: tuple[str, ...]
    posted_at: datetime


def build_message_link(chat_id: int, chat_username: str | None, message_id: int) -> str | None:
    if chat_username:
        return f"https://t.me/{chat_username}/{message_id}"
    raw = str(chat_id)
    if raw.startswith("-100"):
        return f"https://t.me/c/{raw[4:]}/{message_id}"
    return None  # grupos básicos: Telegram no ofrece enlace a mensajes
