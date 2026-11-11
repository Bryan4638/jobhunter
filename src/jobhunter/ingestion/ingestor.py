"""Ingestor: guarda cada mensaje de los grupos, lo prefiltra y detecta duplicados."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta

from jobhunter.config import AppConfig
from jobhunter.ingestion.dedupe import find_near_duplicate
from jobhunter.ingestion.models import IncomingMessage, build_message_link
from jobhunter.ingestion.prefilter import prefilter
from jobhunter.ingestion.text import normalize, text_hash
from jobhunter.storage.messages import NewRawMessage, RawMessageRepo
from jobhunter.storage.repositories import SourceChatRepo

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class IngestResult:
    raw_id: int
    status: str  # new | filtered | duplicate
    reason: str | None = None
    duplicate_of: int | None = None


class Ingestor:
    def __init__(
        self, raw_repo: RawMessageRepo, sources: SourceChatRepo, config: AppConfig
    ) -> None:
        self._raw = raw_repo
        self._sources = sources
        self._config = config
        # Serializa la deduplicación: dos copias simultáneas no deben pasar ambas como originales.
        self._lock = asyncio.Lock()
        # Se llama cuando hay un mensaje nuevo pendiente de analizar (despierta al worker).
        self.on_pending: Callable[[], None] | None = None

    async def ingest(self, msg: IncomingMessage) -> IngestResult | None:
        """Procesa un mensaje. Devuelve None si ya estaba guardado (reintento o catch-up)."""
        async with self._lock:
            if await self._raw.exists(msg.chat_id, msg.message_id):
                return None

            full_text = msg.text
            if msg.hidden_urls:
                full_text += "\n" + "\n".join(msg.hidden_urls)
            norm = normalize(full_text)
            digest = text_hash(norm)

            status, reason, dup_of = "new", None, None
            check = prefilter(msg.text, norm, self._config)
            if not check.passed:
                status, reason = "filtered", check.reason
            else:
                since = msg.posted_at - timedelta(days=self._config.dedupe.window_days)
                dup_of = await self._raw.find_original_by_hash(digest, since)
                if dup_of is None:
                    candidates = await self._raw.recent_originals(since)
                    dup_of = find_near_duplicate(
                        norm, candidates, self._config.dedupe.similarity_threshold
                    )
                if dup_of is not None:
                    status, reason = "duplicate", f"duplicado de #{dup_of}"

            raw_id = await self._raw.insert(
                NewRawMessage(
                    chat_id=msg.chat_id,
                    message_id=msg.message_id,
                    sender_id=msg.sender_id,
                    sender_username=msg.sender_username,
                    sender_name=msg.sender_name,
                    sender_is_user=msg.sender_is_user,
                    text=msg.text,
                    hidden_urls=msg.hidden_urls,
                    link=build_message_link(msg.chat_id, msg.chat_username, msg.message_id),
                    text_norm=norm,
                    text_hash=digest,
                    status=status,
                    filter_reason=reason,
                    duplicate_of=dup_of,
                    posted_at=msg.posted_at,
                )
            )
            await self._sources.touch(msg.chat_id, msg.message_id, msg.posted_at)

        if raw_id is None:
            return None
        log.info(
            "Mensaje #%s chat=%s msg=%s → %s%s",
            raw_id,
            msg.chat_id,
            msg.message_id,
            status,
            f" ({reason})" if reason else "",
        )
        if status == "new" and self.on_pending is not None:
            self.on_pending()
        return IngestResult(raw_id, status, reason, dup_of)
