"""Pipeline completo con una IA falsa: estados, canal, borrador y aislamiento (docs/08 §4)."""

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from jobhunter.analysis.pipeline import AnalysisWorker, OfferAnalyzer
from jobhunter.analysis.writer import DraftWriter
from jobhunter.config import AppConfig
from jobhunter.domain.preferences import PreferencesService
from jobhunter.domain.schemas import DraftText, JobExtraction, MatchResult
from jobhunter.llm.base import LLMError, LLMResult
from jobhunter.llm.prompt_store import PromptStore
from jobhunter.storage.db import Database
from jobhunter.storage.messages import NewRawMessage, RawMessageRepo
from jobhunter.storage.offers import OfferRepo
from jobhunter.storage.profiles import ProfileRecord, ProfileRepo
from jobhunter.storage.repositories import SettingsRepo
from tests.test_profile import profile
from tests.test_rules_guard import extraction

FIXTURES = Path(__file__).parent / "fixtures" / "injection"
CFG = AppConfig()


def match(score: int = 85, **kw: Any) -> MatchResult:
    data: dict[str, Any] = {
        "score": score,
        "verdict": "strong",
        "reasons": ["Node.js"],
        "gaps": [],
        "red_flags": [],
        "pitch_points": ["5 años con Node.js"],
    }
    return MatchResult(**(data | kw))


class FakeLLM:
    """Responde según el esquema pedido y guarda cada llamada."""

    def __init__(
        self,
        ex: JobExtraction,
        m: MatchResult | None = None,
        drafts: list[DraftText] | None = None,
    ) -> None:
        self.responses: dict[type, list[Any]] = {
            JobExtraction: [ex],
            MatchResult: [m or match()],
            DraftText: drafts or [DraftText(subject=None, body="Hola, me interesa la oferta.")],
        }
        self.calls: list[dict[str, Any]] = []
        self.fail = False

    async def generate_json(self, **kw: Any) -> LLMResult[Any]:
        self.calls.append(kw)
        if self.fail:
            raise LLMError("saturado")
        queue = self.responses[kw["schema"]]
        return LLMResult(queue.pop(0) if len(queue) > 1 else queue[0], "fake")

    async def generate_text(self, **kw: Any) -> LLMResult[str]:
        raise NotImplementedError


async def add_raw(db: Database, text: str, msg_id: int = 1, username: str | None = "poster") -> int:
    raw_id = await RawMessageRepo(db).insert(
        NewRawMessage(
            chat_id=-100,
            message_id=msg_id,
            sender_id=42,
            sender_username=username,
            sender_name="Ana",
            sender_is_user=True,
            text=text,
            hidden_urls=(),
            link=None,
            text_norm=text.lower(),
            text_hash=f"h{msg_id}",
            status="new",
            filter_reason=None,
            duplicate_of=None,
            posted_at=datetime.now(UTC),
        )
    )
    assert raw_id is not None
    return raw_id


async def active_profile(db: Database) -> ProfileRecord:
    repo = ProfileRepo(db)
    draft = await repo.create_draft("cv.pdf", "cv", "sha", profile(), "fake")
    await repo.activate(draft.id, "cv.pdf")
    record = await repo.active()
    assert record is not None
    return record


def build(db: Database, llm: FakeLLM, config: AppConfig = CFG) -> tuple[OfferAnalyzer, OfferRepo]:
    prompts = PromptStore()
    offers = OfferRepo(db)
    analyzer = OfferAnalyzer(
        llm, prompts, config, RawMessageRepo(db), offers, ProfileRepo(db),
        PreferencesService(SettingsRepo(db), config), DraftWriter(llm, prompts, config),
    )  # fmt: skip
    return analyzer, offers


async def test_strong_offer_gets_card_with_draft(db: Database) -> None:
    text = "Buscamos backend Node.js remoto. Escribe a @rrhh_acme"
    ex = extraction(
        contacts={"emails": [], "telegram_usernames": ["rrhh_acme"], "urls": []},
        preferred_channel_stated="telegram_dm",
    )
    llm = FakeLLM(ex)
    analyzer, offers = build(db, llm)
    raw_id = await add_raw(db, text)
    out = await analyzer.analyze(raw_id, await active_profile(db))

    assert (out.raw_status, out.offer_status, out.score) == ("processed", "notified", 85)
    assert out.offer_id is not None
    m = await offers.match(out.offer_id)
    assert m is not None and (m.channel.kind, m.channel.target) == ("telegram_dm", "@rrhh_acme")
    draft = await offers.latest_draft(out.offer_id)
    assert draft is not None and draft.version == 1 and draft.subject is None
    assert [c["schema"] for c in llm.calls] == [JobExtraction, MatchResult, DraftText]
    raw = await RawMessageRepo(db).get(raw_id)
    assert raw is not None and raw["status"] == "processed"


