"""Carpeta de Telegram con los reclutadores a los que has escrito (docs/06).

Tras cada DM real (no en simulación) el usuario se añade a la carpeta ``folder_name`` de tu
cuenta; si no existe, se crea. Al arrancar se sincroniza con todos los contactos a los que ya
se escribió. Es solo organización: un fallo aquí nunca afecta al envío.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from telethon import TelegramClient
from telethon.errors import RPCError
from telethon.tl.functions.messages import GetDialogFiltersRequest, UpdateDialogFilterRequest
from telethon.tl.types import DialogFilter, InputPeerUser, TextWithEntities

log = logging.getLogger(__name__)

# Telegram: ids 0 y 1 reservados; 100 chats incluidos por carpeta sin Premium (200 con Premium).
FIRST_FOLDER_ID = 2
MAX_FOLDER_ID = 255


def title_of(f: Any) -> str:
    title = getattr(f, "title", "")
    return getattr(title, "text", title) or ""


def included_user_ids(f: DialogFilter) -> set[int]:
    return {p.user_id for p in f.include_peers if isinstance(p, InputPeerUser)}


def next_folder_id(filters: list[Any]) -> int:
    used = {f.id for f in filters if hasattr(f, "id")}
    for candidate in range(FIRST_FOLDER_ID, MAX_FOLDER_ID + 1):
        if candidate not in used:
            return candidate
    raise RuntimeError("No quedan ids de carpeta libres")


def folder_error(exc: RPCError) -> str:
    text = f"{getattr(exc, 'message', '')} {exc}"
    if "INCLUDE_TOO_MUCH" in text:
        return "la carpeta ya tiene el máximo de chats que permite Telegram (100 sin Premium)"
    if "DIALOG_FILTERS_TOO_MUCH" in text or "FILTERS_TOO_MUCH" in text:
        return "tienes el máximo de carpetas que permite Telegram; borra alguna"
    return text


class ApplicationsFolder:
    def __init__(self, userbot: TelegramClient, name: str) -> None:
        self._userbot = userbot
        self.name = name.strip()
        self._lock = asyncio.Lock()

    @property
    def enabled(self) -> bool:
        return bool(self.name)

    async def _filters(self) -> list[Any]:
        result = await self._userbot(GetDialogFiltersRequest())
        return list(getattr(result, "filters", result))

    async def add(self, user_ids: list[int]) -> tuple[int, str | None]:
        """Añade los usuarios que falten. Devuelve (añadidos, error legible o None)."""
        if not self.enabled or not user_ids:
            return 0, None
        async with self._lock:
            try:
                filters = await self._filters()
                folder = next(
                    (
                        f
                        for f in filters
                        if isinstance(f, DialogFilter) and title_of(f) == self.name
                    ),
                    None,
                )
                present = included_user_ids(folder) if folder else set()
                peers = []
                for uid in dict.fromkeys(user_ids):
                    if uid in present:
                        continue
                    try:
                        peer = await self._userbot.get_input_entity(uid)
                    except (ValueError, RPCError):
                        log.info("No pude resolver al usuario %s para la carpeta", uid)
                        continue
                    if isinstance(peer, InputPeerUser):
                        peers.append(peer)
                if not peers:
                    return 0, None
                if folder is None:
                    folder = DialogFilter(
                        id=next_folder_id(filters),
                        title=TextWithEntities(text=self.name, entities=[]),
                        pinned_peers=[],
                        include_peers=peers,
                        exclude_peers=[],
                    )
                    log.info("Creo la carpeta de Telegram «%s»", self.name)
                else:
                    folder.include_peers = [*folder.include_peers, *peers]
                await self._userbot(UpdateDialogFilterRequest(id=folder.id, filter=folder))
                return len(peers), None
            except RPCError as exc:
                log.warning("No pude actualizar la carpeta «%s»: %s", self.name, exc)
                return 0, folder_error(exc)
            except RuntimeError as exc:
                return 0, str(exc)
