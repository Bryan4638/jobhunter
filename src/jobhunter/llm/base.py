"""Interfaz de proveedor de IA. La lógica de negocio solo conoce esto.

Garantías de aislamiento que toda implementación debe cumplir (docs/08 §4):
- ``system`` va por el canal de instrucciones de sistema del proveedor y solo contiene
  plantillas de ``PromptStore``; el contenido no confiable va únicamente en ``prompt``.
- Cada llamada es independiente: sin historial ni sesiones de chat.
- Sin herramientas / function calling.
- La salida JSON se valida con el esquema Pydantic y nunca se interpreta como instrucción.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, TypeVar

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


class LLMError(Exception):
    """La IA no respondió o no devolvió un resultado válido tras los reintentos."""


class QuotaExhaustedError(LLMError):
    """Todos los modelos tienen la cuota diaria agotada: hay que esperar, no reintentar."""


@dataclass(frozen=True, slots=True)
class LLMResult[M]:
    value: M
    model: str


class LLMProvider(Protocol):
    async def generate_json(
        self,
        *,
        system: str,
        prompt: str,
        schema: type[T],
        model: str | None = None,
        temperature: float | None = None,
    ) -> LLMResult[T]: ...

    async def generate_text(
        self,
        *,
        system: str,
        prompt: str,
        model: str | None = None,
        temperature: float | None = None,
    ) -> LLMResult[str]: ...
