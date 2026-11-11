"""DM + CV desde tu cuenta (userbot), con manejo de los límites de Telegram."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from telethon import TelegramClient, utils
from telethon.errors import (
    FloodWaitError,
    InputUserDeactivatedError,
    PeerFloodError,
    RPCError,
    UserIsBlockedError,
    UsernameInvalidError,
    UsernameNotOccupiedError,
    UserPrivacyRestrictedError,
    YouBlockedUserError,
)
from telethon.tl.types import DocumentAttributeFilename, User

log = logging.getLogger(__name__)


class RecipientError(Exception):
    """El destinatario no existe o no se le puede escribir."""


class SendError(Exception):
    def __init__(self, message: str, *, flagged: bool = False) -> None:
        super().__init__(message)
        self.flagged = flagged  # PeerFlood: Telegram limitó tu cuenta para escribir a desconocidos


@dataclass(frozen=True, slots=True)
class Recipient:
    user_id: int
    username: str | None
    display: str
    is_self: bool
    entity: Any

    @property
    def label(self) -> str:
        return f"@{self.username}" if self.username else self.display


async def resolve_recipient(userbot: TelegramClient, target: str) -> Recipient:
    """``@usuario`` o ``id:<user_id>`` (autor del post) → usuario de Telegram."""
    try:
        if target.startswith("@"):
            entity = await userbot.get_entity(target[1:])
        elif target.startswith("id:"):
            entity = await userbot.get_entity(int(target[3:]))
        else:
            raise RecipientError(f"Destinatario no válido: {target}")
    except (UsernameNotOccupiedError, UsernameInvalidError):
        raise RecipientError(f"El usuario {target} no existe.") from None
    except ValueError:
        raise RecipientError(
            f"No puedo abrir un chat con {target}: Telegram no me da acceso a ese usuario "
            "(no tiene @usuario público o no lo he visto en tus chats)."
        ) from None
    except RPCError as exc:
        raise RecipientError(f"No pude comprobar {target}: {exc}") from exc
    if not isinstance(entity, User):
        raise RecipientError(f"{target} no es una persona (es un grupo o un canal).")
    if entity.bot:
        raise RecipientError(f"{target} es un bot: aplica a mano.")
    if entity.deleted:
        raise RecipientError(f"La cuenta de {target} está eliminada.")
    return Recipient(
        entity.id, entity.username, utils.get_display_name(entity), bool(entity.is_self), entity
    )


async def has_prior_chat(userbot: TelegramClient, recipient: Recipient) -> bool:
    """¿Ya existe un chat privado con mensajes? (Contigo mismo siempre cuenta como conocido.)"""
    if recipient.is_self:
        return True
    messages = await userbot.get_messages(recipient.entity, limit=1)
    return bool(messages)


async def send_dm(
    userbot: TelegramClient,
    recipient: Any,
    text: str,
    cv_path: Path | None,
    filename: str,
    max_flood_wait: int,
) -> int:
    """Envía el texto y, si se indica, el CV como documento. Devuelve el id del mensaje."""
    for attempt in range(2):
        try:
            msg = await userbot.send_message(recipient, text, link_preview=False)
            if cv_path is not None:
                await userbot.send_file(
                    recipient,
                    cv_path,
                    force_document=True,
                    attributes=[DocumentAttributeFilename(filename)],
                )
            return int(msg.id)
        except FloodWaitError as exc:
            if attempt == 0 and exc.seconds <= max_flood_wait:
                log.warning("FloodWait de %ss al enviar un DM; espero", exc.seconds)
                await asyncio.sleep(exc.seconds + 1)
                continue
            raise SendError(
                f"Telegram pide esperar {exc.seconds} s antes de enviar más mensajes."
            ) from exc
        except PeerFloodError as exc:
            raise SendError(
                "Telegram ha limitado tu cuenta para escribir a personas que no te tienen "
                "como contacto (PeerFlood). No envíes más DMs hoy; usa el email.",
                flagged=True,
            ) from exc
        except UserPrivacyRestrictedError as exc:
            raise SendError("La privacidad de ese usuario no permite que le escribas.") from exc
        except (UserIsBlockedError, YouBlockedUserError) as exc:
            raise SendError("Ese usuario y tú tenéis un bloqueo activo.") from exc
        except InputUserDeactivatedError as exc:
            raise SendError("La cuenta de ese usuario está desactivada.") from exc
        except RPCError as exc:
            raise SendError(f"Telegram rechazó el envío: {exc}") from exc
    raise SendError("No se pudo enviar tras esperar el FloodWait.")
