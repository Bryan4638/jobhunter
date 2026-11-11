from __future__ import annotations

import hashlib
import re
import unicodedata

_NON_WORD = re.compile(r"[^a-z0-9@.]+")


def fold(text: str) -> str:
    """Minúsculas y sin tildes."""
    decomposed = unicodedata.normalize("NFKD", text.casefold())
    return "".join(c for c in decomposed if not unicodedata.combining(c))


def normalize(text: str) -> str:
    """Forma canónica para comparar mensajes: sin tildes, emojis, formato ni espacios extra."""
    return _NON_WORD.sub(" ", fold(text)).strip()


def text_hash(normalized: str) -> str:
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()
