"""Texto de la oferta tal como lo ve la IA: mensaje + enlaces + página enlazada (si se leyó).

Todo se sanea con ``sanitize`` y va dentro del mismo bloque delimitado con nonce: la página
es tan poco confiable como el mensaje (docs/08 §4).
"""

from __future__ import annotations

from jobhunter.llm.guard import sanitize

PAGE_HEADER = "Contenido de la página enlazada de la oferta"


def offer_input(
    text: str,
    hidden_urls: tuple[str, ...],
    max_chars: int,
    page_url: str | None = None,
    page_text: str | None = None,
    max_page_chars: int = 6000,
) -> str:
    if hidden_urls:
        text += "\n\nEnlaces del mensaje:\n" + "\n".join(hidden_urls)
    out = sanitize(text, max_chars)
    if page_text:
        out += f"\n\n--- {PAGE_HEADER} ({sanitize(page_url or '', 300)}) ---\n" + sanitize(
            page_text, max_page_chars
        )
    return out


def full_source(text: str, page_text: str | None) -> str:
    """Texto contra el que se validan contactos y se buscan intentos de manipulación."""
    return f"{text}\n{page_text}" if page_text else text
