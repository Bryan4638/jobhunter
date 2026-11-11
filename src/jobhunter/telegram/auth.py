"""Filtros: el bot solo atiende a OWNER_USER_ID, en chat privado."""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any


def owner_message(owner_id: int) -> Callable[[Any], bool]:
    return lambda e: bool(e.is_private) and e.sender_id == owner_id


def owner_callback(owner_id: int) -> Callable[[Any], bool]:
    return lambda e: e.sender_id == owner_id


def command(name: str) -> re.Pattern[str]:
    """``/name``, ``/name@MiBot`` y ``/name argumentos`` (en el grupo ``args``)."""
    return re.compile(rf"^/{name}(?:@\w+)?(?:\s+(?P<args>.+))?\s*$", re.IGNORECASE | re.DOTALL)


def args_of(event: Any) -> str:
    """Argumentos de un comando escrito; vacío si viene de un botón del /menu."""
    match = getattr(event, "pattern_match", None)
    if not isinstance(match, re.Match) or not isinstance(match.re.pattern, str):
        return ""
    return (match.groupdict().get("args") or "").strip()
