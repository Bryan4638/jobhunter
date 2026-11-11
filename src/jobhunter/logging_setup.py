from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

_FORMAT = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"


def setup_logging(level: str, log_dir: Path) -> None:
    log_dir.mkdir(parents=True, exist_ok=True)
    file_handler = RotatingFileHandler(
        log_dir / "jobhunter.log", maxBytes=5 * 1024 * 1024, backupCount=5, encoding="utf-8"
    )
    logging.basicConfig(
        level=level.upper(),
        format=_FORMAT,
        handlers=[logging.StreamHandler(), file_handler],
        force=True,
    )
    # Telethon es muy verboso en INFO.
    logging.getLogger("telethon").setLevel(logging.WARNING)
