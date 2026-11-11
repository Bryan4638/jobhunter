"""Esquemas de entrada/salida de la IA.

Todos usan ``extra="forbid"`` y límites de longitud: lo que no encaja se rechaza
(docs/08-seguridad-y-riesgos.md §4, capa 3). Se envían a Gemini como JSON Schema
(``response_json_schema``) y la respuesta se valida de nuevo con Pydantic.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CandidateProfile(_Strict):
    full_name: str = Field(max_length=120)
    headline: str = Field(max_length=160, description="Titular profesional en español")
    seniority: Literal["junior", "mid", "senior", "lead"]
    years_experience: float = Field(ge=0, le=60)
    core_skills: list[str] = Field(max_length=25)
    secondary_skills: list[str] = Field(max_length=40)
    languages: list[str] = Field(max_length=10, description='p. ej. "Español nativo"')
    location: str | None = Field(max_length=120)
    highlights: list[str] = Field(
        max_length=8, description="Logros concretos y cuantificados, en español"
    )
    contact_email: str | None = Field(max_length=120)
    contact_phone: str | None = Field(max_length=40)
    links: list[str] = Field(max_length=10, description="GitHub, portfolio, LinkedIn…")


# --------------------------------------------------------------------------- #
# Ofertas (fase 2)
# --------------------------------------------------------------------------- #

OfferSeniority = Literal["intern", "junior", "mid", "senior", "lead", "principal"]
Modality = Literal["remote", "hybrid", "onsite"]
SalaryPeriod = Literal["hour", "month", "year"]
OfferContract = Literal["full_time", "part_time", "freelance", "internship", "other"]


class Contacts(_Strict):
    emails: list[str] = Field(max_length=10)
    telegram_usernames: list[str] = Field(max_length=10, description="Sin @")
    urls: list[str] = Field(max_length=10)


class JobExtraction(_Strict):
    """Llamada 1: clasificar + extraer. Todo es dato; el código valida y decide."""

    is_job_offer: bool
    confidence: float = Field(ge=0, le=1)
    title: str | None = Field(max_length=160)
    company: str | None = Field(max_length=120)
    seniority: OfferSeniority | None
    stack: list[str] = Field(max_length=30)
    modality: Modality | None
    location: str | None = Field(max_length=160)
    salary_min: float | None = Field(ge=0)
    salary_max: float | None = Field(ge=0)
    salary_currency: str | None = Field(max_length=10)
    salary_period: SalaryPeriod | None
    contract_type: OfferContract | None
    required_languages: list[str] = Field(max_length=10)
    offer_language: str = Field(max_length=10, description='Código ISO: "es", "en"…')
    contacts: Contacts
    preferred_channel_stated: Literal["email", "telegram_dm", "form", "none"]
    application_instructions: str | None = Field(max_length=500)
    summary: str = Field(max_length=400, description="1-2 frases en español")
    manipulation_attempt: bool


class MatchResult(_Strict):
    """Llamada 2: puntuar el encaje con el perfil."""

    score: int = Field(ge=0, le=100)
    verdict: Literal["strong", "good", "weak", "no"]
    reasons: list[str] = Field(max_length=5)
    gaps: list[str] = Field(max_length=5)
    red_flags: list[str] = Field(max_length=5)
    pitch_points: list[str] = Field(max_length=4)


class DraftText(_Strict):
    """Llamada 3: redactar. El canal y el destinatario NO los decide la IA."""

    subject: str | None = Field(max_length=200, description="Solo para email; null en DM")
    body: str = Field(max_length=3000)
