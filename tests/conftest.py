from collections.abc import AsyncIterator
from pathlib import Path

import pytest

from jobhunter.storage.db import Database


@pytest.fixture
async def db(tmp_path: Path) -> AsyncIterator[Database]:
    database = Database(tmp_path / "test.db")
    await database.connect()
    yield database
    await database.close()


def make_pdf(lines: list[str], uri: str | None = None) -> bytes:
    """PDF mínimo de una página con texto (ASCII) y, opcionalmente, un enlace."""

    def esc(s: str) -> str:
        return s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")

    content = "BT /F1 10 Tf 50 750 Td 12 TL " + " ".join(f"({esc(x)}) Tj T*" for x in lines)
    content += " ET"
    annots = " /Annots [6 0 R]" if uri else ""
    objects = [
        "<< /Type /Catalog /Pages 2 0 R >>",
        "<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        "<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R"
        f" /Resources << /Font << /F1 5 0 R >> >>{annots} >>",
        f"<< /Length {len(content)} >>\nstream\n{content}\nendstream",
        "<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    if uri:
        objects.append(
            f"<< /Type /Annot /Subtype /Link /Rect [0 0 100 100] /A << /S /URI /URI ({uri}) >> >>"
        )
    out = b"%PDF-1.4\n"
    offsets = []
    for i, obj in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{i} 0 obj\n{obj}\nendobj\n".encode("latin-1")
    xref = len(out)
    out += f"xref\n0 {len(objects) + 1}\n0000000000 65535 f \n".encode()
    for off in offsets:
        out += f"{off:010d} 00000 n \n".encode()
    out += (
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode()
    )
    return out
