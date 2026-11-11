"""/umbral: puntuación mínima para avisarte, editable desde el bot (ADR-011)."""

from __future__ import annotations

from html import escape

from telethon import Button, events
from telethon.errors import MessageNotModifiedError

from jobhunter.context import AppContext
from jobhunter.domain.preferences import Thresholds, parse_threshold_args
from jobhunter.telegram.auth import args_of, command, owner_callback, owner_message

PRESETS = (20, 40, 60, 80)
USAGE = (
    "Cámbialo con los botones o escribiendo:\n"
    "• <code>/umbral 50</code> — aviso inmediato desde 50\n"
    "• <code>/umbral 70 40</code> — aviso inmediato desde 70 y resumen diario desde 40"
)


def describe(t: Thresholds) -> str:
    lines = [f"🔔 <b>Aviso inmediato</b> (tarjeta al momento): desde <b>{t.immediate}</b> puntos"]
    if t.digest < t.immediate:
        lines.append(
            f"📋 <b>Resumen diario</b>: de <b>{t.digest}</b> a <b>{t.immediate - 1}</b> puntos"
        )
    else:
        lines.append("📋 <b>Resumen diario</b>: solo las ofertas pospuestas con ⏰")
    if t.digest > 0:
        lines.append(f"📉 Por debajo de {t.digest}: no se avisa (las ves en /descartadas)")
    return "\n".join(lines)


def buttons(current: Thresholds) -> list[list[Button]]:
    row = [
        Button.inline(f"{'✓ ' if p == current.immediate else ''}{p}", f"thr:{p}".encode())
        for p in PRESETS
    ]
    return [row]


async def apply(ctx: AppContext, new: Thresholds) -> str:
    await ctx.prefs.set_thresholds(new)
    promoted = await ctx.offers.promote_low_scores(new.digest)
    text = f"✅ <b>Umbral actualizado</b>\n\n{describe(new)}"
    if promoted:
        text += (
            f"\n\n📬 {promoted} ofertas ya analizadas superan el nuevo umbral: "
            "las tienes en /ofertas."
        )
    return text


def register(ctx: AppContext) -> None:
    bot = ctx.bot

    @bot.on(events.NewMessage(pattern=command("umbral"), func=owner_message(ctx.owner_id)))
    async def threshold(event: events.NewMessage.Event) -> None:
        current = await ctx.prefs.thresholds()
        args = args_of(event)
        if not args:
            await event.respond(
                f"🎯 <b>Umbral de puntuación</b>\n\n{describe(current)}\n\n{USAGE}",
                buttons=buttons(current),
            )
            return
        try:
            new = parse_threshold_args(args, current)
        except ValueError as exc:
            await event.respond(f"❌ {escape(str(exc))}\n\n{USAGE}")
            return
        await event.respond(await apply(ctx, new), buttons=buttons(new))

    ctx.menu_actions["umbral"] = threshold

    @bot.on(events.CallbackQuery(pattern=rb"^thr:\d+$", func=owner_callback(ctx.owner_id)))
    async def on_preset(event: events.CallbackQuery.Event) -> None:
        value = int(event.data.decode().split(":")[1])
        current = await ctx.prefs.thresholds()
        new = Thresholds(value, min(current.digest, value))
        text = await apply(ctx, new)
        await event.answer(f"Aviso desde {value} puntos")
        try:
            await event.edit(text, buttons=buttons(new))
        except MessageNotModifiedError:
            pass
