"""Redacción de borradores de aplicación (docs/05 «Redacción»).

- El canal y el destinatario vienen del código, nunca de la IA.
- La firma la añade el código con los datos de ``signature`` (o del perfil).
- El borrador se valida: contactos que no estén en la oferta ni en tu perfil → aviso.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass

from jobhunter.analysis.channel_selector import ChannelOption
from jobhunter.analysis.offer_text import offer_input
from jobhunter.config import AppConfig
from jobhunter.domain.schemas import CandidateProfile, DraftText
from jobhunter.llm.base import LLMProvider
from jobhunter.llm.guard import foreign_contacts, new_tag, sanitize
from jobhunter.llm.prompt_store import PromptStore
from jobhunter.storage.offers import Draft, Match, Offer

log = logging.getLogger(__name__)

CHANNEL_RULES = {
    "telegram_dm": (
        "mensaje directo de Telegram. Entre 60 y 120 palabras, sin asunto (subject = null). "
        "Empieza con un saludo breve y ve al grano."
    ),
    "email": (
        "email. subject obligatorio y concreto (si la oferta pide un asunto o una referencia, "
        "úsalo tal cual). Cuerpo de 120 a 200 palabras."
    ),
    "manual": (
        "texto para pegar en un formulario o como carta de presentación. Entre 100 y 180 "
        "palabras, sin asunto (subject = null)."
    ),
}


@dataclass(frozen=True, slots=True)
class WrittenDraft:
    subject: str | None
    body: str  # sin firma
    warnings: list[str]
    model: str


def candidate_for_writer(profile: CandidateProfile) -> str:
    """Solo lo necesario para redactar: sin datos de contacto (van en la firma)."""
    data = profile.model_dump(
        include={
            "full_name",
            "headline",
            "seniority",
            "years_experience",
            "core_skills",
            "secondary_skills",
            "languages",
            "location",
            "highlights",
        }
    )
    return json.dumps(data, ensure_ascii=False, indent=2)


def signature(config: AppConfig, profile: CandidateProfile | None) -> str:
    sig = config.signature
    name = profile.full_name if profile else ""
    email = sig.email or (profile.contact_email if profile else None)
    phone = sig.phone or (profile.contact_phone if profile else None)
    link = sig.github_portfolio
    if link is None and profile and profile.links:
        github = [x for x in profile.links if "github" in x.lower()]
        others = [x for x in profile.links if "linkedin" not in x.lower()]
        link = (github or others or [None])[0]
    lines = [name] if name else []
    if "email" in sig.include and email:
        lines.append(f"Email: {email}")
    if "phone" in sig.include and phone:
        lines.append(f"Tel./WhatsApp: {phone}")
    if "github_portfolio" in sig.include and link:
        label = "GitHub" if "github" in link.lower() else "Portfolio"
        lines.append(f"{label}: {link}")
    return "\n".join(lines)


def full_body(body: str, sig: str) -> str:
    return f"{body.rstrip()}\n\n{sig}" if sig else body.rstrip()


class DraftWriter:
    def __init__(self, llm: LLMProvider, prompts: PromptStore, config: AppConfig) -> None:
        self._llm = llm
        self._prompts = prompts
        self._config = config

    def _offer_text(self, offer: Offer) -> str:
        return offer_input(
            offer.text, offer.hidden_urls, self._config.llm.max_offer_chars,
            offer.page_url, offer.page_text, self._config.links.max_page_chars,
        )  # fmt: skip

    def _extraction_json(self, offer: Offer) -> str:
        return json.dumps(
            {
                "title": offer.title,
                "company": offer.company,
                "seniority": offer.seniority,
                "stack": offer.stack,
                "modality": offer.modality,
                "contract_type": offer.contract_type,
                "summary": offer.summary,
            },
            ensure_ascii=False,
            indent=2,
        )

    async def write(
        self,
        offer: Offer,
        match: Match,
        profile: CandidateProfile,
        channel: ChannelOption,
        *,
        instruction: str | None = None,
        current: Draft | None = None,
    ) -> WrittenDraft:
        tag = new_tag()
        system = self._prompts.render("draft_system", tag=tag)
        style = self._config.writing.style_example
        common = {
            "channel_rules": CHANNEL_RULES[channel.kind],
            "candidate_json": candidate_for_writer(profile),
            "tag": tag,
            "offer_text": self._offer_text(offer),
        }
        if instruction and current:
            prompt = self._prompts.render(
                "draft_revise",
                instruction=instruction[:1000],
                current_subject=current.subject or "(sin asunto)",
                current_body=current.body,
                **common,
            )
        else:
            prompt = self._prompts.render(
                "draft_write",
                application_instructions=sanitize(offer.application_instructions or "ninguna", 500),
                pitch_points="\n".join(f"- {p}" for p in match.pitch_points) or "- (ninguno)",
                style=(
                    f"\nImita el estilo de este ejemplo del candidato (no su contenido):\n"
                    f"<ejemplo>\n{style}\n</ejemplo>\n"
                    if style
                    else ""
                ),
                extraction_json=self._extraction_json(offer),
                **common,
            )

        allowed = (
            offer.text, *offer.hidden_urls, offer.page_url or "", offer.page_text or "",
            signature(self._config, profile),
        )  # fmt: skip
        result = await self._llm.generate_json(
            system=system, prompt=prompt, schema=DraftText, model=self._config.llm.models.write,
            temperature=self._config.llm.temperature_write,
        )  # fmt: skip
        draft = result.value
        foreign = foreign_contacts(f"{draft.subject or ''}\n{draft.body}", *allowed)
        if foreign:
            log.warning(
                "Borrador de la oferta %s con contactos ajenos %s; regenero", offer.id, foreign
            )
            retry = prompt + (
                "\n\nIMPORTANTE: tu versión anterior incluía datos de contacto o enlaces "
                f"({', '.join(foreign)}). No incluyas ningún contacto ni enlace en el cuerpo."
            )
            result = await self._llm.generate_json(
                system=system, prompt=retry, schema=DraftText, model=self._config.llm.models.write,
                temperature=self._config.llm.temperature_write,
            )  # fmt: skip
            draft = result.value
            foreign = foreign_contacts(f"{draft.subject or ''}\n{draft.body}", *allowed)

        warnings = (
            [f"contiene contactos que no están en la oferta ni en tu perfil: {', '.join(foreign)}"]
            if foreign
            else []
        )
        subject = draft.subject.strip() if draft.subject else None
        if channel.kind == "email" and not subject:
            subject = f"{offer.title or 'Candidatura'} — {profile.full_name}"
        if channel.kind != "email":
            subject = None
        return WrittenDraft(subject, draft.body.strip(), warnings, result.model)
