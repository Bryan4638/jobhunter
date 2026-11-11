"""Configuración.

- ``Settings``: secretos y rutas desde variables de entorno / ``.env``.
- ``AppConfig``: comportamiento desde ``config.yaml``.

Ambos son **inmutables** (``frozen``): el pipeline de análisis los recibe como solo
lectura y no existe ningún camino para modificarlos en tiempo de ejecución
(ver docs/08-seguridad-y-riesgos.md §4). Las preferencias que el propietario cambia
desde el bot se guardan en la tabla ``settings`` y tienen prioridad sobre estos valores.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# --------------------------------------------------------------------------- #
# Secretos y rutas (.env)
# --------------------------------------------------------------------------- #


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        frozen=True,
        # `OWNER_USER_ID=` (vacío, como en .env.example) = no definido
        env_ignore_empty=True,
    )

    # Telegram - userbot (tu cuenta)
    tg_api_id: int
    tg_api_hash: SecretStr
    tg_phone: str | None = None
    tg_session_name: Path = Path("data/sessions/userbot")

    # Telegram - bot de control (opcionales para `login`, obligatorios para `run`)
    bot_token: SecretStr | None = None
    bot_session_name: Path = Path("data/sessions/bot")
    owner_user_id: int | None = None

    # IA
    llm_provider: str = "gemini"
    gemini_api_key: SecretStr | None = None

    # Email
    smtp_host: str = "smtp.gmail.com"
    smtp_port: int = 587
    smtp_user: str | None = None
    smtp_password: SecretStr | None = None
    smtp_from: str | None = None

    # General
    tz: str = "UTC"
    log_level: str = "INFO"
    data_dir: Path = Path("data")
    database_path: Path = Path("data/jobhunter.db")
    config_path: Path = Path("config.yaml")
    # Si se define, tiene prioridad sobre `sending.dry_run` de config.yaml.
    sending_dry_run: bool | None = None

    @field_validator("tz")
    @classmethod
    def _valid_tz(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError(f"Zona horaria desconocida: {value!r}") from exc
        return value

    @property
    def zoneinfo(self) -> ZoneInfo:
        return ZoneInfo(self.tz)

    def missing_for_run(self) -> list[str]:
        """Variables obligatorias para `run` que faltan."""
        missing = []
        if self.bot_token is None:
            missing.append("BOT_TOKEN")
        if self.owner_user_id is None:
            missing.append("OWNER_USER_ID")
        return missing


# --------------------------------------------------------------------------- #
# Comportamiento (config.yaml)
# --------------------------------------------------------------------------- #


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


Period = Literal["hour", "month", "year"]
Seniority = Literal["intern", "junior", "mid", "senior", "lead", "principal"]
ContractType = Literal["full_time", "part_time", "freelance"]


class SourcesConfig(_Frozen):
    catch_up_on_start: bool = True
    catch_up_max_messages: int = Field(200, ge=0)


class CVConfig(_Frozen):
    path: Path = Path("data/cv/cv.pdf")
    attachment_filename: str = "CV.pdf"
    max_size_mb: int = Field(10, ge=1, le=20)  # los bots descargan hasta 20 MB
    max_chars: int = Field(30000, ge=1000)  # texto del CV que se envía a la IA


class SalaryConfig(_Frozen):
    amount: float = Field(gt=0)
    currency: str = Field(min_length=3, max_length=3)
    period: Period = "month"


class PreferencesConfig(_Frozen):
    modalities: tuple[Literal["remote", "hybrid", "onsite"], ...] = ("remote",)
    remote_regions_ok: tuple[str, ...] = ()
    min_salary: SalaryConfig | None = None
    salary_rule: Literal["discard", "penalize"] = "penalize"
    exclude_keywords: tuple[str, ...] = (
        "sin remuneración",
        "no remunerado",
        "MLM",
        "inversión inicial",
    )
    seniority: tuple[Seniority, ...] = ("junior", "mid", "senior")
    contract_types: tuple[ContractType, ...] = ("full_time", "part_time", "freelance")
    offer_languages: tuple[str, ...] = ("es",)


class SignatureConfig(_Frozen):
    include: tuple[Literal["email", "phone", "github_portfolio"], ...] = (
        "email",
        "phone",
        "github_portfolio",
    )
    email: str | None = None
    phone: str | None = None
    github_portfolio: str | None = None


class WritingConfig(_Frozen):
    tone: str = "profesional-cercano"
    style_example: str | None = None


class MatchingConfig(_Frozen):
    threshold_immediate: int = Field(80, ge=0, le=100)
    threshold_digest: int = Field(60, ge=0, le=100)
    generate_draft_for_digest: Literal["eager", "lazy"] = "lazy"


class ChannelsConfig(_Frozen):
    preferred: Literal["telegram_dm", "email"] = "telegram_dm"
    fallback_to_email_when_dm_limit: bool = True
    allow_dm_to_post_author: bool = True


class TelegramDMConfig(_Frozen):
    max_new_users_per_day: int = Field(10, ge=0)
    warn_ratio: float = Field(0.8, gt=0, le=1)
    pin_status_message: bool = True
    min_delay_seconds: int = Field(45, ge=0)
    max_delay_seconds: int = Field(180, ge=0)
    send_cv_as_file: bool = True
    # Si Telegram pide esperar más que esto (FloodWait), el envío falla en vez de bloquear.
    max_flood_wait_seconds: int = Field(120, ge=0)
    # Carpeta de Telegram donde agrupar a los reclutadores a los que escribes ("" = no usar).
    folder_name: str = Field("Aplicadas", max_length=12)


class EmailConfig(_Frozen):
    max_per_day: int = Field(30, ge=0)
    bcc_self: bool = True


class SendingConfig(_Frozen):
    dry_run: bool = True
    require_final_confirmation: bool = True
    telegram_dm: TelegramDMConfig = TelegramDMConfig()
    email: EmailConfig = EmailConfig()
    dedupe_window_days: int = Field(30, ge=0)
    # Recordatorio de seguimiento si no hay respuesta (0 = desactivado).
    follow_up_days: int = Field(7, ge=0)


class DigestConfig(_Frozen):
    enabled: bool = True
    time: str = Field("09:00", pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    max_items: int = Field(15, ge=1)
    skip_if_empty: bool = True


class PrefilterConfig(_Frozen):
    """Reglas baratas antes de la IA. Ante la duda, dejar pasar: la IA decide en la fase 2."""

    min_length: int = Field(80, ge=0)
    # Coincidencia por prefijo, sin mayúsculas ni tildes ("desarrollador" ⊂ "desarrolladores").
    keywords: tuple[str, ...] = (
        "hiring", "vacante", "vacancy", "buscamos", "busco", "se busca", "se solicita",
        "estamos buscando", "oferta", "empleo", "trabajo", "contrat", "puesto", "plaza",
        "requisitos", "postula", "aplica", "cv", "curriculum", "freelance", "proyecto",
        "developer", "desarrollador", "programador", "engineer", "ingenier", "dev",
        "backend", "frontend", "fullstack", "full stack", "remote", "remoto",
        "salario", "sueldo", "salary", "pago", "usd", "mlc", "$", "€",
    )  # fmt: skip
    # Descarta mensajes claramente en inglés cuando offer_languages == ["es"].
    language_heuristic: bool = True


class DedupeConfig(_Frozen):
    window_days: int = Field(14, ge=1)
    # Similitud (0-100, rapidfuzz.ratio sobre texto normalizado) para considerar casi-duplicado.
    similarity_threshold: int = Field(92, ge=50, le=100)


class LLMModelsConfig(_Frozen):
    analyze: str = "gemini-3.5-flash"
    write: str = "gemini-3.5-flash"
    # Se prueban en orden si el principal está saturado (429/5xx) tras los reintentos.
    fallback: tuple[str, ...] = ("gemini-3.5-flash-lite",)


class LLMConfig(_Frozen):
    provider: Literal["gemini"] = "gemini"
    models: LLMModelsConfig = LLMModelsConfig()
    max_concurrency: int = Field(2, ge=1)
    requests_per_minute: int = Field(10, ge=1)
    max_retries: int = Field(3, ge=1, le=10)
    request_timeout_seconds: int = Field(90, ge=5)
    temperature_analyze: float = Field(0.1, ge=0, le=2)
    temperature_write: float = Field(0.7, ge=0, le=2)
    max_offer_chars: int = Field(4000, ge=200)
    injection_penalty: int = Field(15, ge=0, le=100)
    anonymize_profile: bool = False


class LinksConfig(_Frozen):
    # Si el mensaje enlaza a la oferta completa («Ir a la oferta»), leer esa página.
    fetch_pages: bool = True
    max_links: int = Field(2, ge=1, le=5)
    max_page_chars: int = Field(6000, ge=500)
    timeout_seconds: int = Field(15, ge=3, le=60)


class RetentionConfig(_Frozen):
    discard_raw_after_days: int = Field(30, ge=1)


class AppConfig(_Frozen):
    sources: SourcesConfig = SourcesConfig()
    cv: CVConfig = CVConfig()
    preferences: PreferencesConfig = PreferencesConfig()
    signature: SignatureConfig = SignatureConfig()
    writing: WritingConfig = WritingConfig()
    matching: MatchingConfig = MatchingConfig()
    channels: ChannelsConfig = ChannelsConfig()
    sending: SendingConfig = SendingConfig()
    digest: DigestConfig = DigestConfig()
    prefilter: PrefilterConfig = PrefilterConfig()
    dedupe: DedupeConfig = DedupeConfig()
    llm: LLMConfig = LLMConfig()
    links: LinksConfig = LinksConfig()
    retention: RetentionConfig = RetentionConfig()


def load_app_config(path: Path) -> AppConfig:
    """Carga ``config.yaml``; si no existe, usa los valores por defecto."""
    if not path.exists():
        return AppConfig()
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(data, dict):
        raise ValueError(f"{path} debe contener un mapa YAML en la raíz")
    return AppConfig.model_validate(data)


def effective_dry_run(settings: Settings, config: AppConfig) -> bool:
    if settings.sending_dry_run is not None:
        return settings.sending_dry_run
    return config.sending.dry_run
