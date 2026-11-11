from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from jobhunter.domain.schemas import CandidateProfile
from jobhunter.storage.db import Database


@dataclass(frozen=True, slots=True)
class ProfileRecord:
    id: int
    cv_path: str
    cv_text: str
    cv_sha256: str
    profile: CandidateProfile
    status: str
    model: str | None
    created_at: str
    confirmed_at: str | None


class ProfileRepo:
    def __init__(self, db: Database) -> None:
        self._db = db

    @staticmethod
    def _row(row: Any) -> ProfileRecord:
        return ProfileRecord(
            id=row["id"],
            cv_path=row["cv_path"],
            cv_text=row["cv_text"],
            cv_sha256=row["cv_sha256"],
            profile=CandidateProfile.model_validate_json(row["profile"]),
            status=row["status"],
            model=row["model"],
            created_at=row["created_at"],
            confirmed_at=row["confirmed_at"],
        )

    async def create_draft(
        self, cv_path: str, cv_text: str, cv_sha256: str, profile: CandidateProfile, model: str
    ) -> ProfileRecord:
        cur = await self._db.conn.execute(
            "INSERT INTO profiles (cv_path, cv_text, cv_sha256, profile, model)"
            " VALUES (?, ?, ?, ?, ?)",
            (cv_path, cv_text, cv_sha256, profile.model_dump_json(), model),
        )
        await self._db.conn.commit()
        assert cur.lastrowid is not None
        record = await self.get(cur.lastrowid)
        assert record is not None
        return record

    async def get(self, profile_id: int) -> ProfileRecord | None:
        async with self._db.conn.execute("SELECT * FROM profiles WHERE id = ?", (profile_id,)) as c:
            row = await c.fetchone()
        return self._row(row) if row else None

    async def active(self) -> ProfileRecord | None:
        async with self._db.conn.execute("SELECT * FROM profiles WHERE status = 'active'") as c:
            row = await c.fetchone()
        return self._row(row) if row else None

    async def update_profile(self, profile_id: int, profile: CandidateProfile, model: str) -> None:
        await self._db.conn.execute(
            "UPDATE profiles SET profile = ?, model = ? WHERE id = ? AND status = 'draft'",
            (profile.model_dump_json(), model, profile_id),
        )
        await self._db.conn.commit()

    async def activate(self, profile_id: int, cv_path: str) -> None:
        """Archiva el perfil activo y activa este (transacción única)."""
        conn = self._db.conn
        await conn.execute("UPDATE profiles SET status = 'archived' WHERE status = 'active'")
        await conn.execute(
            "UPDATE profiles SET status = 'active', cv_path = ?,"
            " confirmed_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now') WHERE id = ?",
            (cv_path, profile_id),
        )
        await conn.commit()

    async def discard(self, profile_id: int) -> None:
        await self._db.conn.execute(
            "UPDATE profiles SET status = 'discarded' WHERE id = ? AND status = 'draft'",
            (profile_id,),
        )
        await self._db.conn.commit()
