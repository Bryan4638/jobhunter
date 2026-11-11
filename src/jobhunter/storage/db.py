from __future__ import annotations

import logging
from pathlib import Path

import aiosqlite

log = logging.getLogger(__name__)

MIGRATIONS_DIR = Path(__file__).parent / "migrations"


class Database:
    """Conexión SQLite única para todo el proceso, con migraciones SQL numeradas."""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path) if str(path) != ":memory:" else path
        self._conn: aiosqlite.Connection | None = None

    @property
    def conn(self) -> aiosqlite.Connection:
        if self._conn is None:
            raise RuntimeError("Database no conectada")
        return self._conn

    async def connect(self) -> None:
        if isinstance(self.path, Path):
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = await aiosqlite.connect(self.path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.execute("PRAGMA foreign_keys = ON")
        await self._conn.execute("PRAGMA journal_mode = WAL")
        await self._migrate()

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    async def _migrate(self) -> None:
        await self.conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations ("
            " version TEXT PRIMARY KEY,"
            " applied_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')))"
        )
        async with self.conn.execute("SELECT version FROM schema_migrations") as cur:
            applied = {row[0] for row in await cur.fetchall()}

        for file in sorted(MIGRATIONS_DIR.glob("*.sql")):
            if file.stem in applied:
                continue
            log.info("Aplicando migración %s", file.name)
            await self.conn.executescript(file.read_text(encoding="utf-8"))
            await self.conn.execute(
                "INSERT INTO schema_migrations (version) VALUES (?)", (file.stem,)
            )
            await self.conn.commit()
