import pytest

from jobhunter.config import AppConfig
from jobhunter.ingestion.prefilter import detect_language, prefilter
from jobhunter.ingestion.text import normalize, text_hash

CONFIG = AppConfig()

OFFER_ES = (
    "🚀 Estamos buscando Desarrollador Backend Python (remoto). Requisitos: 3 años con "
    "Django y PostgreSQL. Salario 1500 USD. Envía tu CV a jobs@acme.com"
)
OFFER_EN = (
    "We are looking for a Senior Python developer to join our team. You will work with "
    "Django and AWS. Requirements: 5+ years of experience. Salary is competitive and the "
    "position is full time remote."
)


def run(text: str):
    return prefilter(text, normalize(text), CONFIG)


def test_spanish_offer_passes() -> None:
    assert run(OFFER_ES).passed


def test_empty_and_short() -> None:
    assert run("   ").reason == "sin texto"
    assert run("Hola a todos 👋").reason.startswith("muy corto")


def test_no_keywords() -> None:
    text = "Buenos días grupo, ¿alguien sabe si mañana hay reunión del club de lectura? " * 2
    assert run(text).reason == "sin palabras clave de empleo"


def test_keywords_accent_and_prefix_insensitive() -> None:
    text = "Se necesitan DESARROLLADORES con experiencia en bases de datos relacionales. " * 2
    assert run(text).passed  # "desarrollador" ⊂ "desarrolladores"
    text = "Hola, este anuncio es para avisar de una nueva información en el canal. Tarifa: 30 $"
    assert run(text).passed  # símbolo literal


def test_exclude_keywords() -> None:
    result = run(OFFER_ES + " Puesto NO REMUNERADO durante los primeros meses")
    assert not result.passed and "no remunerado" in (result.reason or "")


def test_english_filtered_when_only_spanish() -> None:
    assert run(OFFER_EN).reason == "idioma: inglés"
    config_en = AppConfig.model_validate({"preferences": {"offer_languages": ["es", "en"]}})
    assert prefilter(OFFER_EN, normalize(OFFER_EN), config_en).passed


@pytest.mark.parametrize(("text", "lang"), [(OFFER_ES, "es"), (OFFER_EN, "en"), ("ok", None)])
def test_detect_language(text: str, lang: str | None) -> None:
    assert detect_language(normalize(text)) == lang


def test_normalize_ignores_format_and_accents() -> None:
    a = "🔥 *Desarrollador* Python — REMOTO!!  Envía CV a jobs@acme.com"
    b = "desarrollador python remoto envia cv a jobs@acme.com"
    assert normalize(a) == b
    assert text_hash(normalize(a)) == text_hash(b)
