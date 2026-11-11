"""Elige el canal de aplicación de forma determinista (docs/03 «Selección de canal»).

Entrada: contactos **ya validados** literalmente, el método que pide la oferta y el
autor del mensaje. La IA nunca decide el destinatario.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Literal

from jobhunter.analysis.contact_validator import ValidContacts

ChannelKind = Literal["email", "telegram_dm", "manual"]


@dataclass(frozen=True, slots=True)
class ChannelOption:
    kind: ChannelKind
    target: str | None  # email · "@usuario" · "id:<user_id>" · URL · None (manual sin enlace)
    label: str  # para botones y tarjeta

    def to_dict(self) -> dict[str, str | None]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class Author:
    user_id: int | None
    username: str | None
    name: str | None
    is_user: bool  # False si es un canal, un admin anónimo o un bot


@dataclass(frozen=True, slots=True)
class ChannelChoice:
    chosen: ChannelOption
    reason: str
    options: tuple[ChannelOption, ...]


def is_bot_username(name: str) -> bool:
    return name.lower().endswith("bot")


def build_options(
    contacts: ValidContacts, author: Author, allow_author: bool
) -> tuple[list[ChannelOption], list[ChannelOption], list[ChannelOption], ChannelOption | None]:
    emails = [ChannelOption("email", e, f"📧 {e}") for e in contacts.emails]
    dms = [
        ChannelOption("telegram_dm", f"@{u}", f"💬 @{u}")
        for u in contacts.usernames
        if not is_bot_username(u)
    ]
    manual = [ChannelOption("manual", u, "🔗 Formulario / enlace") for u in contacts.urls]
    manual += [
        ChannelOption("manual", f"https://t.me/{u}", f"🤖 Bot @{u}")
        for u in contacts.usernames
        if is_bot_username(u)
    ]
    author_opt = None
    if allow_author and author.is_user and (author.username or author.user_id):
        target = f"@{author.username}" if author.username else f"id:{author.user_id}"
        who = f"@{author.username}" if author.username else (author.name or "autor")
        if not any(o.target and o.target.lower() == target.lower() for o in dms):
            author_opt = ChannelOption("telegram_dm", target, f"💬 Autor del post ({who})")
    return emails, dms, manual, author_opt


def select_channel(
    contacts: ValidContacts,
    stated: str,
    instructions: str | None,
    author: Author,
    preferred: Literal["telegram_dm", "email"],
    allow_author: bool = True,
) -> ChannelChoice:
    emails, dms, manual, author_opt = build_options(contacts, author, allow_author)
    options = [*emails, *dms, *([author_opt] if author_opt else []), *manual]
    fallback = ChannelOption("manual", None, "🔗 Manual")
    if not manual:
        options.append(fallback)  # siempre se puede aplicar a mano
    quote = f' — la oferta dice: "{instructions}"' if instructions else ""

    def choice(opt: ChannelOption, reason: str) -> ChannelChoice:
        return ChannelChoice(opt, reason, tuple(dict.fromkeys(options)))

    # 1. Método explícito de la oferta.
    if stated == "email" and emails:
        return choice(emails[0], f"la oferta pide aplicar por email{quote}")
    if stated == "telegram_dm":
        if dms:
            return choice(dms[0], f"la oferta pide escribir por Telegram{quote}")
        if author_opt:
            return choice(author_opt, f"la oferta pide escribir por privado al autor{quote}")
    if stated == "form" and manual:
        return choice(manual[0], f"la oferta se aplica por formulario o enlace{quote}")

    # 2. Según los contactos disponibles.
    if emails and dms:
        if preferred == "email":
            return choice(emails[0], "la oferta da email y Telegram; prefieres email")
        return choice(dms[0], "la oferta da email y Telegram; prefieres DM")
    if emails:
        return choice(emails[0], "el único contacto de la oferta es un email")
    if dms:
        return choice(dms[0], "el único contacto de la oferta es un usuario de Telegram")
    if manual:
        return choice(manual[0], "la oferta solo da un enlace para aplicar")
    if author_opt:
        return choice(author_opt, "la oferta no da contacto; se escribe al autor del post")
    return choice(fallback, "la oferta no da ningún contacto utilizable")