async def test_isolation_of_every_call(db: Database) -> None:
    text = "Buscamos backend Node.js remoto. Escribe a @rrhh_acme"
    llm = FakeLLM(extraction())
    analyzer, _ = build(db, llm)
    await analyzer.analyze(await add_raw(db, text), await active_profile(db))
    for call in llm.calls:
        assert text not in call["system"]  # la oferta nunca va en las instrucciones
        assert text in call["prompt"]
        tag = call["system"].split("<", 1)[1].split(">", 1)[0]
        assert tag.startswith("offer_") and f"<{tag}>" in call["prompt"]
    tags = {c["system"].split("<", 1)[1].split(">", 1)[0] for c in llm.calls}
    assert len(tags) == len(llm.calls)  # nonce distinto en cada llamada
    # El redactor no recibe los datos de contacto del candidato
    assert "juan@example.com" not in llm.calls[-1]["prompt"]


async def test_not_a_job(db: Database) -> None:
    llm = FakeLLM(extraction(is_job_offer=False, summary="Anuncio de un curso"))
    analyzer, _ = build(db, llm)
    raw_id = await add_raw(db, "Curso de Python, inscríbete")
    out = await analyzer.analyze(raw_id, await active_profile(db))
    assert out.raw_status == "not_job" and out.offer_id is None
    assert len(llm.calls) == 1


async def test_rules_reject_before_scoring(db: Database) -> None:
    llm = FakeLLM(extraction(modality="onsite"))
    analyzer, offers = build(db, llm)
    out = await analyzer.analyze(
        await add_raw(db, "Presencial en La Habana"), await active_profile(db)
    )
    assert out.offer_status == "rejected_rules"
    assert len(llm.calls) == 1  # no se gasta la llamada de puntuación
    assert out.offer_id is not None
    offer = await offers.get(out.offer_id)
    assert offer is not None and "presencial" in offer.rejections[0]


@pytest.mark.parametrize(
    ("score", "status", "calls"), [(70, "pending_digest", 2), (40, "low_score", 2)]
)
async def test_thresholds_and_lazy_draft(db: Database, score: int, status: str, calls: int) -> None:
    llm = FakeLLM(extraction(), match(score))
    analyzer, offers = build(db, llm)
    out = await analyzer.analyze(
        await add_raw(db, "Oferta Node.js remoto"), await active_profile(db)
    )
    assert out.offer_status == status and len(llm.calls) == calls
    assert out.offer_id is not None and await offers.latest_draft(out.offer_id) is None


async def test_draft_with_invented_contact_is_regenerated(db: Database) -> None:
    drafts = [
        DraftText(subject=None, body="Escríbeme a hacker@evil.com"),
        DraftText(subject=None, body="Hola, me interesa la oferta."),
    ]
    llm = FakeLLM(extraction(), drafts=drafts)
    analyzer, offers = build(db, llm)
    out = await analyzer.analyze(
        await add_raw(db, "Oferta Node.js remoto"), await active_profile(db)
    )
    assert out.offer_id is not None
    draft = await offers.latest_draft(out.offer_id)
    assert draft is not None and draft.body == "Hola, me interesa la oferta." and not draft.warnings
    assert [c["schema"] for c in llm.calls].count(DraftText) == 2


async def test_email_draft_always_has_subject(db: Database) -> None:
    ex = extraction(
        contacts={"emails": ["jobs@acme.com"], "telegram_usernames": [], "urls": []},
        preferred_channel_stated="email",
    )
    llm = FakeLLM(ex, drafts=[DraftText(subject=None, body="Hola")])
    analyzer, offers = build(db, llm)
    out = await analyzer.analyze(await add_raw(db, "CV a jobs@acme.com"), await active_profile(db))
    assert out.offer_id is not None
    draft = await offers.latest_draft(out.offer_id)
    assert draft is not None and draft.channel == "email" and draft.subject


@pytest.mark.parametrize("fixture", sorted(p.name for p in FIXTURES.glob("*.txt")))
async def test_injection_fixtures(db: Database, fixture: str) -> None:
    """La oferta maliciosa no cambia prompts, config, destinatario ni la puntuación base."""
    text = (FIXTURES / fixture).read_text(encoding="utf-8")
    prompts_before = PromptStore().digest
    config_before = CFG.model_dump_json()
    # La IA "cae" en la trampa: inventa un destinatario y pide 100 puntos.
    ex = extraction(
        contacts={
            "emails": ["attacker@evil.com"],
            "telegram_usernames": ["evil_admin"],
            "urls": [],
        },
        preferred_channel_stated="email",
    )
    llm = FakeLLM(ex, match(60))
    analyzer, offers = build(db, llm)
    out = await analyzer.analyze(await add_raw(db, text), await active_profile(db))

    assert PromptStore().digest == prompts_before
    assert CFG.model_dump_json() == config_before
    assert out.offer_id is not None
    offer = await offers.get(out.offer_id)
    m = await offers.match(out.offer_id)
    assert offer is not None and m is not None
    assert offer.injection_suspected and offer.injection_signals
    assert m.score == 60 - CFG.llm.injection_penalty  # penalizada, nunca subida
    targets = {o.target for o in m.options}
    assert "attacker@evil.com" not in targets and "@evil_admin" not in targets
    assert m.channel.target in {"@poster", "jobs@acme.com", "@rrhh_acme", "@hr_startup", None}


