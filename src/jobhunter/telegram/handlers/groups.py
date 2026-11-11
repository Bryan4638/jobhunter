"""Gestión de grupos/canales monitorizados: única vía para configurarlos (ADR-011).

Callbacks (``data``):
    g:list            lista
    g:t:<id>          pausar / reanudar
    g:d:<id>          pedir confirmación para quitar
    g:x:<id>          quitar
    g:pick:<page>     elegir entre los chats de tu cuenta
    g:a:<id>:<page>   añadir (page = -1 si viene de un reenvío)
"""

from __future__ import annotations

import logging
from html import escape

from telethon import Button, events, utils
from telethon.errors import MessageNotModifiedError, RPCError
from telethon.tl.types import PeerChannel

from jobhunter.context import AppContext
from jobhunter.telegram.auth import args_of, command, owner_callback, owner_message
from jobhunter.telegram.chat_ref import InviteLinkError
from jobhunter.telegram.userbot import (
    ChatResolveError,
    ResolvedChat,
    list_candidate_chats,
    resolve_chat,
)

log = logging.getLogger(__name__)

PAGE_SIZE = 8
KIND_ICON = {"channel": "📢", "group": "👥"}
ADD_USAGE = (
    "Para añadir un grupo o canal:\n"
    "• <code>/grupo_add @nombre</code> o <code>/grupo_add https://t.me/nombre</code>\n"
    "• reenvíame un mensaje de un canal\n"
    "• o elige entre los chats de tu cuenta 👇\n\n"
    "Tu cuenta debe ser miembro del grupo o canal."
)


def _short(text: str, n: int = 28) -> str:
    return text if len(text) <= n else text[: n - 1] + "…"


async def render_list(ctx: AppContext) -> tuple[str, list[list[Button]]]:
    chats = await ctx.sources.list()
    buttons: list[list[Button]] = []
    if not chats:
        text = "📭 <b>No hay grupos monitorizados.</b>\n\n" + ADD_USAGE
    else:
        active = sum(c.enabled for c in chats)
        lines = [f"<b>Grupos y canales</b> · {active} activos de {len(chats)}\n"]
        for c in chats:
            status = "▶️" if c.enabled else "⏸"
            user = f" (@{escape(c.username)})" if c.username else ""
            lines.append(f"{status} {KIND_ICON[c.kind]} {escape(c.title)}{user}")
            buttons.append(
                [
                    Button.inline(
                        ("⏸ Pausar " if c.enabled else "▶️ Reanudar ") + _short(c.title, 22),
                        f"g:t:{c.id}".encode(),
                    ),
                    Button.inline("🗑", f"g:d:{c.id}".encode()),
                ]
            )
        text = "\n".join(lines)
    buttons.append([Button.inline("➕ Añadir desde mis chats", b"g:pick:0")])
    return text, buttons


async def render_picker(ctx: AppContext, page: int) -> tuple[str, list[list[Button]]]:
    monitored = {c.id for c in await ctx.sources.list()}
    candidates = [c for c in await list_candidate_chats(ctx.userbot) if c.id not in monitored]
    back = [Button.inline("« Volver", b"g:list")]
    if not candidates:
        return (
            "No quedan grupos ni canales de tu cuenta por añadir.\n"
            "Únete a uno desde Telegram y vuelve a intentarlo.",
            [back],
        )
    pages = (len(candidates) - 1) // PAGE_SIZE + 1
    page = max(0, min(page, pages - 1))
    chunk = candidates[page * PAGE_SIZE : (page + 1) * PAGE_SIZE]
    buttons = [
        [Button.inline(f"{KIND_ICON[c.kind]} {_short(c.title, 40)}", f"g:a:{c.id}:{page}".encode())]
        for c in chunk
    ]
    nav = []
    if page > 0:
        nav.append(Button.inline("◀️", f"g:pick:{page - 1}".encode()))
    if page < pages - 1:
        nav.append(Button.inline("▶️", f"g:pick:{page + 1}".encode()))
    if nav:
        buttons.append(nav)
    buttons.append(back)
    return f"Elige qué monitorizar (página {page + 1}/{pages}):", buttons


async def add_source(ctx: AppContext, chat: ResolvedChat) -> bool:
    """Guarda el chat y fija el cursor en su último mensaje (base del catch-up)."""
    is_new = await ctx.sources.add(chat.id, chat.title, chat.username, chat.kind)
    existing = await ctx.sources.get(chat.id)
    if existing is not None and existing.last_message_id is None:
        try:
            latest = await ctx.userbot.get_messages(chat.id, limit=1)
            if latest:
                await ctx.sources.touch(chat.id, latest[0].id, latest[0].date)
        except RPCError as exc:
            log.warning("No pude leer el último mensaje de %s: %s", chat.title, exc)
    await ctx.registry.refresh()
    return is_new


async def _edit(event: events.CallbackQuery.Event, text: str, buttons: list[list[Button]]) -> None:
    try:
        await event.edit(text, buttons=buttons)
    except MessageNotModifiedError:
        pass


