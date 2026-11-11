"""Proveedor Google Gemini (SDK ``google-genai``)."""

from __future__ import annotations

import asyncio
import logging
import random
import re
import time
from dataclasses import dataclass
from typing import Any

from google import genai
from google.genai import errors, types
from pydantic import ValidationError

from jobhunter.config import LLMConfig
from jobhunter.llm.base import LLMError, LLMResult, QuotaExhaustedError, T
from jobhunter.llm.rate_limit import RateLimiter

log = logging.getLogger(__name__)

RETRYABLE_CODES = {408, 429, 500, 502, 503, 504}
DEFAULT_QUOTA_PAUSE = 3600.0


def daily_quota_pause(exc: errors.APIError) -> float | None:
    """Si el 429 es por la cuota **diaria** agotada, segundos hasta poder volver a usar el
    modelo (``retryDelay`` de la respuesta). None si es un 429 normal (por minuto)."""
    if exc.code != 429:
        return None
    text = str(exc)
    if "PerDay" not in text and "per day" not in text.lower():
        return None
    match = re.search(r"retryDelay'?\"?:\s*'?\"?(\d+(?:\.\d+)?)s", text)
    return float(match.group(1)) if match else DEFAULT_QUOTA_PAUSE


def short(exc: BaseException, limit: int = 160) -> str:
    text = str(exc).replace("\n", " ")
    return text if len(text) <= limit else text[:limit] + "…"


@dataclass
class LLMUsage:
    calls: int = 0
    tokens: int = 0
    retries: int = 0  # errores transitorios (503, 429, timeout) absorbidos con reintento o respaldo
    failures: int = 0  # peticiones que terminaron sin respuesta válida


class GeminiProvider:
    def __init__(self, api_key: str, config: LLMConfig, client: Any | None = None) -> None:
        self._client = client or genai.Client(api_key=api_key)
        self._cfg = config
        self._sem = asyncio.Semaphore(config.max_concurrency)
        self._limiter = RateLimiter(config.requests_per_minute)
        self.usage = LLMUsage()
        # Modelos con la cuota diaria agotada → momento (monotonic) en que se pueden reintentar.
        self.exhausted_until: dict[str, float] = {}

    def _models(self, preferred: str | None) -> list[str]:
        first = preferred or self._cfg.models.analyze
        models = [first, *(m for m in self._cfg.models.fallback if m != first)]
        now = time.monotonic()
        available = [m for m in models if self.exhausted_until.get(m, 0) <= now]
        return available or models  # si todos están agotados, se intenta igualmente

    def exhausted_models(self) -> list[str]:
        now = time.monotonic()
        return [m for m, until in self.exhausted_until.items() if until > now]

    async def _generate(self, model: str, prompt: str, config: types.GenerateContentConfig) -> Any:
        async with self._sem:
            await self._limiter.wait()
            response = await asyncio.wait_for(
                # Petición nueva y sin historial: `contents` es solo el prompt de esta llamada.
                self._client.aio.models.generate_content(
                    model=model, contents=prompt, config=config
                ),
                timeout=self._cfg.request_timeout_seconds,
            )
        self.usage.calls += 1
        meta = getattr(response, "usage_metadata", None)
        self.usage.tokens += getattr(meta, "total_token_count", 0) or 0
        return response

    async def _call(
        self, preferred: str | None, prompt: str, config: types.GenerateContentConfig
    ) -> tuple[Any, str]:
        last_error: Exception | None = None
        quota_only = True  # ¿todos los modelos fallaron por cuota diaria?
        for model in self._models(preferred):
            for attempt in range(self._cfg.max_retries):
                try:
                    return await self._generate(model, prompt, config), model
                except errors.APIError as exc:
                    if exc.code not in RETRYABLE_CODES:
                        raise LLMError(
                            f"Gemini rechazó la petición ({exc.code}): {short(exc)}"
                        ) from exc
                    self.usage.retries += 1
                    last_error = exc
                    if (pause := daily_quota_pause(exc)) is not None:
                        # Cuota diaria agotada: reintentar no sirve; pasar al respaldo ya.
                        self.exhausted_until[model] = time.monotonic() + pause
                        log.warning(
                            "Cuota diaria de %s agotada (vuelve en ~%.0f h); uso el respaldo",
                            model, pause / 3600,
                        )  # fmt: skip
                        break
                    quota_only = False
                except TimeoutError as exc:
                    self.usage.retries += 1
                    last_error = exc
                    quota_only = False
                if attempt < self._cfg.max_retries - 1:
                    await asyncio.sleep(min(30.0, 2.0 * 2**attempt) + random.uniform(0, 1))
            else:
                log.warning(
                    "Modelo %s no disponible (%s); pruebo el siguiente",
                    model,
                    short(last_error or Exception()),
                )
        if quota_only and last_error is not None:
            raise QuotaExhaustedError(
                "Cuota diaria de Gemini agotada en todos los modelos"
            ) from last_error
        raise LLMError(
            f"Ningún modelo respondió: {short(last_error or Exception())}"
        ) from last_error

    async def generate_json(
        self,
        *,
        system: str,
        prompt: str,
        schema: type[T],
        model: str | None = None,
        temperature: float | None = None,
    ) -> LLMResult[T]:
        try:
            return await self._generate_json(system, prompt, schema, model, temperature)
        except LLMError:
            self.usage.failures += 1
            raise

    async def generate_text(
        self,
        *,
        system: str,
        prompt: str,
        model: str | None = None,
        temperature: float | None = None,
    ) -> LLMResult[str]:
        try:
            return await self._generate_text(system, prompt, model, temperature)
        except LLMError:
            self.usage.failures += 1
            raise

    async def _generate_json(
        self,
        system: str,
        prompt: str,
        schema: type[T],
        model: str | None = None,
        temperature: float | None = None,
    ) -> LLMResult[T]:
        config = types.GenerateContentConfig(
            system_instruction=system,
            response_mime_type="application/json",
            response_json_schema=schema.model_json_schema(),
            temperature=self._cfg.temperature_analyze if temperature is None else temperature,
        )
        response, used = await self._call(model, prompt, config)
        try:
            return LLMResult(schema.model_validate_json(response.text or ""), used)
        except ValidationError as exc:
            log.warning("Respuesta fuera de esquema (%s); reintento con el error", used)
            retry_prompt = (
                f"{prompt}\n\n---\nTu respuesta anterior no cumplía el esquema JSON requerido. "
                f"Errores:\n{exc}\nResponde de nuevo cumpliendo exactamente el esquema."
            )
            response, used = await self._call(used, retry_prompt, config)
            try:
                return LLMResult(schema.model_validate_json(response.text or ""), used)
            except ValidationError as exc2:
                raise LLMError(f"La IA no devolvió un JSON válido: {exc2}") from exc2

    async def _generate_text(
        self,
        system: str,
        prompt: str,
        model: str | None = None,
        temperature: float | None = None,
    ) -> LLMResult[str]:
        config = types.GenerateContentConfig(
            system_instruction=system,
            temperature=self._cfg.temperature_write if temperature is None else temperature,
        )
        response, used = await self._call(model or self._cfg.models.write, prompt, config)
        text = (response.text or "").strip()
        if not text:
            raise LLMError("La IA devolvió una respuesta vacía")
        return LLMResult(text, used)
