"""Cliente Telethon con tu cuenta personal (lee grupos, enviará DMs en la fase 3)."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path

from telethon import TelegramClient, utils
from telethon.errors import RPCError
from telethon.tl.types import Channel, Chat

from jobhunter import __version__
from jobhunter.config import Settings
from jobhunter.telegram.chat_ref import parse_chat_ref

log = logging.getLogger(__name__)


def session_file(name: Path) -> Path:
    return Path(f"{name}.session")


def secure_session(name: Path) -> None:
    """El .session equivale a tu cuenta iniciada: permisos 600."""
    path = session_file(name)
    if path.exists():
        os.chmod(path, 0o600)


def create_userbot(settings: Settings) -> TelegramClient:
    settings.tg_session_name.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    return TelegramClient(
        str(settings.tg_session_name),
        settings.tg_api_id,
        settings.tg_api_hash.get_secret_value(),
        device_model="JobHunter",
        app_version=__version__,
    )


@dataclass(frozen=True, slots=True)
class ResolvedChat:
    id: int
    title: str
    username: str | None
    kind: str  # "group" | "channel"


class ChatResolveError(ValueError):
    pass


def to_resolved(entity: object) -> ResolvedChat:
    if isinstance(entity, Channel):
        if entity.left:
            raise ChatResolveError(
                f"Tu cuenta no es miembro de «{entity.title}». Únete primero desde Telegram."
            )
        kind = "channel" if entity.broadcast else "group"
        return ResolvedChat(utils.get_peer_id(entity), entity.title, entity.username, kind)
    if isinstance(entity, Chat):
        if entity.left or entity.deactivated:
            raise ChatResolveError(
                f"«{entity.title}» no está disponible (has salido o se migró a supergrupo)."
            )
        return ResolvedChat(utils.get_peer_id(entity), entity.title, None, "group")
    raise ChatResolveError("Eso es un usuario o bot, no un grupo o canal.")


async def resolve_chat(client: TelegramClient, ref: str | int) -> ResolvedChat:
    """Resuelve una referencia (texto del usuario o peer id) a un grupo/canal."""
    target = parse_chat_ref(ref) if isinstance(ref, str) else ref
    try:
        entity = await client.get_entity(target)
    except ValueError:
        if isinstance(target, int):
            # Puede que no esté en la caché de entidades: refrescar diálogos y reintentar.
            await client.get_dialogs()
            try:
                entity = await client.get_entity(target)
            except ValueError as exc:
                raise ChatResolveError(
                    "No encuentro ese chat entre los de tu cuenta. ¿Eres miembro?"
                ) from exc
        else:
            raise ChatResolveError(f"No existe @{target} o no es accesible.") from None
    except RPCError as exc:
        raise ChatResolveError(f"Telegram rechazó la consulta: {exc.__class__.__name__}") from exc
    return to_resolved(entity)


async def list_candidate_chats(client: TelegramClient) -> list[ResolvedChat]:
    """Grupos y canales de los que tu cuenta es miembro."""
    result: list[ResolvedChat] = []
    async for dialog in client.iter_dialogs():
        if not (dialog.is_group or dialog.is_channel):
            continue
        try:
            result.append(to_resolved(dialog.entity))
        except ChatResolveError:
            continue
    result.sort(key=lambda c: c.title.casefold())
    return result
