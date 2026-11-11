"""Ofertas, puntuaciones (matches), borradores y tarjetas enviadas por el bot."""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from jobhunter.analysis.channel_selector import ChannelOption
from jobhunter.analysis.contact_validator import ValidContacts
from jobhunter.analysis.rules import Adjustment
from jobhunter.domain.schemas import JobExtraction, MatchResult
from jobhunter.storage.db import Database

# Ofertas que esperan una decisión tuya.
OPEN_STATUSES = ("notified", "pending_digest", "snoozed", "manual_pending")
# Ofertas descartadas automáticamente (se pueden consultar).
AUTO_DISCARDED = ("rejected_rules", "low_score")


@dataclass(frozen=True, slots=True)
class Offer:
    id: int
    raw_message_id: int
    status: str
    title: str | None
    company: str | None
    seniority: str | None
    stack: list[str]
    modality: str | None
    location: str | None
    salary_min: float | None
    salary_max: float | None
    salary_currency: str | None
    salary_period: str | None
    contract_type: str | None
    language: str | None
    contacts: ValidContacts
    application_instructions: str | None
    summary: str | None
    injection_suspected: bool
    injection_signals: list[str]
    rejections: list[str]
    created_at: str
    # Del mensaje original
    chat_id: int
    chat_title: str | None
    link: str | None
    text: str
    hidden_urls: tuple[str, ...]
    posted_at: str
    sender_id: int | None
    sender_username: str | None
    sender_name: str | None
    sender_is_user: bool
    # Página enlazada de la oferta (si se leyó)
    page_url: str | None = None
    page_text: str | None = None


@dataclass(frozen=True, slots=True)
class Match:
    id: int
    offer_id: int
    profile_id: int
    score: int
    llm_score: int
    verdict: str | None
    adjustments: list[dict[str, Any]]
    reasons: list[str]
    gaps: list[str]
    red_flags: list[str]
    pitch_points: list[str]
    channel: ChannelOption
    channel_reason: str | None
    options: list[ChannelOption]
    model: str | None


@dataclass(frozen=True, slots=True)
class Draft:
    id: int
    offer_id: int
    version: int
    channel: str
    target: str | None
    subject: str | None
    body: str
    origin: str
    instruction: str | None
    warnings: list[str]
    model: str | None


@dataclass(frozen=True, slots=True)
class OfferSummary:
    id: int
    status: str
    title: str | None
    company: str | None
    modality: str | None
    score: int | None
    rejections: list[str]
    created_at: str


def _contacts(data: str) -> ValidContacts:
    d = json.loads(data or "{}")
    return ValidContacts(
        tuple(d.get("emails", [])), tuple(d.get("usernames", [])), tuple(d.get("urls", []))
    )


def _draft(row: Any) -> Draft:
    return Draft(
        id=row["id"],
        offer_id=row["offer_id"],
        version=row["version"],
        channel=row["channel"],
        target=row["target"],
        subject=row["subject"],
        body=row["body"],
        origin=row["origin"],
        instruction=row["instruction"],
        warnings=json.loads(row["warnings"]),
        model=row["model"],
    )


def _option(d: dict[str, Any]) -> ChannelOption:
    return ChannelOption(d["kind"], d.get("target"), d.get("label") or d["kind"])


