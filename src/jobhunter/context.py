from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from telethon import TelegramClient

from jobhunter.analysis.pipeline import AnalysisWorker, OfferAnalyzer
from jobhunter.config import AppConfig, Settings
from jobhunter.domain.preferences import PreferencesService
from jobhunter.ingestion.ingestor import Ingestor
from jobhunter.llm.gemini import GeminiProvider
from jobhunter.profile.profile_service import ProfileService
from jobhunter.sending.dispatcher import Dispatcher
from jobhunter.storage.db import Database
from jobhunter.storage.messages import RawMessageRepo
from jobhunter.storage.offers import OfferRepo
from jobhunter.storage.repositories import SettingsRepo, SourceChatRepo
from jobhunter.storage.sending import ApplicationRepo, ContactRepo
from jobhunter.telegram.conversation import Conversation
from jobhunter.telegram.folders import ApplicationsFolder
from jobhunter.telegram.sources import RuntimeState, SourceRegistry
from jobhunter.telegram.status_board import StatusBoard


@dataclass(slots=True)
class AppContext:
    settings: Settings
    config: AppConfig
    db: Database
    sources: SourceChatRepo
    raw_messages: RawMessageRepo
    prefs: PreferencesService
    registry: SourceRegistry
    userbot: TelegramClient
    bot: TelegramClient
    state: RuntimeState
    offers: OfferRepo
    ingestor: Ingestor
    settings_repo: SettingsRepo
    contacts: ContactRepo
    applications: ApplicationRepo
    dispatcher: Dispatcher
    status_board: StatusBoard
    # None si falta GEMINI_API_KEY
    llm: GeminiProvider | None = None
    profiles: ProfileService | None = None
    analyzer: OfferAnalyzer | None = None
    worker: AnalysisWorker | None = None
    folder: ApplicationsFolder | None = None
    conversation: Conversation = field(default_factory=Conversation)
    # Acciones del /menu: nombre del comando → handler (handlers/menu.py)
    menu_actions: dict[str, Callable[[Any], Awaitable[None]]] = field(default_factory=dict)

    @property
    def owner_id(self) -> int:
        assert self.settings.owner_user_id is not None
        return self.settings.owner_user_id
