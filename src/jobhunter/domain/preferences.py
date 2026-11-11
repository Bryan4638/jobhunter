"""Preferencias editables desde el bot (prioridad sobre config.yaml)."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Literal

from jobhunter.config import AppConfig
from jobhunter.storage.repositories import SettingsRepo

Period = Literal["hour", "month", "year"]
SalaryRule = Literal["discard", "penalize"]

SALARY_KEY = "preferences.min_salary"
SALARY_RULE_KEY = "preferences.salary_rule"
THRESHOLDS_KEY = "matching.thresholds"
PAUSED_KEY = "notifications.paused"

_PERIOD_WORDS: dict[str, Period] = {
    "hora": "hour", "h": "hour", "hr": "hour", "hour": "hour", "/h": "hour",
    "mes": "month", "mensual": "month", "month": "month", "mo": "month", "/mes": "month",
    "año": "year", "ano": "year", "anual": "year", "year": "year", "/año": "year",
}  # fmt: skip
_CURRENCY_SYMBOLS = {"$": "USD", "€": "EUR", "£": "GBP"}
PERIOD_LABEL: dict[Period, str] = {"hour": "hora", "month": "mes", "year": "año"}
OFF_WORDS = {"off", "no", "desactivar", "ninguno", "0", "quitar"}


@dataclass(frozen=True, slots=True)
class Thresholds:
    """Puntuación mínima para avisarte al momento y para entrar en el resumen diario."""

    immediate: int
    digest: int

    def __post_init__(self) -> None:
        if not (0 <= self.digest <= self.immediate <= 100):
            raise ValueError(
                "Los umbrales van de 0 a 100 y el del resumen no puede superar al inmediato"
            )


def parse_threshold_args(text: str, current: Thresholds) -> Thresholds:
    """``/umbral 50`` → inmediato 50 (el resumen baja si hace falta) · ``/umbral 70 40``."""
    tokens = text.split()
    if not 1 <= len(tokens) <= 2 or not all(t.isdigit() for t in tokens):
        raise ValueError("Escribe uno o dos números entre 0 y 100")
    values = [int(t) for t in tokens]
    if any(v > 100 for v in values):
        raise ValueError("Los umbrales van de 0 a 100")
    if len(values) == 1:
        return Thresholds(values[0], min(current.digest, values[0]))
    immediate, digest = values
    if digest > immediate:
        raise ValueError(
            "El umbral del resumen no puede ser mayor que el de aviso inmediato "
            f"({digest} > {immediate})"
        )
    return Thresholds(immediate, digest)


@dataclass(frozen=True, slots=True)
class MinSalary:
    amount: float
    currency: str
    period: Period = "month"

    def label(self) -> str:
        amount = f"{self.amount:,.0f}".replace(",", ".")
        return f"{amount} {self.currency}/{PERIOD_LABEL[self.period]}"


def _parse_amount(token: str) -> float:
    token = token.strip().lower()
    multiplier = 1
    if token.endswith("k"):
        multiplier, token = 1000, token[:-1]
    if re.fullmatch(r"\d{1,3}([.,]\d{3})+", token):  # 1.500 / 1,500 / 1.500.000
        token = re.sub(r"[.,]", "", token)
    else:
        token = token.replace(",", ".")
    try:
        value = float(token) * multiplier
    except ValueError as exc:
        raise ValueError(f"Cantidad no válida: {token!r}") from exc
    if value <= 0:
        raise ValueError("La cantidad debe ser mayor que 0")
    return value


def parse_salary_args(text: str) -> MinSalary | None:
    """Interpreta ``/salario <cantidad> <moneda> [mes|hora|año]``.

    Devuelve ``None`` si el usuario pide desactivarlo (``off``). Lanza ``ValueError``
    con un mensaje legible si no se entiende.
    """
    text = text.strip()
    if text.lower() in OFF_WORDS:
        return None

    # Separar símbolos pegados: "$1500" -> "$ 1500", "1500€" -> "1500 €"
    for symbol in _CURRENCY_SYMBOLS:
        text = text.replace(symbol, f" {symbol} ")
    tokens = text.split()
    if not tokens:
        raise ValueError("Falta la cantidad")

    amount: float | None = None
    currency: str | None = None
    period: Period = "month"
    for tok in tokens:
        low = tok.lower()
        if low in _PERIOD_WORDS:
            period = _PERIOD_WORDS[low]
        elif tok in _CURRENCY_SYMBOLS:
            currency = _CURRENCY_SYMBOLS[tok]
        elif re.fullmatch(r"[A-Za-z]{3}", tok):
            currency = tok.upper()
        elif amount is None:
            amount = _parse_amount(tok)
        else:
            raise ValueError(f"No entiendo {tok!r}")

    if amount is None:
        raise ValueError("Falta la cantidad")
    if currency is None:
        raise ValueError("Falta la moneda (por ejemplo USD, EUR, MXN)")
    return MinSalary(amount=amount, currency=currency, period=period)


class PreferencesService:
    """Lee preferencias combinando BD (bot) y config.yaml. Escribe solo en la BD."""

    def __init__(self, settings_repo: SettingsRepo, config: AppConfig) -> None:
        self._repo = settings_repo
        self._config = config

    async def min_salary(self) -> MinSalary | None:
        if await self._repo.has(SALARY_KEY):
            data = await self._repo.get(SALARY_KEY)
            return MinSalary(**data) if data else None
        cfg = self._config.preferences.min_salary
        return MinSalary(cfg.amount, cfg.currency, cfg.period) if cfg else None

    async def salary_rule(self) -> SalaryRule:
        rule = await self._repo.get(SALARY_RULE_KEY)
        return rule if rule in ("discard", "penalize") else self._config.preferences.salary_rule

    async def set_min_salary(self, salary: MinSalary | None) -> None:
        await self._repo.set(SALARY_KEY, asdict(salary) if salary else None)

    async def set_salary_rule(self, rule: SalaryRule) -> None:
        await self._repo.set(SALARY_RULE_KEY, rule)

    async def thresholds(self) -> Thresholds:
        data = await self._repo.get(THRESHOLDS_KEY)
        if isinstance(data, dict):
            try:
                return Thresholds(int(data["immediate"]), int(data["digest"]))
            except (KeyError, TypeError, ValueError):
                pass  # valor corrupto: se usan los de config.yaml
        m = self._config.matching
        return Thresholds(m.threshold_immediate, min(m.threshold_digest, m.threshold_immediate))

    async def set_thresholds(self, thresholds: Thresholds) -> None:
        await self._repo.set(THRESHOLDS_KEY, asdict(thresholds))

    async def paused(self) -> bool:
        """/pausa: las ofertas siguen analizándose pero van al resumen, sin tarjeta inmediata."""
        return bool(await self._repo.get(PAUSED_KEY))

    async def set_paused(self, paused: bool) -> None:
        await self._repo.set(PAUSED_KEY, paused)
