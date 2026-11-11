from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from jobhunter.storage.db import Database


@dataclass(frozen=True, slots=True)
class SourceChat:
    id: int
    title: str
    username: str | None
    kind: str
    enabled: bool
    last_message_id: int | None
    last_message_at: str | None


class SourceChatRepo:
    """Grupos y canales monitorizados (gestionados solo desde el bot)."""

    def __init__(self, db: Database) -> None:
        self._db = db

    @staticmethod
    def _row(row: Any) -> SourceChat:
        return SourceChat(
            id=row["id"],
            title=row["title"],
            username=row["username"],
            kind=row["kind"],
            enabled=bool(row["enabled"]),
            last_message_id=row["last_message_id"],
            last_message_at=row["last_message_at"],
        )

    async def list(self) -> list[SourceChat]:
        async with self._db.conn.execute(
            "SELECT * FROM source_chats ORDER BY title COLLATE NOCASE"
        ) as cur:
            return [self._row(r) for r in await cur.fetchall()]

    async def get(self, chat_id: int) -> SourceChat | None:
        async with self._db.conn.execute(
            "SELECT * FROM source_chats WHERE id = ?", (chat_id,)
        ) as cur:
            row = await cur.fetchone()
        return self._row(row) if row else None

    async def enabled_ids(self) -> set[int]:
        async with self._db.conn.execute("SELECT id FROM source_chats WHERE enabled = 1") as cur:
            return {r[0] for r in await cur.fetchall()}

    async def add(self, chat_id: int, title: str, username: str | None, kind: str) -> bool:
        """Añade (o reactiva) un chat. Devuelve True si era nuevo."""
        existed = await self.get(chat_id) is not None
        await self._db.conn.execute(
            "INSERT INTO source_chats (id, title, username, kind, enabled) VALUES (?, ?, ?, ?, 1)"
            " ON CONFLICT(id) DO UPDATE SET title = excluded.title,"
            " username = excluded.username, kind = excluded.kind, enabled = 1",
            (chat_id, title, username, kind),
        )
        await self._db.conn.commit()
        return not existed

    async def set_enabled(self, chat_id: int, enabled: bool) -> bool:
        cur = await self._db.conn.execute(
            "UPDATE source_chats SET enabled = ? WHERE id = ?", (int(enabled), chat_id)
        )
        await self._db.conn.commit()
        return cur.rowcount > 0

    async def remove(self, chat_id: int) -> bool:
        cur = await self._db.conn.execute("DELETE FROM source_chats WHERE id = ?", (chat_id,))
        await self._db.conn.commit()
        return cur.rowcount > 0

    async def touch(self, chat_id: int, message_id: int, at: datetime) -> None:
        await self._db.conn.execute(
            "UPDATE source_chats SET last_message_id = MAX(COALESCE(last_message_id, 0), ?),"
            " last_message_at = ? WHERE id = ?",
            (message_id, at.isoformat(), chat_id),
        )
        await self._db.conn.commit()


_MISSING = object()


class SettingsRepo:
    """Clave-valor JSON. Solo lo escriben los handlers del propietario (ADR-010/011)."""

    def __init__(self, db: Database) -> None:
        self._db = db

    async def get(self, key: str, default: Any = None) -> Any:
        async with self._db.conn.execute("SELECT value FROM settings WHERE key = ?", (key,)) as cur:
            row = await cur.fetchone()
        return json.loads(row[0]) if row else default

    async def has(self, key: str) -> bool:
        return await self.get(key, _MISSING) is not _MISSING

    async def set(self, key: str, value: Any) -> None:
        await self._db.conn.execute(
            "INSERT INTO settings (key, value) VALUES (?, ?)"
            " ON CONFLICT(key) DO UPDATE SET value = excluded.value,"
            " updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')",
            (key, json.dumps(value, ensure_ascii=False)),
        )
        await self._db.conn.commit()

    async def delete(self, key: str) -> None:
        await self._db.conn.execute("DELETE FROM settings WHERE key = ?", (key,))
        await self._db.conn.commit()
