from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from jobhunter.storage.db import Database

# Estados que representan un mensaje "original" con el que comparar duplicados.
_ORIGINAL_STATUSES = ("new", "processing", "not_job", "processed", "error")


@dataclass(frozen=True, slots=True)
class NewRawMessage:
    chat_id: int
    message_id: int
    sender_id: int | None
    sender_username: str | None
    sender_name: str | None
    sender_is_user: bool
    text: str
    hidden_urls: tuple[str, ...]
    link: str | None
    text_norm: str
    text_hash: str
    status: str
    filter_reason: str | None
    duplicate_of: int | None
    posted_at: datetime


class RawMessageRepo:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def exists(self, chat_id: int, message_id: int) -> bool:
        async with self._db.conn.execute(
            "SELECT 1 FROM raw_messages WHERE chat_id = ? AND message_id = ?",
            (chat_id, message_id),
        ) as cur:
            return await cur.fetchone() is not None

    async def insert(self, m: NewRawMessage) -> int | None:
        """Inserta; devuelve el id, o None si ya existía (chat_id, message_id)."""
        cur = await self._db.conn.execute(
            "INSERT OR IGNORE INTO raw_messages (chat_id, message_id, sender_id, sender_username,"
            " sender_name, sender_is_user, text, hidden_urls, link, text_norm, text_hash, status,"
            " filter_reason, duplicate_of, posted_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                m.chat_id,
                m.message_id,
                m.sender_id,
                m.sender_username,
                m.sender_name,
                int(m.sender_is_user),
                m.text,
                json.dumps(list(m.hidden_urls)),
                m.link,
                m.text_norm,
                m.text_hash,
                m.status,
                m.filter_reason,
                m.duplicate_of,
                m.posted_at.isoformat(),
            ),
        )
        await self._db.conn.commit()
        return cur.lastrowid if cur.rowcount else None

    async def find_original_by_hash(self, text_hash: str, since: datetime) -> int | None:
        placeholders = ",".join("?" * len(_ORIGINAL_STATUSES))
        async with self._db.conn.execute(
            f"SELECT id FROM raw_messages WHERE text_hash = ? AND posted_at >= ?"
            f" AND status IN ({placeholders}) ORDER BY id LIMIT 1",
            (text_hash, since.isoformat(), *_ORIGINAL_STATUSES),
        ) as cur:
            row = await cur.fetchone()
        return row[0] if row else None

    async def recent_originals(self, since: datetime) -> list[tuple[int, str]]:
        """(id, text_norm) de los originales recientes, para detectar casi-duplicados."""
        placeholders = ",".join("?" * len(_ORIGINAL_STATUSES))
        async with self._db.conn.execute(
            f"SELECT id, text_norm FROM raw_messages WHERE posted_at >= ?"
            f" AND status IN ({placeholders}) ORDER BY id",
            (since.isoformat(), *_ORIGINAL_STATUSES),
        ) as cur:
            return [(r[0], r[1]) for r in await cur.fetchall()]

    async def counts_since(self, since: datetime) -> dict[str, int]:
        async with self._db.conn.execute(
            "SELECT status, COUNT(*) FROM raw_messages WHERE created_at >= ? GROUP BY status",
            (since.isoformat(),),
        ) as cur:
            return {r[0]: r[1] for r in await cur.fetchall()}

    async def get(self, raw_id: int) -> dict[str, Any] | None:
        async with self._db.conn.execute(
            "SELECT * FROM raw_messages WHERE id = ?", (raw_id,)
        ) as cur:
            row = await cur.fetchone()
        return dict(row) if row else None

    async def set_page(self, raw_id: int, url: str, text: str) -> None:
        await self._db.conn.execute(
            "UPDATE raw_messages SET page_url = ?, page_text = ? WHERE id = ?", (url, text, raw_id)
        )
        await self._db.conn.commit()

    # --- análisis (fase 2) ------------------------------------------------------
    async def pending_ids(self, limit: int = 50) -> list[int]:
        """Mensajes pendientes de analizar, del más antiguo al más reciente."""
        async with self._db.conn.execute(
            "SELECT id FROM raw_messages WHERE status = 'new' ORDER BY posted_at, id LIMIT ?",
            (limit,),
        ) as cur:
            return [r[0] for r in await cur.fetchall()]

    async def count_pending(self) -> int:
        async with self._db.conn.execute(
            "SELECT COUNT(*) FROM raw_messages WHERE status = 'new'"
        ) as cur:
            row = await cur.fetchone()
        return int(row[0]) if row else 0

    async def set_status(
        self, raw_id: int, status: str, reason: str | None = None, error: str | None = None
    ) -> None:
        await self._db.conn.execute(
            "UPDATE raw_messages SET status = ?, filter_reason = COALESCE(?, filter_reason),"
            " error = ? WHERE id = ?",
            (status, reason, error, raw_id),
        )
        await self._db.conn.commit()

    async def record_failure(self, raw_id: int, error: str, max_attempts: int) -> bool:
        """Suma un intento fallido. Devuelve True si se agotaron (queda en ``error``)."""
        await self._db.conn.execute(
            "UPDATE raw_messages SET attempts = attempts + 1, error = ?,"
            " status = CASE WHEN attempts + 1 >= ? THEN 'error' ELSE 'new' END WHERE id = ?",
            (error[:500], max_attempts, raw_id),
        )
        await self._db.conn.commit()
        row = await self.get(raw_id)
        return bool(row and row["status"] == "error")

    async def reset_processing(self) -> int:
        """Tras un reinicio, lo que quedó a medias vuelve a la cola."""
        cur = await self._db.conn.execute(
            "UPDATE raw_messages SET status = 'new' WHERE status = 'processing'"
        )
        await self._db.conn.commit()
        return cur.rowcount