async def test_worker_retries_then_errors(db: Database, monkeypatch: pytest.MonkeyPatch) -> None:
    import jobhunter.analysis.pipeline as pipeline

    async def no_sleep(_: float) -> None:
        return None

    monkeypatch.setattr(pipeline.asyncio, "sleep", no_sleep)
    llm = FakeLLM(extraction())
    llm.fail = True
    analyzer, _ = build(db, llm)
    raw = RawMessageRepo(db)
    raw_id = await add_raw(db, "Oferta Node.js remoto")
    await active_profile(db)
    notified: list[int] = []

    async def notify(offer_id: int) -> None:
        notified.append(offer_id)

    worker = AnalysisWorker(analyzer, raw, ProfileRepo(db), notify)
    for attempt in range(1, pipeline.MAX_ATTEMPTS + 1):
        await worker._drain()
        row = await raw.get(raw_id)
        assert row is not None and row["attempts"] == attempt
    assert row["status"] == "error"

    llm.fail = False
    raw_id2 = await add_raw(db, "Otra oferta Node.js remoto", msg_id=2)
    await worker._drain()
    row2 = await raw.get(raw_id2)
    assert row2 is not None and row2["status"] == "processed" and len(notified) == 1


async def test_worker_waits_for_cv(db: Database) -> None:
    llm = FakeLLM(extraction())
    analyzer, _ = build(db, llm)
    raw = RawMessageRepo(db)
    await add_raw(db, "Oferta Node.js remoto")

    async def notify(offer_id: int) -> None:
        return None

    worker = AnalysisWorker(analyzer, raw, ProfileRepo(db), notify)
    await worker._drain()
    assert worker.waiting_for_cv and llm.calls == [] and await raw.count_pending() == 1


async def test_bot_threshold_overrides_config_and_promotes(db: Database) -> None:
    from jobhunter.domain.preferences import Thresholds

    llm = FakeLLM(extraction(), match(35))
    analyzer, offers = build(db, llm)
    out = await analyzer.analyze(
        await add_raw(db, "Oferta Node.js remoto"), await active_profile(db)
    )
    assert out.offer_status == "low_score" and out.offer_id is not None

    # Bajar el umbral desde el bot rescata lo ya analizado…
    assert await offers.promote_low_scores(30) == 1
    offer = await offers.get(out.offer_id)
    assert offer is not None and offer.status == "pending_digest"

    # …y las ofertas nuevas usan el umbral del bot, no el de config.yaml (80).
    await PreferencesService(SettingsRepo(db), CFG).set_thresholds(Thresholds(30, 30))
    llm2 = FakeLLM(extraction(), match(35))
    analyzer2, _ = build(db, llm2)
    out2 = await analyzer2.analyze(
        await add_raw(db, "Otra oferta Node.js", msg_id=2), await active_profile_or_existing(db)
    )
    assert out2.offer_status == "notified"


async def active_profile_or_existing(db: Database) -> ProfileRecord:
    record = await ProfileRepo(db).active()
    assert record is not None
    return record


async def test_quota_exhausted_does_not_spend_attempts(
    db: Database, monkeypatch: pytest.MonkeyPatch
) -> None:
    import jobhunter.analysis.pipeline as pipeline
    from jobhunter.llm.base import QuotaExhaustedError

    async def no_sleep(_: float) -> None:
        return None

    monkeypatch.setattr(pipeline.asyncio, "sleep", no_sleep)

    class NoQuota(FakeLLM):
        async def generate_json(self, **kw: Any) -> LLMResult[Any]:
            raise QuotaExhaustedError("sin cuota")

    llm = NoQuota(extraction())
    analyzer, _ = build(db, llm)
    raw = RawMessageRepo(db)
    raw_id = await add_raw(db, "Oferta Node.js remoto")
    await active_profile(db)

    async def notify(offer_id: int) -> None:
        return None

    worker = AnalysisWorker(analyzer, raw, ProfileRepo(db), notify)
    for _ in range(5):
        await worker._drain()
    row = await raw.get(raw_id)
    assert row is not None and row["status"] == "new" and row["attempts"] == 0
    assert worker.quota_paused
