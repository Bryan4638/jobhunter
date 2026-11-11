"""/menu: todas las opciones del bot en botones.

Cada botón (``mn:<comando>``) ejecuta exactamente el mismo handler que el comando escrito,
registrado en ``ctx.menu_actions``. Los comandos siguen funcionando si se escriben (útil con
argumentos, p. ej. ``/salario 1500 USD mes``), pero en la lista «/» de Telegram solo se
publica /menu (``telegram/bot.py``).
"""

from __future__ import annotations

import logging

from telethon import Button, events
from telethon.errors import MessageNotModifiedError

from jobhunter.context import AppContext
from jobhunter.storage.offers import OPEN_STATUSES
from jobhunter.telegram.auth import command, owner_callback, owner_message

log = logging.getLogger(__name__)

# (texto del botón, comando). La fila de pausa se añade según el estado actual.
LAYOUT: list[list[tuple[str, str]]] = [
    [("💼 Ofertas", "ofertas"), ("📋 Resumen", "resumen")],
    [("🔎 Escanear grupos", "escanear"), ("🚫 Descartadas", "descartadas")],
    [("👤 Contador de hoy", "contador"), ("📨 Aplicadas", "aplicadas")],
    [("📈 Estadísticas", "stats"), ("🔁 Reevaluar", "reevaluar")],
    [("📄 Subir CV", "cv"), ("🧑 Mi perfil", "perfil")],
    [("👥 Grupos", "grupos"), ("➕ Añadir grupo", "grupo_add")],
    [("💰 Salario mínimo", "salario"), ("🎯 Umbral", "umbral")],
    [("🩺 Estado del bot", "estado"), ("❓ Ayuda", "ayuda")],
]


async def menu_buttons(ctx: AppContext) -> list[list[Button]]:
    rows = [[Button.inline(label, f"mn:{name}") for label, name in row] for row in LAYOUT]
    if await ctx.prefs.paused():
        pause = Button.inline("▶️ Reanudar avisos", b"mn:reanudar")
    else:
        pause = Button.inline("⏸ Pausar avisos", b"mn:pausa")
    rows.insert(4, [pause])
    return rows


async def menu_text(ctx: AppContext) -> str:
    to_review = len(await ctx.offers.summaries(OPEN_STATUSES, 100))
    counter = await ctx.dispatcher.counter()
    limits = await ctx.prefs.thresholds()
    paused = " · ⏸ avisos en pausa" if await ctx.prefs.paused() else ""
    return (
        "🧭 <b>Menú</b>\n"
        f"💼 Por revisar: {to_review} · 👤 Nuevos hoy: {counter.new_today}/{counter.limit} "
        f"{counter.indicator}\n"
        f"🎯 Aviso desde {limits.immediate} puntos{paused}"
    )


def register(ctx: AppContext) -> None:
    bot = ctx.bot

    @bot.on(events.NewMessage(pattern=command("menu"), func=owner_message(ctx.owner_id)))
    async def menu(event: events.NewMessage.Event) -> None:
        await event.respond(await menu_text(ctx), buttons=await menu_buttons(ctx))

    @bot.on(events.CallbackQuery(pattern=rb"^mn:(\w+)$", func=owner_callback(ctx.owner_id)))
    async def on_button(event: events.CallbackQuery.Event) -> None:
        name = event.pattern_match.group(1).decode()
        action = ctx.menu_actions.get(name)
        if action is None:
            await event.answer("Opción no disponible", alert=True)
            return
        await event.answer()
        await action(event)
        if name in ("pausa", "reanudar"):
            try:
                await event.edit(await menu_text(ctx), buttons=await menu_buttons(ctx))
            except MessageNotModifiedError:
                pass
