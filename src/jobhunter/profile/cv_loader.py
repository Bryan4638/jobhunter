"""PDF del CV → texto + enlaces (incluidos los hipervínculos ocultos del PDF)."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path

from pypdf import PdfReader
from pypdf.errors import PdfReadError

MAX_PAGES = 10
MIN_CHARS = 200


class CVError(Exception):
    """El PDF no se puede usar como CV (mensaje legible para el usuario)."""


@dataclass(frozen=True, slots=True)
class CVText:
    text: str
    links: tuple[str, ...]
    pages: int
    sha256: str

    def for_llm(self, max_chars: int) -> str:
        text = self.text[:max_chars]
        if self.links:
            text += "\n\nEnlaces del PDF:\n" + "\n".join(self.links)
        return text


def _pdf_links(reader: PdfReader) -> list[str]:
    links: list[str] = []
    for page in reader.pages:
        for annot in page.get("/Annots") or []:
            obj = annot.get_object()
            action = obj.get("/A")
            uri = action.get_object().get("/URI") if action else None
            if isinstance(uri, (str, bytes)):
                value = uri.decode(errors="ignore") if isinstance(uri, bytes) else str(uri)
                if value not in links:
                    links.append(value)
    return links


def _clean(text: str) -> str:
    text = text.replace("\x00", "")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n\s*\n\s*\n+", "\n\n", text)
    return text.strip()


def extract_cv(path: Path) -> CVText:
    """Síncrono (usar con ``asyncio.to_thread``)."""
    data = path.read_bytes()
    try:
        reader = PdfReader(path)
        if reader.is_encrypted and not reader.decrypt(""):
            raise CVError("El PDF está protegido con contraseña.")
        if len(reader.pages) > MAX_PAGES:
            raise CVError(f"El PDF tiene {len(reader.pages)} páginas; un CV debería tener ≤ 10.")
        text = _clean("\n".join(page.extract_text() or "" for page in reader.pages))
        links = _pdf_links(reader)
        pages = len(reader.pages)
    except PdfReadError as exc:
        raise CVError(f"No pude leer el PDF: {exc}") from exc

    if len(text) < MIN_CHARS:
        raise CVError(
            "Casi no pude extraer texto del PDF (¿es un escaneo o una imagen?). "
            "Exporta el CV desde Word/Google Docs como PDF con texto seleccionable."
        )
    return CVText(text, tuple(links), pages, hashlib.sha256(data).hexdigest())
