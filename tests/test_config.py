import re
from pathlib import Path

import pytest
from pydantic import ValidationError

from jobhunter.config import AppConfig, load_app_config
from jobhunter.telegram.auth import command

ROOT = Path(__file__).resolve().parents[1]


def test_defaults_when_missing(tmp_path: Path) -> None:
    config = load_app_config(tmp_path / "nope.yaml")
    assert config == AppConfig()
    assert config.preferences.modalities == ("remote",)
    assert config.preferences.offer_languages == ("es",)
    assert config.channels.preferred == "telegram_dm"
    assert config.sending.telegram_dm.max_new_users_per_day == 10
    assert config.sending.dry_run is True


def test_example_file_is_valid() -> None:
    config = load_app_config(ROOT / "config.example.yaml")
    assert config.preferences.min_salary is None
    assert config.preferences.seniority == ("junior", "mid", "senior")


def test_config_is_frozen() -> None:
    config = AppConfig()
    with pytest.raises(ValidationError):
        config.matching.threshold_immediate = 0  # type: ignore[misc]


def test_unknown_keys_rejected(tmp_path: Path) -> None:
    path = tmp_path / "c.yaml"
    path.write_text("matching:\n  threshold_inmediate: 70\n")  # typo
    with pytest.raises(ValidationError):
        load_app_config(path)


def test_command_pattern() -> None:
    pat = command("grupo_add")
    m = pat.match("/grupo_add @python_jobs")
    assert m and m.group("args") == "@python_jobs"
    m = pat.match("/grupo_add@JobHunterBot https://t.me/x")
    assert m and m.group("args") == "https://t.me/x"
    m = pat.match("/grupo_add")
    assert m and m.group("args") is None
    assert pat.match("/grupo_addx") is None


def test_empty_env_values_are_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    from jobhunter.config import Settings

    monkeypatch.setenv("TG_API_ID", "123")
    monkeypatch.setenv("TG_API_HASH", "abc")
    monkeypatch.setenv("OWNER_USER_ID", "")
    monkeypatch.setenv("BOT_TOKEN", "")
    settings = Settings(_env_file=None)  # type: ignore[call-arg]
    assert settings.owner_user_id is None
    assert settings.missing_for_run() == ["BOT_TOKEN", "OWNER_USER_ID"]


def test_args_of_typed_command_and_menu_button() -> None:
    from types import SimpleNamespace

    from jobhunter.telegram.auth import args_of, command

    typed = SimpleNamespace(pattern_match=command("salario").match("/salario 1500 USD mes "))
    assert args_of(typed) == "1500 USD mes"
    assert args_of(SimpleNamespace(pattern_match=command("ofertas").match("/ofertas"))) == ""
    button = SimpleNamespace(pattern_match=re.match(rb"^mn:(\w+)$", b"mn:salario"))
    assert args_of(button) == ""
