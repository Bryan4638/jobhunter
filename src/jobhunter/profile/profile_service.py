"""CV → perfil estructurado con IA; borradores, correcciones y activación."""

from __future__ import annotations

import asyncio
import logging
import shutil
from datetime import UTC, datetime
from pathlib import Path

from jobhunter.config import AppConfig
from jobhunter.domain.schemas import CandidateProfile
from jobhunter.llm.base import LLMProvider
from jobhunter.llm.prompt_store import PromptStore
from jobhunter.profile.cv_loader import CVText, extract_cv
from jobhunter.storage.profiles import ProfileRecord, ProfileRepo

log = logging.getLogger(__name__)


def _install_cv(source: Path, target: Path) -> None:
    """Mueve el PDF nuevo a ``target`` archivando el anterior (bloqueante)."""
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        archive = target.parent / "archive"
        archive.mkdir(exist_ok=True)
        stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
        shutil.move(target, archive / f"cv_{stamp}.pdf")
    shutil.move(source, target)


class ProfileService:
    def __init__(
        self,
        repo: ProfileRepo,
        llm: LLMProvider,
        prompts: PromptStore,
        config: AppConfig,
    ) -> None:
        self._repo = repo
        self._llm = llm
        self._prompts = prompts
        self._config = config

    @property
    def cv_path(self) -> Path:
        return self._config.cv.path

    @property
    def incoming_dir(self) -> Path:
        return self.cv_path.parent / "incoming"

    async def active(self) -> ProfileRecord | None:
        return await self._repo.active()

    async def get(self, profile_id: int) -> ProfileRecord | None:
        return await self._repo.get(profile_id)

    async def _extract(self, cv_text: str) -> tuple[CandidateProfile, str]:
        result = await self._llm.generate_json(
            system=self._prompts.get("cv_system"),
            prompt=self._prompts.render("cv_extract", cv_text=cv_text),
            schema=CandidateProfile,
            model=self._config.llm.models.analyze,
        )
        return result.value, result.model

    async def build_from_pdf(self, pdf_path: Path) -> ProfileRecord:
        """Analiza un PDF recién subido y crea un perfil en borrador (sin activarlo)."""
        cv: CVText = await asyncio.to_thread(extract_cv, pdf_path)
        cv_text = cv.for_llm(self._config.cv.max_chars)
        profile, model = await self._extract(cv_text)
        record = await self._repo.create_draft(str(pdf_path), cv_text, cv.sha256, profile, model)
        log.info("Perfil borrador #%s creado con %s (%d páginas)", record.id, model, cv.pages)
        return record

    async def regenerate(self, profile_id: int) -> ProfileRecord:
        record = await self._require_draft(profile_id)
        profile, model = await self._extract(record.cv_text)
        await self._repo.update_profile(profile_id, profile, model)
        return await self._require_draft(profile_id)

    async def correct(self, profile_id: int, instruction: str) -> ProfileRecord:
        """Aplica una corrección escrita por el propietario (única vía de instrucciones)."""
        record = await self._require_draft(profile_id)
        result = await self._llm.generate_json(
            system=self._prompts.get("cv_system"),
            prompt=self._prompts.render(
                "cv_correct",
                profile_json=record.profile.model_dump_json(indent=2),
                instruction=instruction[:2000],
                cv_text=record.cv_text,
            ),
            schema=CandidateProfile,
            model=self._config.llm.models.analyze,
        )
        await self._repo.update_profile(profile_id, result.value, result.model)
        return await self._require_draft(profile_id)

    async def confirm(self, profile_id: int) -> ProfileRecord:
        """Mueve el PDF a ``cv.path`` (archivando el anterior) y activa el perfil."""
        record = await self._require_draft(profile_id)
        target = self.cv_path
        await asyncio.to_thread(_install_cv, Path(record.cv_path), target)
        await self._repo.activate(profile_id, str(target))
        active = await self._repo.active()
        assert active is not None
        log.info("Perfil #%s activado", profile_id)
        return active

    async def discard(self, profile_id: int) -> None:
        record = await self._repo.get(profile_id)
        if record is None or record.status != "draft":
            return
        await asyncio.to_thread(Path(record.cv_path).unlink, missing_ok=True)
        await self._repo.discard(profile_id)

    async def _require_draft(self, profile_id: int) -> ProfileRecord:
        record = await self._repo.get(profile_id)
        if record is None or record.status != "draft":
            raise ValueError("Ese perfil ya no está pendiente de confirmar.")
        return record
