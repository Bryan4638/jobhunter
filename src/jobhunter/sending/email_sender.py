"""Email con el CV adjunto vía SMTP (Gmail con contraseña de aplicación)."""

from __future__ import annotations

import asyncio
from email.message import EmailMessage
from email.utils import formataddr, make_msgid, parseaddr
from pathlib import Path

import aiosmtplib

from jobhunter.config import Settings
from jobhunter.sending.telegram_sender import SendError


def sender_address(settings: Settings) -> tuple[str | None, str | None]:
    """(nombre, dirección) de ``SMTP_FROM`` (admite ``"Nombre <correo>"``) o ``SMTP_USER``."""
    name, address = parseaddr(settings.smtp_from or "")
    if not address or "@" not in address:
        name, address = None, settings.smtp_user
    return (name or None), address


def smtp_missing(settings: Settings) -> list[str]:
    missing = []
    if not settings.smtp_user:
        missing.append("SMTP_USER")
    if settings.smtp_password is None:
        missing.append("SMTP_PASSWORD")
    if not (settings.smtp_from or settings.smtp_user):
        missing.append("SMTP_FROM")
    return missing


def build_email(
    sender: str,
    sender_name: str | None,
    to: str,
    subject: str,
    body: str,
    attachment: bytes | None,
    filename: str,
) -> EmailMessage:
    msg = EmailMessage()
    msg["From"] = formataddr((sender_name, sender)) if sender_name else sender
    msg["To"] = to
    msg["Subject"] = subject
    msg["Message-ID"] = make_msgid(domain=sender.split("@")[-1])
    msg.set_content(body)
    if attachment is not None:
        msg.add_attachment(attachment, maintype="application", subtype="pdf", filename=filename)
    return msg


async def send_email(
    settings: Settings,
    to: str,
    subject: str,
    body: str,
    cv_path: Path | None,
    filename: str,
    sender_name: str | None,
    bcc_self: bool,
) -> str:
    """Envía y devuelve el Message-ID."""
    if missing := smtp_missing(settings):
        raise SendError(f"Falta configurar el email en .env: {', '.join(missing)}")
    from_name, sender = sender_address(settings)
    assert sender is not None and settings.smtp_password is not None
    attachment = await asyncio.to_thread(cv_path.read_bytes) if cv_path else None
    msg = build_email(sender, from_name or sender_name, to, subject, body, attachment, filename)
    recipients = [to] + ([sender] if bcc_self and sender.lower() != to.lower() else [])
    implicit_tls = settings.smtp_port == 465
    try:
        await aiosmtplib.send(
            msg,
            hostname=settings.smtp_host,
            port=settings.smtp_port,
            username=settings.smtp_user,
            password=settings.smtp_password.get_secret_value(),
            use_tls=implicit_tls,
            start_tls=not implicit_tls,
            recipients=recipients,
            timeout=60,
        )
    except aiosmtplib.SMTPAuthenticationError as exc:
        raise SendError(
            "Gmail rechazó el usuario o la contraseña. Usa una contraseña de aplicación "
            "(no tu contraseña normal)."
        ) from exc
    except aiosmtplib.SMTPRecipientsRefused as exc:
        raise SendError(f"El servidor rechazó la dirección {to}.") from exc
    except (aiosmtplib.SMTPException, OSError) as exc:
        raise SendError(f"No se pudo enviar el email: {exc}") from exc
    return str(msg["Message-ID"])