class OfferRepo:
    def __init__(self, db: Database) -> None:
        self._db = db

    # --- ofertas --------------------------------------------------------------
    async def create(
        self,
        raw_id: int,
        ex: JobExtraction,
        contacts: ValidContacts,
        injection_signals: list[str],
        injection_suspected: bool,
        rejections: list[str],
        model: str,
        status: str,
    ) -> int:
        cur = await self._db.conn.execute(
            "INSERT INTO offers (raw_message_id, title, company, seniority, stack, modality,"
            " location, salary_min, salary_max, salary_currency, salary_period, contract_type,"
            " language, contacts, application_instructions, summary, injection_suspected,"
            " injection_signals, rejections, extraction, model, status)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                raw_id,
                ex.title,
                ex.company,
                ex.seniority,
                json.dumps(ex.stack, ensure_ascii=False),
                ex.modality,
                ex.location,
                ex.salary_min,
                ex.salary_max,
                ex.salary_currency,
                ex.salary_period,
                ex.contract_type,
                ex.offer_language,
                json.dumps(
                    {
                        "emails": contacts.emails,
                        "usernames": contacts.usernames,
                        "urls": contacts.urls,
                    }
                ),
                ex.application_instructions,
                ex.summary,
                int(injection_suspected),
                json.dumps(injection_signals, ensure_ascii=False),
                json.dumps(rejections, ensure_ascii=False),
                ex.model_dump_json(),
                model,
                status,
            ),
        )
        await self._db.conn.commit()
        assert cur.lastrowid is not None
        return cur.lastrowid

    async def get(self, offer_id: int) -> Offer | None:
        async with self._db.conn.execute(
            "SELECT o.*, r.chat_id, r.link, r.text, r.hidden_urls, r.posted_at, r.sender_id,"
            " r.sender_username, r.sender_name, r.sender_is_user, r.page_url, r.page_text,"
            " s.title AS chat_title"
            " FROM offers o JOIN raw_messages r ON r.id = o.raw_message_id"
            " LEFT JOIN source_chats s ON s.id = r.chat_id WHERE o.id = ?",
            (offer_id,),
        ) as cur:
            row = await cur.fetchone()
        if row is None:
            return None
        return Offer(
            id=row["id"],
            raw_message_id=row["raw_message_id"],
            status=row["status"],
            title=row["title"],
            company=row["company"],
            seniority=row["seniority"],
            stack=json.loads(row["stack"]),
            modality=row["modality"],
            location=row["location"],
            salary_min=row["salary_min"],
            salary_max=row["salary_max"],
            salary_currency=row["salary_currency"],
            salary_period=row["salary_period"],
            contract_type=row["contract_type"],
            language=row["language"],
            contacts=_contacts(row["contacts"]),
            application_instructions=row["application_instructions"],
            summary=row["summary"],
            injection_suspected=bool(row["injection_suspected"]),
            injection_signals=json.loads(row["injection_signals"]),
            rejections=json.loads(row["rejections"]),
            created_at=row["created_at"],
            chat_id=row["chat_id"],
            chat_title=row["chat_title"],
            link=row["link"],
            text=row["text"],
            hidden_urls=tuple(json.loads(row["hidden_urls"])),
            posted_at=row["posted_at"],
            sender_id=row["sender_id"],
            sender_username=row["sender_username"],
            sender_name=row["sender_name"],
            sender_is_user=bool(row["sender_is_user"]),
            page_url=row["page_url"],
            page_text=row["page_text"],
        )

    async def set_status(self, offer_id: int, status: str) -> None:
        await self._db.conn.execute(
            "UPDATE offers SET status = ?, updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')"
            " WHERE id = ?",
            (status, offer_id),
        )
        await self._db.conn.commit()

    async def summaries(
        self, statuses: Iterable[str], limit: int, since: datetime | None = None
    ) -> list[OfferSummary]:
        """Ofertas con su mejor puntuación, de mayor a menor."""
        statuses = tuple(statuses)
        placeholders = ",".join("?" * len(statuses))
        params: list[Any] = list(statuses)
        where = f"o.status IN ({placeholders})"
        if since is not None:
            where += " AND o.created_at >= ?"
            params.append(since.isoformat())
        params.append(limit)
        async with self._db.conn.execute(
            "SELECT o.id, o.status, o.title, o.company, o.modality, o.rejections, o.created_at,"
            " (SELECT MAX(score) FROM matches m WHERE m.offer_id = o.id) AS score"
            f" FROM offers o WHERE {where}"
            " ORDER BY score IS NULL, score DESC, o.id DESC LIMIT ?",
            params,
        ) as cur:
            rows = await cur.fetchall()
        return [
            OfferSummary(
                r["id"],
                r["status"],
                r["title"],
                r["company"],
                r["modality"],
                r["score"],
                json.loads(r["rejections"]),
                r["created_at"],
            )
            for r in rows
        ]

    async def promote_low_scores(self, min_score: int) -> int:
        """Al bajar el umbral, las ofertas con puntuación baja que ahora lo superan pasan
        a «por revisar» (resumen y /ofertas). Subir el umbral no oculta nada ya avisado."""
        cur = await self._db.conn.execute(
            "UPDATE offers SET status = 'pending_digest',"
            " updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')"
            " WHERE status = 'low_score' AND id IN"
            " (SELECT offer_id FROM matches GROUP BY offer_id HAVING MAX(score) >= ?)",
            (min_score,),
        )
        await self._db.conn.commit()
        return cur.rowcount

    async def counts_since(self, since: datetime) -> dict[str, int]:
        async with self._db.conn.execute(
            "SELECT status, COUNT(*) FROM offers WHERE created_at >= ? GROUP BY status",
            (since.isoformat(),),
        ) as cur:
            return {r[0]: r[1] for r in await cur.fetchall()}

    # --- puntuaciones ---------------------------------------------------------
    async def save_match(
        self,
        offer_id: int,
        profile_id: int,
        score: int,
        result: MatchResult,
        adjustments: list[Adjustment],
        channel: ChannelOption,
        channel_reason: str,
        options: Iterable[ChannelOption],
        model: str,
    ) -> None:
        await self._db.conn.execute(
            "INSERT INTO matches (offer_id, profile_id, score, llm_score, verdict,"
            " rule_adjustments, reasons, gaps, red_flags, pitch_points, channel, channel_target,"
            " channel_reason, channel_options, model) VALUES"
            " (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
            " ON CONFLICT (offer_id, profile_id) DO UPDATE SET score = excluded.score,"
            " llm_score = excluded.llm_score, verdict = excluded.verdict,"
            " rule_adjustments = excluded.rule_adjustments, reasons = excluded.reasons,"
            " gaps = excluded.gaps, red_flags = excluded.red_flags,"
            " pitch_points = excluded.pitch_points, channel = excluded.channel,"
            " channel_target = excluded.channel_target, channel_reason = excluded.channel_reason,"
            " channel_options = excluded.channel_options, model = excluded.model",
            (
                offer_id,
                profile_id,
                score,
                result.score,
                result.verdict,
                json.dumps(
                    [{"rule": a.rule, "delta": a.delta, "label": a.label} for a in adjustments],
                    ensure_ascii=False,
                ),
                json.dumps(result.reasons, ensure_ascii=False),
                json.dumps(result.gaps, ensure_ascii=False),
                json.dumps(result.red_flags, ensure_ascii=False),
                json.dumps(result.pitch_points, ensure_ascii=False),
                channel.kind,
                channel.target,
                channel_reason,
                json.dumps([o.to_dict() for o in options], ensure_ascii=False),
                model,
            ),
        )
        await self._db.conn.commit()

    async def match(self, offer_id: int) -> Match | None:
        """La puntuación más reciente de la oferta."""
        async with self._db.conn.execute(
            "SELECT * FROM matches WHERE offer_id = ? ORDER BY id DESC LIMIT 1", (offer_id,)
        ) as cur:
            row = await cur.fetchone()
        if row is None:
            return None
        options = [_option(o) for o in json.loads(row["channel_options"])]
        chosen = next(
            (o for o in options if o.kind == row["channel"] and o.target == row["channel_target"]),
            ChannelOption(row["channel"], row["channel_target"], row["channel"]),
        )
        return Match(
            id=row["id"],
            offer_id=row["offer_id"],
            profile_id=row["profile_id"],
            score=row["score"],
            llm_score=row["llm_score"],
            verdict=row["verdict"],
            adjustments=json.loads(row["rule_adjustments"]),
            reasons=json.loads(row["reasons"]),
            gaps=json.loads(row["gaps"]),
            red_flags=json.loads(row["red_flags"]),
            pitch_points=json.loads(row["pitch_points"]),
            channel=chosen,
            channel_reason=row["channel_reason"],
            options=options,
            model=row["model"],
        )

    async def set_channel(self, match_id: int, option: ChannelOption, reason: str) -> None:
        await self._db.conn.execute(
            "UPDATE matches SET channel = ?, channel_target = ?, channel_reason = ? WHERE id = ?",
            (option.kind, option.target, reason, match_id),
        )
        await self._db.conn.commit()

    # --- borradores -----------------------------------------------------------
    async def add_draft(
        self,
        offer_id: int,
        channel: ChannelOption,
        subject: str | None,
        body: str,
        origin: str,
        warnings: list[str],
        model: str | None,
        instruction: str | None = None,
    ) -> Draft:
        await self._db.conn.execute(
            "INSERT INTO drafts (offer_id, version, channel, target, subject, body, origin,"
            " instruction, warnings, model) VALUES (?, (SELECT COALESCE(MAX(version), 0) + 1"
            " FROM drafts WHERE offer_id = ?), ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                offer_id,
                offer_id,
                channel.kind,
                channel.target,
                subject,
                body,
                origin,
                instruction,
                json.dumps(warnings, ensure_ascii=False),
                model,
            ),
        )
        await self._db.conn.commit()
        draft = await self.latest_draft(offer_id)
        assert draft is not None
        return draft

    async def draft(self, draft_id: int) -> Draft | None:
        async with self._db.conn.execute("SELECT * FROM drafts WHERE id = ?", (draft_id,)) as cur:
            row = await cur.fetchone()
        return _draft(row) if row else None

    async def latest_draft(self, offer_id: int) -> Draft | None:
        async with self._db.conn.execute(
            "SELECT * FROM drafts WHERE offer_id = ? ORDER BY version DESC LIMIT 1", (offer_id,)
        ) as cur:
            row = await cur.fetchone()
        return _draft(row) if row else None

    async def update_analysis(self, offer_id: int, status: str, rejections: list[str]) -> None:
        await self._db.conn.execute(
            "UPDATE offers SET status = ?, rejections = ?,"
            " updated_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now') WHERE id = ?",
            (status, json.dumps(rejections, ensure_ascii=False), offer_id),
        )
        await self._db.conn.commit()

    async def extraction(self, offer_id: int) -> JobExtraction | None:
        async with self._db.conn.execute(
            "SELECT extraction FROM offers WHERE id = ?", (offer_id,)
        ) as cur:
            row = await cur.fetchone()
        return JobExtraction.model_validate_json(row[0]) if row else None

    async def ids_for_rescore(
        self, statuses: Iterable[str], since: datetime, limit: int
    ) -> list[int]:
        statuses = tuple(statuses)
        placeholders = ",".join("?" * len(statuses))
        async with self._db.conn.execute(
            f"SELECT id FROM offers WHERE status IN ({placeholders}) AND created_at >= ?"
            " ORDER BY id DESC LIMIT ?",
            (*statuses, since.isoformat(), limit),
        ) as cur:
            return [r[0] for r in await cur.fetchall()]

    async def set_dismiss_reason(self, offer_id: int, reason: str) -> None:
        await self._db.conn.execute(
            "UPDATE offers SET dismiss_reason = ? WHERE id = ?", (reason, offer_id)
        )
        await self._db.conn.commit()

    async def dismiss_reasons_since(self, since: datetime) -> dict[str, int]:
        async with self._db.conn.execute(
            "SELECT COALESCE(dismiss_reason, 'sin motivo'), COUNT(*) FROM offers"
            " WHERE status = 'dismissed' AND updated_at >= ? GROUP BY 1",
            (since.isoformat(),),
        ) as cur:
            return {r[0]: r[1] for r in await cur.fetchall()}

    async def totals(self) -> dict[str, int]:
        async with self._db.conn.execute(
            "SELECT status, COUNT(*) FROM offers GROUP BY status"
        ) as c:
            return {r[0]: r[1] for r in await c.fetchall()}

    # --- tarjetas -------------------------------------------------------------
    async def remember_message(self, bot_message_id: int, offer_id: int | None, kind: str) -> None:
        await self._db.conn.execute(
            "INSERT OR REPLACE INTO bot_messages (bot_message_id, offer_id, kind) VALUES (?, ?, ?)",
            (bot_message_id, offer_id, kind),
        )
        await self._db.conn.commit()

    async def card_messages(self, offer_id: int) -> list[int]:
        async with self._db.conn.execute(
            "SELECT bot_message_id FROM bot_messages WHERE offer_id = ? AND kind = 'card'"
            " ORDER BY bot_message_id DESC LIMIT 3",
            (offer_id,),
        ) as cur:
            return [r[0] for r in await cur.fetchall()]
