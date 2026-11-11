"""Tareas programadas (zona horaria ``TZ``):

- resumen diario a ``digest.time`` y, a la misma hora, recordatorios de seguimiento;
- a medianoche, el mensaje fijado con el contador vuelve a 0/10.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, time, timedelta
from html import escape

from telethon import Button

from jobhunter.context import AppContext
from jobhunter.telegram.handlers.offers import send_digest

log = logging.getLogger(__name__)
MAX_FOLLOW_UPS = 10


def next_run(now: datetime, at: time) -> datetime:
    """Próxima ocurrencia de ``at`` (en la zona de ``now``), estrictamente posterior."""
    candidate = now.replace(hour=at.hour, minute=at.minute, second=0, microsecond=0)
    if candidate <= now:
        candidate += timedelta(days=1)
    return candidate


async def daily(ctx: AppContext, at: time, job: Callable[[], Awaitable[object]], name: str) -> None:
    while True:
        now = datetime.now(ctx.settings.zoneinfo)
        await asyncio.sleep((next_run(now, at) - now).total_seconds())
        try:
            await job()
        except Exception:
            log.exception("Error en la tarea programada «%s»", name)


async def send_follow_ups(ctx: AppContext) -> int:
    days = ctx.config.sending.follow_up_days
    if days <= 0:
        return 0
    due = await ctx.applications.follow_ups_due(datetime.now(UTC) - timedelta(days=days))
    for app in due[:MAX_FOLLOW_UPS]:
        channel = "DM" if app.channel == "telegram_dm" else "email"
        await ctx.bot.send_message(
            ctx.owner_id,
            f"⏰ <b>Seguimiento</b>: hace {days} días aplicaste a "
            f"«{escape(app.offer_title or 'oferta')}» por {channel} a {escape(app.target)} "
            "y no he visto respuesta. Quizá merece un mensaje breve de seguimiento.",
            buttons=[[Button.inline("Ver oferta", f"of:v:{app.offer_id}")]],
        )
        await ctx.applications.update(app.id, follow_up_done=1)
    return len(due)


def start_jobs(ctx: AppContext) -> list[asyncio.Task[None]]:
    tasks = [
        asyncio.create_task(
            daily(ctx, time(0, 0), ctx.status_board.refresh, "contador de medianoche")
        )
    ]
    hour, minute = map(int, ctx.config.digest.time.split(":"))
    at = time(hour, minute)

    async def morning() -> None:
        if ctx.config.digest.enabled:
            await send_digest(ctx)
        await send_follow_ups(ctx)

    tasks.append(asyncio.create_task(daily(ctx, at, morning, "resumen y seguimientos")))
    return tasks
