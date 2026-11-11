"""Comandos generales: /start, /ayuda, /estado + filtro de usuarios no autorizados."""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from html import escape

from telethon import events

from jobhunter import __version__
from jobhunter.config import effective_dry_run
from jobhunter.context import AppContext
from jobhunter.llm.gemini import LLMUsage
from jobhunter.telegram.auth import command, owner_callback, owner_message
from jobhunter.telegram.bot import BOT_COMMANDS
from jobhunter.telegram.handlers.menu import menu_buttons

log = logging.getLogger(__name__)

HELP = (
    "Todas las opciones están en /menu, con botones.\n\n"
    "<b>Comandos</b> (también puedes escribirlos; algunos aceptan argumentos)\n"
    "/menu — menú con todas las opciones\n"
    "/ofertas — ofertas por revisar, de mejor a peor puntuación\n"
    "/resumen — resumen de ofertas ahora (también llega cada día)\n"
    "/escanear [días] — analizar los últimos días de los grupos (por defecto 3)\n"
    "/descartadas — ofertas descartadas por tus reglas o con puntuación baja\n"
    "/contador — usuarios nuevos contactados hoy (límite diario) y últimos 7 días\n"
    "/aplicadas — historial de aplicaciones enviadas\n"
    "/stats — estadísticas y sugerencias según tus descartes\n"
    "/reevaluar [días] — volver a puntuar ofertas recientes (tras cambiar CV o preferencias)\n"
    "/pausa · /reanudar — pausar o reanudar los avisos inmediatos\n"
    "/cv — subir o reemplazar tu CV (también puedes enviarme el PDF directamente)\n"
    "/perfil — ver el perfil extraído de tu CV\n"
    "/grupos — ver, pausar o quitar los grupos y canales monitorizados\n"
    "/grupo_add &lt;@grupo | t.me/... | ID&gt; — añadir un grupo o canal\n"
    "   (también puedes <b>reenviarme un mensaje</b> de un canal)\n"
    "/salario — ver el salario mínimo · <code>/salario 1500 USD mes</code> · "
    "<code>/salario off</code>\n"
    "/umbral — puntuación mínima para avisarte · <code>/umbral 50</code>\n"
    "/estado — salud del bot y mensajes de hoy\n"
    "/cancelar — cancelar la acción en curso\n"
    "/ayuda — esta ayuda"
)


def fmt_dt(dt: datetime | None, ctx: AppContext) -> str:
    if dt is None:
        return "—"
    return dt.astimezone(ctx.settings.zoneinfo).strftime("%d/%m %H:%M")


def fmt_uptime(start: datetime) -> str:
    secs = int((datetime.now(UTC) - start).total_seconds())
    days, rem = divmod(secs, 86400)
    hours, rem = divmod(rem, 3600)
    mins = rem // 60
    return f"{days}d {hours}h {mins}m" if days else f"{hours}h {mins}m"


def start_of_today(ctx: AppContext) -> datetime:
    local = datetime.now(ctx.settings.zoneinfo).replace(hour=0, minute=0, second=0, microsecond=0)
    return local.astimezone(UTC)


def llm_issues(usage: LLMUsage, exhausted: list[str] | None = None) -> str:
    """Distingue los cortes temporales de Gemini (ya resueltos) de los fallos reales."""
    parts = []
    if usage.retries:
        parts.append(f"{usage.retries} reintentos por saturación")
    if usage.failures:
        parts.append(f"⚠️ {usage.failures} fallos")
    if exhausted:
        parts.append(f"cuota diaria agotada en {', '.join(exhausted)} (uso el respaldo)")
    return "".join(f" · {p}" for p in parts)


async def queue_line(ctx: AppContext) -> str:
    pending = await ctx.raw_messages.count_pending()
    if ctx.worker is None:
        return f"⏸ En cola: {pending} · análisis desactivado (falta GEMINI_API_KEY)"
    if ctx.worker.quota_paused and pending:
        return f"⏸ En cola: {pending} · cuota diaria de Gemini agotada, reintento cada 15 min"
    if ctx.worker.waiting_for_cv and pending:
        return f"⏸ En cola: {pending} · esperando a que confirmes tu CV (/cv)"
    if pending or ctx.worker.busy:
        return f"⏳ En cola de análisis: {pending}"
    return "✅ Cola de análisis vacía"


async def cv_line(ctx: AppContext) -> str:
    if ctx.profiles is None:
        return "⚠️ CV: falta GEMINI_API_KEY en .env"
    active = await ctx.profiles.active()
    if active is None:
        return "⚠️ CV: aún no lo has subido — envíame el PDF (/cv)"
    return f"✅ CV: {escape(active.profile.full_name)} — {escape(active.profile.headline)}"


