"""Pipeline de análisis: mensaje guardado → oferta puntuada con canal y borrador.

Flujo por mensaje (docs/03 §1):
    sanear + detector + nonce → extraer (IA) → validar contactos → reglas duras
    → puntuar (IA) → canal (código) → borrador si la puntuación lo merece (IA)

Un único worker recorre los mensajes ``new`` en orden. Si Gemini está saturado, el
mensaje vuelve a la cola (hasta ``MAX_ATTEMPTS``) y el worker espera antes de seguir.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from jobhunter.analysis.channel_selector import Author, ChannelOption, select_channel
from jobhunter.analysis.contact_validator import validate_contacts
from jobhunter.analysis.offer_text import full_source, offer_input
from jobhunter.analysis.rules import apply_rules, final_score
from jobhunter.analysis.web_page import fetch_offer_page
from jobhunter.analysis.writer import DraftWriter
from jobhunter.config import AppConfig
from jobhunter.domain.preferences import PreferencesService
from jobhunter.domain.schemas import JobExtraction, MatchResult
from jobhunter.llm.base import LLMError, LLMProvider, QuotaExhaustedError
from jobhunter.llm.guard import detect_injection, new_tag, without_injection_lines
from jobhunter.llm.prompt_store import PromptStore
from jobhunter.storage.messages import RawMessageRepo
from jobhunter.storage.offers import Draft, Offer, OfferRepo
from jobhunter.storage.profiles import ProfileRecord, ProfileRepo

log = logging.getLogger(__name__)

MAX_ATTEMPTS = 3
MIN_CONFIDENCE = 0.5
RETRY_PAUSE_SECONDS = 60
QUOTA_PAUSE_SECONDS = 15 * 60
IDLE_POLL_SECONDS = 60

Notify = Callable[[int], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class AnalysisOutcome:
    raw_status: str  # not_job | processed
    offer_id: int | None = None
    offer_status: str | None = None
    score: int | None = None


class OfferAnalyzer:
    def __init__(
        self,
        llm: LLMProvider,
        prompts: PromptStore,
        config: AppConfig,
        raw: RawMessageRepo,
        offers: OfferRepo,
        profiles: ProfileRepo,
        prefs: PreferencesService,
        writer: DraftWriter,
    ) -> None:
        self._llm = llm
        self._prompts = prompts
        self._config = config
        self._raw = raw
        self._offers = offers
        self._profiles = profiles
        self._prefs = prefs
        self.writer = writer

    async def _preferences_json(self) -> str:
        p = self._config.preferences
        salary = await self._prefs.min_salary()
        return json.dumps(
            {
                "modalidades": list(p.modalities),
                "zona_del_remoto": list(p.remote_regions_ok) or "cualquiera",
                "seniority": list(p.seniority),
                "tipos_de_contrato": list(p.contract_types),
                "salario_minimo": salary.label() if salary else "sin mínimo",
            },
            ensure_ascii=False,
        )

    async def analyze(self, raw_id: int, profile: ProfileRecord) -> AnalysisOutcome:
        row = await self._raw.get(raw_id)
        if row is None:
            raise ValueError(f"Mensaje #{raw_id} no existe")
        hidden = tuple(json.loads(row["hidden_urls"]))
        text: str = row["text"]
        cfg = self._config.llm

        # Oferta completa en la página enlazada («Ir a la oferta»): se lee una sola vez.
        links = self._config.links
        page_url, page_text = row.get("page_url"), row.get("page_text")
        if page_text is None and links.fetch_pages:
            page = await fetch_offer_page(
                text, hidden, max_links=links.max_links, timeout_s=links.timeout_seconds
            )
            if page is not None:
                page_url, page_text = page.url, page.text[: links.max_page_chars]
                await self._raw.set_page(raw_id, page_url, page_text)

        # Capa 1: saneado + delimitador con nonce. Capa 5: detector (solo informa).
        offer_text = offer_input(
            text, hidden, cfg.max_offer_chars, page_url, page_text, links.max_page_chars
        )
        source = full_source(text, page_text)
        signals = detect_injection(source)
        tag = new_tag()
        system = self._prompts.render("offer_system", tag=tag)

        extraction = await self._llm.generate_json(
            system=system,
            prompt=self._prompts.render("offer_extract", tag=tag, offer_text=offer_text),
            schema=JobExtraction,
            model=cfg.models.analyze,
        )
        ex = extraction.value
        if not ex.is_job_offer or ex.confidence < MIN_CONFIDENCE:
            reason = f"la IA no lo considera una oferta ({ex.summary[:150]})"
            await self._raw.set_status(raw_id, "not_job", reason=reason)
            log.info("Mensaje #%s → no es una oferta", raw_id)
            return AnalysisOutcome("not_job")

        # Solo cuentan contactos literales fuera de las líneas con intentos de manipulación.
        page_links = (page_url,) if page_url else ()
        contacts = validate_contacts(
            ex.contacts, without_injection_lines(source), (*hidden, *page_links)
        )
        if contacts.rejected:
            log.warning(
                "Mensaje #%s: contactos no literales descartados %s", raw_id, contacts.rejected
            )
        if ex.manipulation_attempt and "la IA lo marcó" not in signals:
            signals.append("la IA lo marcó")
        injection = bool(signals)

        outcome = apply_rules(
            ex,
            text,
            self._config,
            await self._prefs.min_salary(),
            await self._prefs.salary_rule(),
            injection,
        )
        if outcome.rejected:
            offer_id = await self._offers.create(
                raw_id, ex, contacts, signals, injection, outcome.rejections,
                extraction.model, "rejected_rules",
            )  # fmt: skip
            await self._raw.set_status(raw_id, "processed")
            log.info(
                "Oferta #%s descartada por reglas: %s", offer_id, "; ".join(outcome.rejections)
            )
            return AnalysisOutcome("processed", offer_id, "rejected_rules")

        # Puntuación: nuevo nonce, llamada independiente y sin historial.
        tag = new_tag()
        scored = await self._llm.generate_json(
            system=self._prompts.render("offer_system", tag=tag),
            prompt=self._prompts.render(
                "offer_score",
                tag=tag,
                offer_text=offer_text,
                profile_json=profile.profile.model_dump_json(indent=2),
                preferences_json=await self._preferences_json(),
                extraction_json=ex.model_dump_json(
                    indent=2, exclude={"contacts", "manipulation_attempt", "confidence"}
                ),
            ),
            schema=MatchResult,
            model=cfg.models.analyze,
        )
        result: MatchResult = scored.value
        score = final_score(result.score, outcome)

        author = Author(
            row["sender_id"],
            row["sender_username"],
            row["sender_name"],
            bool(row["sender_is_user"]),
        )
        choice = select_channel(
            contacts,
            ex.preferred_channel_stated,
            ex.application_instructions,
            author,
            self._config.channels.preferred,
            self._config.channels.allow_dm_to_post_author,
        )

        m = self._config.matching
        limits = await self._prefs.thresholds()  # /umbral tiene prioridad sobre config.yaml
        if score >= limits.immediate:
            status = "notified"
        elif score >= limits.digest:
            status = "pending_digest"
        else:
            status = "low_score"
        offer_id = await self._offers.create(
            raw_id, ex, contacts, signals, injection, [], extraction.model, status
        )
        await self._offers.save_match(
            offer_id, profile.id, score, result, outcome.adjustments, choice.chosen,
            choice.reason, choice.options, scored.model,
        )  # fmt: skip
        if status == "notified" or (
            status == "pending_digest" and m.generate_draft_for_digest == "eager"
        ):
            try:
                await self.ensure_draft(offer_id, profile)
            except LLMError as exc:
                # La oferta ya está guardada: la tarjeta ofrecerá «Redactar mensaje».
                log.warning("Oferta #%s sin borrador por ahora: %s", offer_id, exc)
        await self._raw.set_status(raw_id, "processed")
        log.info("Oferta #%s · %s/100 → %s", offer_id, score, status)
        return AnalysisOutcome("processed", offer_id, status, score)

    async def rescore(self, offer_id: int, profile: ProfileRecord) -> str | None:
        """/reevaluar: vuelve a aplicar reglas y puntuación con el perfil y preferencias actuales.

        Reutiliza la extracción guardada (no se vuelve a extraer). No envía tarjetas: lo que
        supere el umbral queda en «por revisar». Devuelve el nuevo estado.
        """
        offer = await self._offers.get(offer_id)
        ex = await self._offers.extraction(offer_id)
        if offer is None or ex is None:
            return None
        outcome = apply_rules(
            ex,
            offer.text,
            self._config,
            await self._prefs.min_salary(),
            await self._prefs.salary_rule(),
            offer.injection_suspected,
        )
        if outcome.rejected:
            await self._offers.update_analysis(offer_id, "rejected_rules", outcome.rejections)
            return "rejected_rules"

        tag = new_tag()
        offer_text = offer_input(
            offer.text, offer.hidden_urls, self._config.llm.max_offer_chars,
            offer.page_url, offer.page_text, self._config.links.max_page_chars,
        )  # fmt: skip
        scored = await self._llm.generate_json(
            system=self._prompts.render("offer_system", tag=tag),
            prompt=self._prompts.render(
                "offer_score",
                tag=tag,
                offer_text=offer_text,
                profile_json=profile.profile.model_dump_json(indent=2),
                preferences_json=await self._preferences_json(),
                extraction_json=ex.model_dump_json(
                    indent=2, exclude={"contacts", "manipulation_attempt", "confidence"}
                ),
            ),
            schema=MatchResult,
            model=self._config.llm.models.analyze,
        )
        score = final_score(scored.value.score, outcome)
        author = Author(
            offer.sender_id, offer.sender_username, offer.sender_name, offer.sender_is_user
        )
        choice = select_channel(
            offer.contacts,
            ex.preferred_channel_stated,
            ex.application_instructions,
            author,
            self._config.channels.preferred,
            self._config.channels.allow_dm_to_post_author,
        )
        await self._offers.save_match(
            offer_id, profile.id, score, scored.value, outcome.adjustments, choice.chosen,
            choice.reason, choice.options, scored.model,
        )  # fmt: skip
        limits = await self._prefs.thresholds()
        status = "pending_digest" if score >= limits.digest else "low_score"
        if offer.status in ("notified", "snoozed") and status == "pending_digest":
            status = offer.status  # ya la habías visto: no cambia de sitio
        await self._offers.update_analysis(offer_id, status, [])
        return status

    async def ensure_draft(self, offer_id: int, profile: ProfileRecord) -> Draft | None:
        """Genera el borrador si aún no existe (redacción perezosa)."""
        if (draft := await self._offers.latest_draft(offer_id)) is not None:
            return draft
        return await self.new_draft(offer_id, profile)

    async def new_draft(
        self,
        offer_id: int,
        profile: ProfileRecord,
        *,
        channel: ChannelOption | None = None,
        instruction: str | None = None,
    ) -> Draft | None:
        offer = await self._offers.get(offer_id)
        match = await self._offers.match(offer_id)
        if offer is None or match is None:
            return None
        channel = channel or match.channel
        current = await self._offers.latest_draft(offer_id) if instruction else None
        written = await self.writer.write(
            offer, match, profile.profile, channel, instruction=instruction, current=current
        )
        return await self._offers.add_draft(
            offer_id,
            channel,
            written.subject,
            written.body,
            "llm_instruction" if instruction else "llm",
            written.warnings,
            written.model,
            instruction,
        )

    async def get_offer(self, offer_id: int) -> Offer | None:
        return await self._offers.get(offer_id)


class AnalysisWorker:
    """Recorre la cola de mensajes ``new`` y avisa de las ofertas que merecen tarjeta."""

    def __init__(
        self,
        analyzer: OfferAnalyzer,
        raw: RawMessageRepo,
        profiles: ProfileRepo,
        notify: Notify,
    ) -> None:
        self._analyzer = analyzer
        self._raw = raw
        self._profiles = profiles
        self._notify = notify
        self._wake = asyncio.Event()
        self.waiting_for_cv = False
        self.busy = False
        self.analyzed = 0
        self.quota_paused = False

    def wake(self) -> None:
        self._wake.set()

    async def _sleep(self, seconds: float) -> None:
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(self._wake.wait(), timeout=seconds)
        self._wake.clear()

    async def run(self) -> None:
        reset = await self._raw.reset_processing()
        if reset:
            log.info("Reencolados %d mensajes que quedaron a medias", reset)
        while True:
            try:
                await self._drain()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("Error inesperado en el worker de análisis")
                await asyncio.sleep(RETRY_PAUSE_SECONDS)
            await self._sleep(IDLE_POLL_SECONDS)

    async def _drain(self) -> None:
        while ids := await self._raw.pending_ids():
            profile = await self._profiles.active()
            self.waiting_for_cv = profile is None
            if profile is None:
                return  # sin CV no se puede puntuar; se reanuda al confirmarlo
            for raw_id in ids:
                if not await self._process(raw_id, profile):
                    return

    async def _process(self, raw_id: int, profile: ProfileRecord) -> bool:
        """Analiza un mensaje. Devuelve False si hay que pausar la cola (IA saturada)."""
        await self._raw.set_status(raw_id, "processing")
        self.busy = True
        try:
            outcome = await self._analyzer.analyze(raw_id, profile)
        except QuotaExhaustedError:
            # Sin cuota no es culpa del mensaje: vuelve a la cola sin gastar intentos.
            await self._raw.set_status(raw_id, "new")
            self.quota_paused = True
            log.warning("Cuota diaria de Gemini agotada: pauso el análisis 15 min")
            await asyncio.sleep(QUOTA_PAUSE_SECONDS)
            return False
        except LLMError as exc:
            exhausted = await self._raw.record_failure(raw_id, str(exc), MAX_ATTEMPTS)
            log.warning(
                "Análisis del mensaje #%s falló (%s)%s", raw_id, exc,
                " · se marca como error" if exhausted else " · se reintentará",
            )  # fmt: skip
            await asyncio.sleep(RETRY_PAUSE_SECONDS)
            return False
        except Exception as exc:
            log.exception("Error analizando el mensaje #%s", raw_id)
            await self._raw.set_status(raw_id, "error", error=f"{type(exc).__name__}: {exc}"[:500])
            return True
        finally:
            self.busy = False
        self.analyzed += 1
        self.quota_paused = False
        if outcome.offer_status == "notified" and outcome.offer_id is not None:
            try:
                await self._notify(outcome.offer_id)
            except Exception:
                log.exception("No pude enviar la tarjeta de la oferta #%s", outcome.offer_id)
        return True
