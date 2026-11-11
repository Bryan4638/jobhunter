"""Prefiltro barato: descarta lo que claramente no es una oferta, sin gastar IA.

Ante la duda deja pasar el mensaje: la clasificación fina la hace la IA (fase 2).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache

from jobhunter.config import AppConfig
from jobhunter.ingestion.text import fold, normalize

_ES_WORDS = frozenset(
    "de la que el en y los del se las por un para con una su al es lo como mas pero sus le ya "
    "este si porque esta entre cuando muy sin sobre tambien me hasta hay donde desde todo nos "
    "buscamos experiencia anos conocimientos trabajo empresa requisitos ofrecemos salario "
    "desarrollador equipo somos tiempo completo remoto".split()
)
_EN_WORDS = frozenset(
    "the and of to in is for with you we are our on as be will this that or an at your have "
    "from experience years looking team work skills requirements about who us join "
    "developer salary full time remote position role".split()
)


@dataclass(frozen=True, slots=True)
class PrefilterResult:
    passed: bool
    reason: str | None = None


@lru_cache(maxsize=8)
def _keyword_patterns(keywords: tuple[str, ...]) -> tuple[re.Pattern[str], tuple[str, ...]]:
    """Palabras → regex por prefijo sobre texto normalizado; símbolos → búsqueda literal."""
    words = [normalize(k) for k in keywords if normalize(k)]
    symbols = tuple(k for k in keywords if not normalize(k))
    pattern = re.compile(r"\b(?:" + "|".join(re.escape(w) for w in words) + r")")
    return pattern, symbols


def detect_language(normalized: str) -> str | None:
    """Heurística mínima es/en por palabras frecuentes. None si no hay señal clara."""
    tokens = normalized.split()
    es = sum(t in _ES_WORDS for t in tokens)
    en = sum(t in _EN_WORDS for t in tokens)
    if en >= 8 and en > 3 * es:
        return "en"
    if es >= 3 and es >= en:
        return "es"
    return None


def prefilter(text: str, normalized: str, config: AppConfig) -> PrefilterResult:
    cfg = config.prefilter
    stripped = text.strip()
    if not stripped:
        return PrefilterResult(False, "sin texto")
    if len(stripped) < cfg.min_length:
        return PrefilterResult(False, f"muy corto ({len(stripped)} caracteres)")

    folded = fold(text)
    for bad in config.preferences.exclude_keywords:
        if fold(bad) in folded:
            return PrefilterResult(False, f"palabra excluida: {bad}")

    pattern, symbols = _keyword_patterns(cfg.keywords)
    if not (pattern.search(normalized) or any(s in text for s in symbols)):
        return PrefilterResult(False, "sin palabras clave de empleo")

    langs = config.preferences.offer_languages
    if cfg.language_heuristic and "en" not in langs and detect_language(normalized) == "en":
        return PrefilterResult(False, "idioma: inglés")

    return PrefilterResult(True)