def register(ctx: AppContext) -> None:
    bot = ctx.bot
    is_owner = owner_message(ctx.owner_id)
    known = {name for name, _ in BOT_COMMANDS}

    @bot.on(events.NewMessage(pattern=command("start"), func=is_owner))
    async def start(event: events.NewMessage.Event) -> None:
        n_groups = len(ctx.registry)
        salary = await ctx.prefs.min_salary()
        limits = await ctx.prefs.thresholds()
        groups_line = (
            f"✅ Grupos monitorizados: {n_groups}"
            if n_groups
            else "⚠️ Grupos monitorizados: 0 — añade uno con /grupo_add"
        )
        dry = (
            "🧪 Modo simulación: <b>activo</b> (no se enviará nada a reclutadores)"
            if effective_dry_run(ctx.settings, ctx.config)
            else "📤 Modo simulación: desactivado"
        )
        await event.respond(
            "👋 Hola, soy <b>JobHunter</b>.\n\n"
            f"✅ Cuenta conectada: {escape(ctx.state.me_display)}\n"
            f"{groups_line}\n"
            f"{await cv_line(ctx)}\n"
            f"💰 Salario mínimo: {salary.label() if salary else 'desactivado'}\n"
            f"🎯 Aviso desde: {limits.immediate} puntos (/umbral)\n"
            f"{dry}\n\n"
            "Elige una opción 👇 (vuelve a este menú cuando quieras con /menu)",
            buttons=await menu_buttons(ctx),
        )

    @bot.on(events.NewMessage(pattern=command("ayuda"), func=is_owner))
    async def help_(event: events.NewMessage.Event) -> None:
        await event.respond(HELP)

    ctx.menu_actions["ayuda"] = help_

    @bot.on(events.NewMessage(pattern=command("estado"), func=is_owner))
    async def status(event: events.NewMessage.Event) -> None:
        st = ctx.state
        connected = "🟢 conectado" if ctx.userbot.is_connected() else "🔴 desconectado"
        today = start_of_today(ctx)
        counts = await ctx.raw_messages.counts_since(today)
        saved = sum(counts.values())
        errors = counts.get("error", 0)
        offers = await ctx.offers.counts_since(today)
        paused = "en pausa (/reanudar)" if await ctx.prefs.paused() else "activos"
        llm = (
            f"🤖 IA: {ctx.llm.usage.calls} llamadas · {ctx.llm.usage.tokens:,} tokens"
            f"{llm_issues(ctx.llm.usage, ctx.llm.exhausted_models())} (desde el arranque)"
            if ctx.llm
            else "🤖 IA: desactivada (falta GEMINI_API_KEY)"
        )
        await event.respond(
            f"<b>Estado</b> · v{__version__}\n"
            f"⏱ En marcha: {fmt_uptime(st.started_at)}\n"
            f"👤 Tu cuenta: {connected} ({escape(st.me_display)})\n"
            f"👥 Chats monitorizados: {len(ctx.registry)}\n"
            f"🕒 Último mensaje: {fmt_dt(st.last_message_at, ctx)}"
            f" · recuperados al arrancar: {st.caught_up}\n"
            f"{await cv_line(ctx)}\n\n"
            f"<b>Mensajes de hoy:</b> {saved}\n"
            f"• 🚫 filtrados: {counts.get('filtered', 0)}\n"
            f"• ♻️ duplicados: {counts.get('duplicate', 0)}\n"
            f"• 🗒 no eran ofertas: {counts.get('not_job', 0)}\n"
            f"• ✅ analizados: {counts.get('processed', 0)}"
            f"{f' · ❌ con error: {errors}' if errors else ''}\n"
            f"{await queue_line(ctx)}\n\n"
            f"<b>Ofertas de hoy:</b> {sum(offers.values())}\n"
            f"• 🔥 notificadas: {offers.get('notified', 0)}"
            f" · 📋 para el resumen: {offers.get('pending_digest', 0)}\n"
            f"• 🚫 descartadas por reglas: {offers.get('rejected_rules', 0)}"
            f" · 📉 puntuación baja: {offers.get('low_score', 0)}\n\n"
            f"{llm}\n"
            f"⏸ Avisos inmediatos: {paused}\n"
            f"🧪 Simulación: {'sí' if effective_dry_run(ctx.settings, ctx.config) else 'no'}"
        )

    ctx.menu_actions["estado"] = status

    @bot.on(events.NewMessage(pattern=r"^/(\w+)", func=is_owner))
    async def unknown(event: events.NewMessage.Event) -> None:
        name = event.pattern_match.group(1).lower()
        if name not in known:
            await event.respond("No conozco ese comando. Usa /menu.")

    # --- Cualquier otra persona: silencio + log ---------------------------------
    owner = ctx.owner_id

    @bot.on(events.NewMessage(func=lambda e: e.is_private and e.sender_id != owner))
    async def unauthorized(event: events.NewMessage.Event) -> None:
        log.warning("Mensaje ignorado de usuario no autorizado: %s", event.sender_id)

    is_owner_cb = owner_callback(owner)

    @bot.on(events.CallbackQuery(func=lambda e: not is_owner_cb(e)))
    async def unauthorized_cb(event: events.CallbackQuery.Event) -> None:
        log.warning("Callback ignorado de usuario no autorizado: %s", event.sender_id)
        await event.answer()