def register(ctx: AppContext) -> None:
    bot = ctx.bot
    is_owner = owner_message(ctx.owner_id)

    @bot.on(events.NewMessage(pattern=command("grupos"), func=is_owner))
    async def list_groups(event: events.NewMessage.Event) -> None:
        text, buttons = await render_list(ctx)
        await event.respond(text, buttons=buttons)

    ctx.menu_actions["grupos"] = list_groups

    @bot.on(events.NewMessage(pattern=command("grupo_add"), func=is_owner))
    async def add_group(event: events.NewMessage.Event) -> None:
        ref = args_of(event)
        if not ref:
            await event.respond(
                ADD_USAGE, buttons=[[Button.inline("➕ Añadir desde mis chats", b"g:pick:0")]]
            )
            return
        try:
            chat = await resolve_chat(ctx.userbot, ref.strip())
        except (ChatResolveError, InviteLinkError, ValueError) as exc:
            await event.respond(f"❌ {escape(str(exc))}")
            return
        is_new = await add_source(ctx, chat)
        verb = "Añadido" if is_new else "Ya estaba en la lista (reactivado)"
        await event.respond(
            f"✅ {verb}: {KIND_ICON[chat.kind]} <b>{escape(chat.title)}</b>\n"
            f"Monitorizando {len(ctx.registry)} chats."
        )

    ctx.menu_actions["grupo_add"] = add_group

    @bot.on(
        events.NewMessage(
            # Los PDF reenviados se tratan como CV (handlers/cv.py), no como chat a añadir.
            func=lambda e: (
                is_owner(e) and e.message.fwd_from is not None and e.message.document is None
            )
        )
    )
    async def forwarded(event: events.NewMessage.Event) -> None:
        peer = event.message.fwd_from.from_id
        if not isinstance(peer, PeerChannel):
            await event.respond(
                "Telegram no indica de qué grupo viene este mensaje (pasa con los mensajes "
                "de miembros de un grupo). Usa /grupos → «Añadir desde mis chats»."
            )
            return
        chat_id = utils.get_peer_id(peer)
        if (existing := await ctx.sources.get(chat_id)) and existing.enabled:
            await event.respond(f"Ya monitorizo <b>{escape(existing.title)}</b>.")
            return
        try:
            chat = await resolve_chat(ctx.userbot, chat_id)
        except ChatResolveError as exc:
            await event.respond(f"❌ {escape(str(exc))}")
            return
        await event.respond(
            f"Detecté {KIND_ICON[chat.kind]} <b>{escape(chat.title)}</b>. ¿Lo monitorizo?",
            buttons=[[Button.inline("➕ Monitorizar este chat", f"g:a:{chat.id}:-1".encode())]],
        )

    @bot.on(events.CallbackQuery(pattern=rb"^g:", func=owner_callback(ctx.owner_id)))
    async def on_callback(event: events.CallbackQuery.Event) -> None:
        parts = event.data.decode().split(":")
        action = parts[1]

        if action == "list":
            await _edit(event, *(await render_list(ctx)))
            await event.answer()

        elif action == "t":
            chat_id = int(parts[2])
            chat = await ctx.sources.get(chat_id)
            if chat:
                await ctx.sources.set_enabled(chat_id, not chat.enabled)
                await ctx.registry.refresh()
                await event.answer("▶️ Reanudado" if not chat.enabled else "⏸ Pausado")
            else:
                await event.answer("Ya no existe")
            await _edit(event, *(await render_list(ctx)))

        elif action == "d":
            chat = await ctx.sources.get(int(parts[2]))
            if not chat:
                await event.answer("Ya no existe")
                await _edit(event, *(await render_list(ctx)))
                return
            await _edit(
                event,
                f"¿Dejar de monitorizar <b>{escape(chat.title)}</b>?",
                [[
                    Button.inline("🗑 Sí, quitar", f"g:x:{chat.id}".encode()),
                    Button.inline("Cancelar", b"g:list"),
                ]],
            )  # fmt: skip
            await event.answer()

        elif action == "x":
            await ctx.sources.remove(int(parts[2]))
            await ctx.registry.refresh()
            await event.answer("🗑 Quitado")
            await _edit(event, *(await render_list(ctx)))

        elif action == "pick":
            await event.answer("Cargando tus chats…")
            await _edit(event, *(await render_picker(ctx, int(parts[2]))))

        elif action == "a":
            chat_id, page = int(parts[2]), int(parts[3])
            try:
                chat = await resolve_chat(ctx.userbot, chat_id)
            except ChatResolveError as exc:
                await event.answer(str(exc)[:190], alert=True)
                return
            await add_source(ctx, chat)
            await event.answer(f"✅ Añadido: {chat.title}"[:190])
            if page < 0:
                await _edit(
                    event,
                    f"✅ Monitorizando {KIND_ICON[chat.kind]} <b>{escape(chat.title)}</b>",
                    [],
                )
            else:
                await _edit(event, *(await render_picker(ctx, page)))
        else:
            await event.answer()
