"""Validación literal de contactos (docs/03 «Selección de canal», docs/08 §4 capa 3).

Un email, @usuario o enlace solo es válido si **aparece literalmente** en el mensaje
original (texto + enlaces ocultos). Lo que la IA "invente" o "corrija" se descarta.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from jobhunter.domain.schemas import Contacts

_EMAIL = re.compile(r"^[\w.+-]+@[\w-]+(?:\.[\w-]+)+$")
_USERNAME = re.compile(r"^[A-Za-z][A-Za-z0-9_]{3,31}$")
_URL = re.compile(r"^(?:https?://)?[\w-]+(?:\.[\w-]+)+(?:/\S*)?$", re.IGNORECASE)


@dataclass(frozen=True, slots=True)
class ValidContacts:
    emails: tuple[str, ...] = ()
    usernames: tuple[str, ...] = ()  # sin @
    urls: tuple[str, ...] = ()
    rejected: tuple[str, ...] = ()  # lo que la IA devolvió y no aparece en el texto

    @property
    def any(self) -> bool:
        return bool(self.emails or self.usernames or self.urls)


def _strip_url(url: str) -> str:
    return re.sub(r"^(?:https?://)?(?:www\.)?", "", url.strip().lower()).rstrip("/.,;")


def validate_contacts(contacts: Contacts, text: str, hidden_urls: tuple[str, ...]) -> ValidContacts:
    source = (text + "\n" + "\n".join(hidden_urls)).lower()
    emails: list[str] = []
    usernames: list[str] = []
    urls: list[str] = []
    rejected: list[str] = []

    for raw in contacts.emails:
        email = raw.strip().strip("<>.,;").removeprefix("mailto:")
        if _EMAIL.match(email) and email.lower() in source:
            emails.append(email)
        else:
            rejected.append(raw)

    for raw in contacts.telegram_usernames:
        name = raw.strip().removeprefix("@")
        name = re.sub(r"^(?:https?://)?t\.me/", "", name, flags=re.IGNORECASE).strip("/")
        low = name.lower()
        literal = re.search(rf"(?:@|t\.me/){re.escape(low)}\b", source) is not None
        if _USERNAME.match(name) and literal:
            usernames.append(name)
        else:
            rejected.append(raw)

    for raw in contacts.urls:
        core = _strip_url(raw)
        if _URL.match(raw.strip()) and core and core in source:
            # Un enlace t.me/<usuario> es un DM, no un formulario.
            m = re.fullmatch(r"t\.me/([A-Za-z][A-Za-z0-9_]{3,31})", core)
            if m:
                usernames.append(m.group(1))
            else:
                urls.append(raw.strip())
        else:
            rejected.append(raw)

    def uniq(items: list[str]) -> tuple[str, ...]:
        seen: dict[str, str] = {}
        for item in items:
            seen.setdefault(item.lower(), item)
        return tuple(seen.values())

    return ValidContacts(uniq(emails), uniq(usernames), uniq(urls), tuple(rejected))
