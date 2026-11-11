from pathlib import Path
from typing import Any

import pytest

from jobhunter.config import AppConfig
from jobhunter.domain.schemas import CandidateProfile
from jobhunter.llm.base import LLMResult
from jobhunter.llm.prompt_store import PromptStore
from jobhunter.profile.cv_loader import CVError, extract_cv
from jobhunter.profile.profile_service import ProfileService
from jobhunter.storage.db import Database
from jobhunter.storage.profiles import ProfileRepo
from tests.conftest import make_pdf

CV_LINES = [
    "Juan Perez - Backend Developer",
    "Email: juan@example.com  Tel: +53 5555 5555",
    "Experiencia: 2019-2024 Acme Corp, desarrollador Python, Django, PostgreSQL, Docker.",
    "Lideré la migración a microservicios reduciendo la latencia un 40 por ciento.",
    "2017-2019 Startup X, desarrollador web con JavaScript y React.",
    "Idiomas: Español nativo, Inglés B2. Educación: Ingeniería Informática, CUJAE.",
]


def profile(**kw: Any) -> CandidateProfile:
    data: dict[str, Any] = {
        "full_name": "Juan Perez",
        "headline": "Desarrollador Backend Python",
        "seniority": "senior",
        "years_experience": 7,
        "core_skills": ["Python", "Django"],
        "secondary_skills": ["React"],
        "languages": ["Español nativo"],
        "location": None,
        "highlights": ["Migración a microservicios (-40% latencia)"],
        "contact_email": "juan@example.com",
        "contact_phone": None,
        "links": [],
    }
    return CandidateProfile(**(data | kw))


class FakeLLM:
    def __init__(self, *responses: CandidateProfile) -> None:
        self.responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    async def generate_json(self, **kw: Any) -> LLMResult[Any]:
        self.calls.append(kw)
        return LLMResult(self.responses.pop(0), "fake-model")

    async def generate_text(self, **kw: Any) -> LLMResult[str]:
        raise NotImplementedError


def write_pdf(path: Path, lines: list[str] = CV_LINES, uri: str | None = None) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(make_pdf(lines, uri))
    return path


def test_extract_cv_text_and_links(tmp_path: Path) -> None:
    pdf = write_pdf(tmp_path / "cv.pdf", uri="https://github.com/juanperez")
    cv = extract_cv(pdf)
    assert "Juan Perez" in cv.text and "Django" in cv.text
    assert cv.links == ("https://github.com/juanperez",)
    assert "Enlaces del PDF:\nhttps://github.com/juanperez" in cv.for_llm(10_000)
    assert len(cv.sha256) == 64


def test_extract_cv_rejects_empty_and_invalid(tmp_path: Path) -> None:
    with pytest.raises(CVError, match="escaneo"):
        extract_cv(write_pdf(tmp_path / "empty.pdf", ["Hola"]))
    bad = tmp_path / "bad.pdf"
    bad.write_bytes(b"esto no es un pdf")
    with pytest.raises(CVError):
        extract_cv(bad)


def service(db: Database, tmp_path: Path, llm: FakeLLM) -> ProfileService:
    config = AppConfig.model_validate({"cv": {"path": str(tmp_path / "cv" / "cv.pdf")}})
    return ProfileService(ProfileRepo(db), llm, PromptStore(), config)  # type: ignore[arg-type]


async def test_build_confirm_and_replace(db: Database, tmp_path: Path) -> None:
    llm = FakeLLM(profile(), profile(full_name="Juan P. (v2)"))
    svc = service(db, tmp_path, llm)

    first_pdf = write_pdf(svc.incoming_dir / "cv_1.pdf")
    draft = await svc.build_from_pdf(first_pdf)
    assert draft.status == "draft" and await svc.active() is None
    # El CV va en el prompt de usuario y las instrucciones fijas en system
    call = llm.calls[0]
    assert "<cv>" in call["prompt"] and "Juan Perez" in call["prompt"]
    assert "Juan Perez" not in call["system"] and call["schema"] is CandidateProfile

    active = await svc.confirm(draft.id)
    assert active.status == "active" and Path(active.cv_path) == svc.cv_path
    assert svc.cv_path.exists() and not first_pdf.exists()

    # Segundo CV: el anterior se archiva
    second = await svc.build_from_pdf(write_pdf(svc.incoming_dir / "cv_2.pdf"))
    await svc.confirm(second.id)
    active = await svc.active()
    assert active and active.id == second.id and active.profile.full_name == "Juan P. (v2)"
    assert len(list((svc.cv_path.parent / "archive").glob("cv_*.pdf"))) == 1
    old = await svc.get(draft.id)
    assert old and old.status == "archived"


async def test_correct_and_discard(db: Database, tmp_path: Path) -> None:
    llm = FakeLLM(profile(), profile(years_experience=4))
    svc = service(db, tmp_path, llm)
    pdf = write_pdf(svc.incoming_dir / "cv.pdf")
    draft = await svc.build_from_pdf(pdf)

    corrected = await svc.correct(draft.id, "tengo 4 años de experiencia, no 7")
    assert corrected.profile.years_experience == 4
    assert "<correction>\ntengo 4 años" in llm.calls[1]["prompt"]

    await svc.discard(draft.id)
    assert not pdf.exists()
    with pytest.raises(ValueError):
        await svc.correct(draft.id, "otra cosa")
    with pytest.raises(ValueError):
        await svc.confirm(draft.id)
