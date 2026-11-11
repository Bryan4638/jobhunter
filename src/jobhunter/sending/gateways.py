"""Implementaciones reales de los gateways del Dispatcher (Telethon y SMTP)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from telethon import TelegramClient

from jobhunter.config import AppConfig, Settings
from jobhunter.sending.email_sender import send_email, sender_address, smtp_missing
from jobhunter.sending.telegram_sender import (
    Recipient,
    has_prior_chat,
    resolve_recipient,
    send_dm,
)


class TelethonDM:
    def __init__(self, userbot: TelegramClient, config: AppConfig) -> None:
        self._userbot = userbot
        self._config = config

    async def resolve(self, target: str) -> Recipient:
        return await resolve_recipient(self._userbot, target)

    async def has_prior_chat(self, recipient: Recipient) -> bool:
        return await has_prior_chat(self._userbot, recipient)

    async def send(self, to: Any, text: str, cv_path: Path | None, filename: str) -> int:
        return await send_dm(
            self._userbot,
            to,
            text,
            cv_path,
            filename,
            self._config.sending.telegram_dm.max_flood_wait_seconds,
        )


class SMTPEmail:
    def __init__(self, settings: Settings, config: AppConfig) -> None:
        self._settings = settings
        self._config = config

    @property
    def configured(self) -> bool:
        return not smtp_missing(self._settings)

    @property
    def own_address(self) -> str | None:
        return sender_address(self._settings)[1]

    async def send(
        self,
        to: str,
        subject: str,
        body: str,
        cv_path: Path | None,
        filename: str,
        name: str | None,
    ) -> str:
        return await send_email(
            self._settings,
            to,
            subject,
            body,
            cv_path,
            filename,
            name,
            self._config.sending.email.bcc_self,
        )
