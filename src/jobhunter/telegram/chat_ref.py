"""Interpretación de referencias a grupos/canales escritas por el usuario."""

from __future__ import annotations

import re

_USERNAME = r"[A-Za-z][A-Za-z0-9_]{3,31}"
_LINK = re.compile(r"^(?:https?://)?(?:www\.)?(?:t\.me|telegram\.me)/(?P<path>.+)$", re.I)


class InviteLinkError(ValueError):
    """Enlace de invitación privado: no se puede resolver sin unirse."""


def parse_chat_ref(ref: str) -> str | int:
    """Convierte ``@grupo``, ``t.me/grupo``, ``t.me/c/123/45`` o un ID en algo resoluble.

    Devuelve un username (``str`` sin @) o un peer id (``int``).
    """
    ref = ref.strip()
    if not ref:
        raise ValueError("Referencia vacía")

    if re.fullmatch(r"-?\d+", ref):
        return int(ref)

    if ref.startswith("@"):
        name = ref[1:]
        if re.fullmatch(_USERNAME, name):
            return name
        raise ValueError(f"Usuario no válido: {ref}")

    match = _LINK.match(ref)
    if match:
        parts = [p for p in match["path"].split("?")[0].split("/") if p]
        if not parts:
            raise ValueError("Enlace incompleto")
        if parts[0].startswith("+") or parts[0] == "joinchat":
            raise InviteLinkError(
                "Es un enlace de invitación privado. Reenvíame un mensaje del grupo o "
                "usa /grupos → «Añadir desde mis chats»."
            )
        if parts[0] == "c" and len(parts) >= 2 and parts[1].isdigit():
            return int(f"-100{parts[1]}")  # enlace a mensaje de grupo privado
        if parts[0] == "s" and len(parts) >= 2:
            parts = parts[1:]  # vista previa web: t.me/s/canal
        if re.fullmatch(_USERNAME, parts[0]):
            return parts[0]
        raise ValueError(f"Enlace no válido: {ref}")

    if re.fullmatch(_USERNAME, ref):
        return ref
    raise ValueError(f"No reconozco {ref!r}. Usa @usuario, un enlace t.me/... o un ID.")
