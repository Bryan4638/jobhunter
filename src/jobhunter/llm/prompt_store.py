"""Prompts inmutables: se cargan una vez al arrancar y no existe forma de modificarlos.

Ver docs/08-seguridad-y-riesgos.md §4 (capa 1). Los placeholders usan ``${nombre}``
(``string.Template``); un ``$`` literal en una plantilla se escribe ``$$``.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from string import Template
from types import MappingProxyType

PROMPTS_DIR = Path(__file__).parent / "prompts"


class PromptStore:
    __slots__ = ("_prompts", "digest")

    def __init__(self, directory: Path = PROMPTS_DIR) -> None:
        texts = {p.stem: p.read_text(encoding="utf-8") for p in sorted(directory.glob("*.md"))}
        object.__setattr__(self, "_prompts", MappingProxyType(texts))
        sha = hashlib.sha256()
        for name, text in sorted(texts.items()):
            sha.update(name.encode() + b"\0" + text.encode() + b"\0")
        object.__setattr__(self, "digest", sha.hexdigest())

    def __setattr__(self, name: str, value: object) -> None:
        raise AttributeError("PromptStore es inmutable")

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(self._prompts)

    def get(self, name: str) -> str:
        return self._prompts[name]

    def render(self, prompt_name: str, /, **values: str) -> str:
        return Template(self._prompts[prompt_name]).substitute(values)
