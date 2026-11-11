"""CLI: ``python -m jobhunter {login,run}``."""

from __future__ import annotations

import argparse
import asyncio
import sys

from pydantic import ValidationError

from jobhunter.app import StartupError, login, run
from jobhunter.config import Settings, load_app_config
from jobhunter.logging_setup import setup_logging


def _load_settings() -> Settings:
    try:
        return Settings()  # type: ignore[call-arg]
    except ValidationError as exc:
        fields = ", ".join(str(e["loc"][0]).upper() for e in exc.errors())
        sys.exit(f"Configuración incompleta o inválida en .env: {fields}\n{exc}")


def main() -> None:
    parser = argparse.ArgumentParser(prog="jobhunter")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("login", help="Iniciar sesión con tu cuenta de Telegram (interactivo)")
    sub.add_parser("run", help="Arrancar el bot")
    args = parser.parse_args()

    settings = _load_settings()
    setup_logging(settings.log_level, settings.data_dir / "logs")

    try:
        if args.cmd == "login":
            asyncio.run(login(settings))
        else:
            try:
                config = load_app_config(settings.config_path)
            except (ValidationError, ValueError) as exc:
                sys.exit(f"Error en {settings.config_path}:\n{exc}")
            asyncio.run(run(settings, config))
    except StartupError as exc:
        sys.exit(f"❌ {exc}")
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
