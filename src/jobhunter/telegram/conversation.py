"""Estado de conversación: qué texto libre espera el bot del propietario."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

PendingKind = Literal["cv_fix", "draft_edit"]


@dataclass(frozen=True, slots=True)
class PendingInput:
    kind: PendingKind
    ref: int  # id del perfil a corregir o de la oferta cuyo borrador se edita


class Conversation:
    def __init__(self) -> None:
        self.pending: PendingInput | None = None

    def expect(self, kind: PendingKind, ref: int) -> None:
        self.pending = PendingInput(kind, ref)

    def clear(self) -> PendingInput | None:
        pending, self.pending = self.pending, None
        return pending
