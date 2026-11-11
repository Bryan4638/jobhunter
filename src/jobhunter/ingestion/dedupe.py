from __future__ import annotations

from collections.abc import Sequence

from rapidfuzz import fuzz, process


def find_near_duplicate(
    normalized: str, candidates: Sequence[tuple[int, str]], threshold: int
) -> int | None:
    """Id del candidato más parecido con similitud >= threshold (0-100), o None.

    Usa ``fuzz.ratio`` (no ``token_set_ratio``): una oferta corta no debe considerarse
    duplicada de otra larga solo porque sus palabras estén contenidas en ella.
    """
    if not normalized or not candidates:
        return None
    n = len(normalized)
    # Descarte rápido por longitud. ratio = 2·M/(n+m)·100 ≥ t  ⇒  |n−m| ≤ 2n(100−t)/t.
    max_diff = 2 * n * (100 - threshold) / threshold
    pool = {cid: norm for cid, norm in candidates if abs(len(norm) - n) <= max_diff}
    if not pool:
        return None
    match = process.extractOne(normalized, pool, scorer=fuzz.ratio, score_cutoff=threshold)
    return match[2] if match else None
