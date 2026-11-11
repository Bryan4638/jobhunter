"""Reglas duras y ajustes de puntuación, aplicados por código (docs/05 «Puntuación final»).

La IA no puede saltárselas: trabajan sobre los datos extraídos y validados.
Un dato desconocido (``None``) nunca descarta: solo lo que la oferta dice explícitamente.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from jobhunter.config import AppConfig
from jobhunter.domain.preferences import MinSalary, SalaryRule
from jobhunter.domain.schemas import JobExtraction
from jobhunter.ingestion.text import fold

MODALITY_LABEL = {"remote": "remoto", "hybrid": "híbrido", "onsite": "presencial"}
CONTRACT_LABEL = {
    "full_time": "tiempo completo",
    "part_time": "medio tiempo",
    "freelance": "freelance",
    "internship": "prácticas",
    "other": "otro",
}
SALARY_PENALTY = 20
NO_COMPANY_NO_SALARY_PENALTY = 5


@dataclass(frozen=True, slots=True)
class Adjustment:
    rule: str
    delta: int
    label: str


@dataclass(slots=True)
class RuleOutcome:
    rejections: list[str] = field(default_factory=list)
    adjustments: list[Adjustment] = field(default_factory=list)

    @property
    def rejected(self) -> bool:
        return bool(self.rejections)

    @property
    def total(self) -> int:
        return sum(a.delta for a in self.adjustments)


def _salary_below(ex: JobExtraction, minimum: MinSalary) -> bool | None:
    """True si el máximo ofrecido es inferior al mínimo. None si no son comparables."""
    top = ex.salary_max or ex.salary_min
    if top is None or not ex.salary_currency:
        return None
    if ex.salary_currency.upper() != minimum.currency:
        return None  # sin conversión de divisas: no se compara
    period = ex.salary_period or "month"
    per_month = {"hour": 160.0, "month": 1.0, "year": 1 / 12}
    offered = top * per_month[period]
    wanted = minimum.amount * per_month[minimum.period]
    return offered < wanted


def apply_rules(
    ex: JobExtraction,
    text: str,
    config: AppConfig,
    min_salary: MinSalary | None,
    salary_rule: SalaryRule,
    injection_suspected: bool,
) -> RuleOutcome:
    prefs = config.preferences
    out = RuleOutcome()

    if ex.modality and ex.modality not in prefs.modalities:
        wanted = ", ".join(MODALITY_LABEL[m] for m in prefs.modalities)
        out.rejections.append(f"modalidad {MODALITY_LABEL[ex.modality]} (buscas: {wanted})")
    language = ex.offer_language.lower()[:2]
    if language and language not in prefs.offer_languages:
        out.rejections.append(f"oferta en otro idioma ({ex.offer_language})")
    if ex.seniority and ex.seniority not in prefs.seniority:
        out.rejections.append(f"seniority {ex.seniority}")
    if ex.contract_type and ex.contract_type not in (*prefs.contract_types, "other"):
        out.rejections.append(f"contrato de {CONTRACT_LABEL[ex.contract_type]}")
    folded = fold(text)
    for word in prefs.exclude_keywords:
        if fold(word) in folded:
            out.rejections.append(f"palabra excluida: {word}")
            break

    if min_salary and _salary_below(ex, min_salary):
        if salary_rule == "discard":
            out.rejections.append(f"salario inferior a tu mínimo ({min_salary.label()})")
        else:
            out.adjustments.append(
                Adjustment("salary_below_min", -SALARY_PENALTY, "salario bajo tu mínimo")
            )
    if not ex.company and ex.salary_min is None and ex.salary_max is None:
        out.adjustments.append(
            Adjustment(
                "no_company_no_salary", -NO_COMPANY_NO_SALARY_PENALTY, "sin empresa ni salario"
            )
        )
    if injection_suspected and config.llm.injection_penalty:
        out.adjustments.append(
            Adjustment(
                "injection_suspected",
                -config.llm.injection_penalty,
                "posible manipulación de la IA",
            )
        )
    return out


def final_score(llm_score: int, outcome: RuleOutcome) -> int:
    return max(0, min(100, llm_score + outcome.total))
