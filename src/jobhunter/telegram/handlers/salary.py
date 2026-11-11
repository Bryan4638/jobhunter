"""/salario: salario mínimo editable desde el bot (desactivado por defecto)."""

from __future__ import annotations

from html import escape

from telethon import Button, events

from jobhunter.context import AppContext
from jobhunter.domain.preferences import parse_salary_args
from jobhunter.telegram.auth import args_of, command, owner_callback, owner_message

RULE_LABEL = {
    "discard": "descartar las ofertas con salario menor",
    "penalize": "solo bajar la puntuación de las ofertas con salario menor",
}
USAGE = (
    "Uso:\n"
    "• <code>/salario 1500 USD mes</code> (también <code>hora</code> o <code>año</code>)\n"
    "• <code>/salario off</code> para desactivarlo\n\n"
    "Solo afecta a las ofertas que indiquen salario; las que no lo dicen no se penalizan."
)


def register(ctx: AppContext) -> None:
    bot = ctx.bot

    @bot.on(events.NewMessage(pattern=command("salario"), func=owner_message(ctx.owner_id)))
    async def salary(event: events.NewMessage.Event) -> None:
        args = args_of(event)
        if not args:
            current = await ctx.prefs.min_salary()
            if current is None:
                text = "💰 Salario mínimo: <b>desactivado</b> (se acepta cualquier salario)."
            else:
                rule = await ctx.prefs.salary_rule()
                text = f"💰 Salario mínimo: <b>{current.label()}</b>\nRegla: {RULE_LABEL[rule]}."
            await event.respond(f"{text}\n\n{USAGE}")
            return

        try:
            parsed = parse_salary_args(args)
        except ValueError as exc:
            await event.respond(f"❌ {escape(str(exc))}\n\n{USAGE}")
            return

        await ctx.prefs.set_min_salary(parsed)
        if parsed is None:
            await event.respond("✅ Salario mínimo desactivado: se acepta cualquier salario.")
            return
        await event.respond(
            f"✅ Salario mínimo: <b>{parsed.label()}</b>\n"
            "¿Qué hago con las ofertas que indiquen un salario menor?",
            buttons=[
                [Button.inline("🗑 Descartarlas", b"sal:discard")],
                [Button.inline("📉 Solo bajar su puntuación", b"sal:penalize")],
            ],
        )

    ctx.menu_actions["salario"] = salary

    @bot.on(events.CallbackQuery(pattern=rb"^sal:", func=owner_callback(ctx.owner_id)))
    async def on_rule(event: events.CallbackQuery.Event) -> None:
        rule = event.data.decode().split(":", 1)[1]
        if rule not in RULE_LABEL:
            await event.answer()
            return
        await ctx.prefs.set_salary_rule(rule)  # type: ignore[arg-type]
        current = await ctx.prefs.min_salary()
        label = current.label() if current else "desactivado"
        await event.edit(f"✅ Salario mínimo: <b>{label}</b>\nRegla: {RULE_LABEL[rule]}.")
        await event.answer("Guardado")
