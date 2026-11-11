from pathlib import Path

import pytest

from jobhunter.config import AppConfig
from jobhunter.domain.preferences import MinSalary, PreferencesService, parse_salary_args
from jobhunter.storage.db import Database
from jobhunter.storage.repositories import SettingsRepo


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("1500 USD mes", MinSalary(1500, "USD", "month")),
        ("1500 usd", MinSalary(1500, "USD", "month")),
        ("1.500 EUR mes", MinSalary(1500, "EUR", "month")),
        ("1,500 MXN", MinSalary(1500, "MXN", "month")),
        ("25 USD hora", MinSalary(25, "USD", "hour")),
        ("30k EUR año", MinSalary(30000, "EUR", "year")),
        ("$2000", MinSalary(2000, "USD", "month")),
        ("2000€ mensual", MinSalary(2000, "EUR", "month")),
        ("12.5 USD /h", MinSalary(12.5, "USD", "hour")),
    ],
)
def test_parse_salary(text: str, expected: MinSalary) -> None:
    assert parse_salary_args(text) == expected


@pytest.mark.parametrize("text", ["off", "OFF", "desactivar", "no"])
def test_parse_salary_off(text: str) -> None:
    assert parse_salary_args(text) is None


@pytest.mark.parametrize("text", ["1500", "USD", "abc USD", "-5 USD", "1500 USD mes extra"])
def test_parse_salary_invalid(text: str) -> None:
    with pytest.raises(ValueError):
        parse_salary_args(text)


def test_label() -> None:
    assert MinSalary(1500, "USD", "month").label() == "1.500 USD/mes"


async def test_service_db_overrides_config(tmp_path: Path) -> None:
    db = Database(tmp_path / "t.db")
    await db.connect()
    try:
        config = AppConfig.model_validate(
            {"preferences": {"min_salary": {"amount": 1000, "currency": "USD"}}}
        )
        prefs = PreferencesService(SettingsRepo(db), config)

        # Sin valor en BD → se usa config.yaml
        assert await prefs.min_salary() == MinSalary(1000, "USD", "month")
        assert await prefs.salary_rule() == "penalize"

        # El bot lo cambia
        await prefs.set_min_salary(MinSalary(2000, "EUR", "month"))
        await prefs.set_salary_rule("discard")
        assert await prefs.min_salary() == MinSalary(2000, "EUR", "month")
        assert await prefs.salary_rule() == "discard"

        # Desactivado explícitamente desde el bot → None aunque config tenga valor
        await prefs.set_min_salary(None)
        assert await prefs.min_salary() is None
    finally:
        await db.close()


# --- umbrales (/umbral) ----------------------------------------------------------

from jobhunter.domain.preferences import Thresholds, parse_threshold_args  # noqa: E402


def test_parse_threshold_args() -> None:
    current = Thresholds(80, 60)
    assert parse_threshold_args("50", current) == Thresholds(50, 50)  # el resumen baja con él
    assert parse_threshold_args("90", current) == Thresholds(90, 60)
    assert parse_threshold_args("70 40", current) == Thresholds(70, 40)
    for bad in ("", "abc", "101", "40 70", "1 2 3", "-5"):
        with pytest.raises(ValueError):
            parse_threshold_args(bad, current)


async def test_thresholds_db_overrides_config(db: Database) -> None:
    prefs = PreferencesService(SettingsRepo(db), AppConfig())
    assert await prefs.thresholds() == Thresholds(80, 60)  # valores de config.yaml
    await prefs.set_thresholds(Thresholds(20, 20))
    assert await prefs.thresholds() == Thresholds(20, 20)
    await SettingsRepo(db).set("matching.thresholds", {"immediate": "x"})
    assert await prefs.thresholds() == Thresholds(80, 60)  # valor corrupto → config
