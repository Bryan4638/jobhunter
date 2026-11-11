"""Contactos de Telegram (contador de usuarios nuevos) y aplicaciones enviadas."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from jobhunter.storage.db import Database

NOW = "strftime('%Y-%m-%dT%H:%M:%fZ', 'now')"


def iso(dt: datetime) -> str:
    """Mismo formato que ``strftime('%Y-%m-%dT%H:%M:%fZ')`` de SQLite (comparable como texto)."""
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


@dataclass(frozen=True, slots=True)
class Contact:
    id: int
    tg_user_id: int
    username: str | None
    display_name: str | None
    had_prior_chat: bool
    first_contacted_at: str | None
    last_contacted_at: str | None
    dm_count: int

    @property
    def known(self) -> bool:
        """Ya hubo conversación (antes del bot o en un envío anterior): no cuenta como nuevo."""
        return self.had_prior_chat or self.first_contacted_at is not None


@dataclass(frozen=True, slots=True)
class Application:
    id: int
    offer_id: int
    draft_id: int
    channel: str
    target: str
    attachment_path: str | None
    status: str
    dry_run: bool
    error: str | None
    external_id: str | None
    contact_id: int | None
    was_new_contact: bool | None
    bot_message_id: int | None
    approved_at: str
    sent_at: str | None
    replied_at: str | None
    offer_title: str | None = None


@dataclass(frozen=True, slots=True)
class NewUserEntry:
    who: str
    at: str
    offer_title: str | None


def _contact(row: Any) -> Contact:
    return Contact(
        id=row["id"],
        tg_user_id=row["tg_user_id"],
        username=row["username"],
        display_name=row["display_name"],
        had_prior_chat=bool(row["had_prior_chat"]),
        first_contacted_at=row["first_contacted_at"],
        last_contacted_at=row["last_contacted_at"],
        dm_count=row["dm_count"],
    )


def _application(row: Any) -> Application:
    keys = row.keys()
    return Application(
        id=row["id"],
        offer_id=row["offer_id"],
        draft_id=row["draft_id"],
        channel=row["channel"],
        target=row["target"],
        attachment_path=row["attachment_path"],
        status=row["status"],
        dry_run=bool(row["dry_run"]),
        error=row["error"],
        external_id=row["external_id"],
        contact_id=row["contact_id"],
        was_new_contact=None if row["was_new_contact"] is None else bool(row["was_new_contact"]),
        bot_message_id=row["bot_message_id"],
        approved_at=row["approved_at"],
        sent_at=row["sent_at"],
        replied_at=row["replied_at"],
        offer_title=row["offer_title"] if "offer_title" in keys else None,
    )


class ContactRepo:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def by_user(self, tg_user_id: int) -> Contact | None:
        async with self._db.conn.execute(
            "SELECT * FROM contacts WHERE tg_user_id = ?", (tg_user_id,)
        ) as cur:
            row = await cur.fetchone()
        return _contact(row) if row else None

    async def by_id(self, contact_id: int) -> Contact | None:
        async with self._db.conn.execute(
            "SELECT * FROM contacts WHERE id = ?", (contact_id,)
        ) as cur:
            row = await cur.fetchone()
        return _contact(row) if row else None

    async def messaged_user_ids(self) -> list[int]:
        """Usuarios a los que se envió al menos un DM real (la simulación no crea contactos)."""
        async with self._db.conn.execute(
            "SELECT tg_user_id FROM contacts WHERE dm_count > 0 ORDER BY first_contacted_at"
        ) as cur:
            return [r[0] for r in await cur.fetchall()]

    async def by_username(self, username: str) -> Contact | None:
        async with self._db.conn.execute(
            "SELECT * FROM contacts WHERE lower(username) = lower(?)", (username.lstrip("@"),)
        ) as cur:
            row = await cur.fetchone()
        return _contact(row) if row else None

    async def ensure(
        self, tg_user_id: int, username: str | None, display_name: str | None, had_prior_chat: bool
    ) -> Contact:
        """Registra al usuario la primera vez que se comprueba (sin contar todavía)."""
        await self._db.conn.execute(
            "INSERT INTO contacts (tg_user_id, username, display_name, had_prior_chat)"
            " VALUES (?, ?, ?, ?) ON CONFLICT (tg_user_id) DO UPDATE SET"
            " username = excluded.username, display_name = excluded.display_name",
            (tg_user_id, username, display_name, int(had_prior_chat)),
        )
        await self._db.conn.commit()
        contact = await self.by_user(tg_user_id)
        assert contact is not None
        return contact

    async def record_dm(self, contact_id: int, *, first_contact: bool) -> None:
        await self._db.conn.execute(
            f"UPDATE contacts SET last_contacted_at = {NOW}, dm_count = dm_count + 1,"
            f" first_contacted_at = CASE WHEN ? THEN {NOW} ELSE first_contacted_at END"
            " WHERE id = ?",
            (int(first_contact), contact_id),
        )
        await self._db.conn.commit()

    async def new_users_between(self, start: datetime, end: datetime) -> int:
        async with self._db.conn.execute(
            "SELECT COUNT(*) FROM contacts WHERE had_prior_chat = 0"
            " AND first_contacted_at >= ? AND first_contacted_at < ?",
            (iso(start), iso(end)),
        ) as cur:
            row = await cur.fetchone()
        return int(row[0]) if row else 0

    async def new_users_list(self, start: datetime, end: datetime) -> list[NewUserEntry]:
        async with self._db.conn.execute(
            "SELECT c.username, c.display_name, c.first_contacted_at,"
            " (SELECT o.title FROM applications a JOIN offers o ON o.id = a.offer_id"
            "  WHERE a.contact_id = c.id AND a.was_new_contact = 1 LIMIT 1) AS title"
            " FROM contacts c WHERE c.had_prior_chat = 0"
            " AND c.first_contacted_at >= ? AND c.first_contacted_at < ?"
            " ORDER BY c.first_contacted_at",
            (iso(start), iso(end)),
        ) as cur:
            rows = await cur.fetchall()
        return [
            NewUserEntry(
                f"@{r['username']}" if r["username"] else (r["display_name"] or "usuario"),
                r["first_contacted_at"],
                r["title"],
            )
            for r in rows
        ]


class ApplicationRepo:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def create(
        self,
        offer_id: int,
        draft_id: int,
        channel: str,
        target: str,
        attachment_path: str | None,
        dry_run: bool,
        bot_message_id: int | None,
    ) -> int:
        cur = await self._db.conn.execute(
            "INSERT INTO applications (offer_id, draft_id, channel, target, attachment_path,"
            " dry_run, bot_message_id) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (offer_id, draft_id, channel, target, attachment_path, int(dry_run), bot_message_id),
        )
        await self._db.conn.commit()
        assert cur.lastrowid is not None
        return cur.lastrowid

    async def get(self, app_id: int) -> Application | None:
        async with self._db.conn.execute(
            "SELECT a.*, o.title AS offer_title FROM applications a"
            " JOIN offers o ON o.id = a.offer_id WHERE a.id = ?",
            (app_id,),
        ) as cur:
            row = await cur.fetchone()
        return _application(row) if row else None

    async def update(self, app_id: int, **fields: Any) -> None:
        allowed = {
            "status", "error", "external_id", "contact_id", "was_new_contact",
            "bot_message_id", "replied_at", "follow_up_done",
        }  # fmt: skip
        if not fields or not set(fields) <= allowed:
            raise ValueError(f"Campos no válidos: {set(fields) - allowed}")
        sets = ", ".join(f"{k} = ?" for k in fields)
        if fields.get("status") == "sent":
            sets += f", sent_at = {NOW}"
        await self._db.conn.execute(
            f"UPDATE applications SET {sets} WHERE id = ?", (*fields.values(), app_id)
        )
        await self._db.conn.commit()

    async def ids_with_status(self, status: str) -> list[int]:
        async with self._db.conn.execute(
            "SELECT id FROM applications WHERE status = ? ORDER BY id", (status,)
        ) as cur:
            return [r[0] for r in await cur.fetchall()]

    async def blocking_for_offer(self, offer_id: int) -> Application | None:
        """Un envío real ya hecho o en curso para la oferta (nunca enviar dos veces)."""
        async with self._db.conn.execute(
            "SELECT a.*, NULL AS offer_title FROM applications a WHERE a.offer_id = ?"
            " AND a.dry_run = 0 AND a.status IN ('queued', 'sending', 'sent')"
            " ORDER BY a.id DESC LIMIT 1",
            (offer_id,),
        ) as cur:
            row = await cur.fetchone()
        return _application(row) if row else None

    async def count_sent(self, channel: str, start: datetime, end: datetime) -> int:
        async with self._db.conn.execute(
            "SELECT COUNT(*) FROM applications WHERE channel = ? AND status = 'sent'"
            " AND dry_run = 0 AND sent_at >= ? AND sent_at < ?",
            (channel, iso(start), iso(end)),
        ) as cur:
            row = await cur.fetchone()
        return int(row[0]) if row else 0

    async def last_dm_sent_at(self) -> str | None:
        async with self._db.conn.execute(
            "SELECT MAX(sent_at) FROM applications WHERE channel = 'telegram_dm'"
            " AND status = 'sent' AND dry_run = 0"
        ) as cur:
            row = await cur.fetchone()
        return row[0] if row else None

    async def recent(self, limit: int = 15) -> list[Application]:
        async with self._db.conn.execute(
            "SELECT a.*, o.title AS offer_title FROM applications a"
            " JOIN offers o ON o.id = a.offer_id ORDER BY a.id DESC LIMIT ?",
            (limit,),
        ) as cur:
            return [_application(r) for r in await cur.fetchall()]

    async def awaiting_reply(self, tg_user_id: int) -> list[Application]:
        """DMs reales enviados a ese usuario que aún no tienen respuesta."""
        async with self._db.conn.execute(
            "SELECT a.*, o.title AS offer_title FROM applications a"
            " JOIN offers o ON o.id = a.offer_id JOIN contacts c ON c.id = a.contact_id"
            " WHERE c.tg_user_id = ? AND a.channel = 'telegram_dm' AND a.status = 'sent'"
            " AND a.dry_run = 0 AND a.replied_at IS NULL",
            (tg_user_id,),
        ) as cur:
            return [_application(r) for r in await cur.fetchall()]

    async def follow_ups_due(self, sent_before: datetime) -> list[Application]:
        async with self._db.conn.execute(
            "SELECT a.*, o.title AS offer_title FROM applications a"
            " JOIN offers o ON o.id = a.offer_id WHERE a.status = 'sent' AND a.dry_run = 0"
            " AND a.replied_at IS NULL AND a.follow_up_done = 0 AND a.sent_at <= ?"
            " ORDER BY a.sent_at",
            (iso(sent_before),),
        ) as cur:
            return [_application(r) for r in await cur.fetchall()]

    async def totals(self) -> dict[str, int]:
        async with self._db.conn.execute(
            "SELECT channel || ':' || status || ':' || dry_run, COUNT(*) FROM applications"
            " GROUP BY channel, status, dry_run"
        ) as cur:
            return {r[0]: r[1] for r in await cur.fetchall()}
