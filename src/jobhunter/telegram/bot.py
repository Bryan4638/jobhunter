"""Cliente Telethon del bot de control (tu interfaz)."""

from __future__ import annotations

from telethon import TelegramClient
from telethon.tl.functions.bots import SetBotCommandsRequest
from telethon.tl.types import BotCommand, BotCommandScopeDefault

from jobhunter.config import Settings

# En la lista «/» de Telegram solo aparece /menu; el resto se abre con sus botones
# (y se puede seguir escribiendo, p. ej. «/salario 1500 USD mes»).
PUBLISHED_COMMANDS: list[tuple[str, str]] = [("menu", "Menú con todas las opciones")]

# Todos los comandos que el bot entiende escritos a mano.
BOT_COMMANDS: list[tuple[str, str]] = [
    ("start", "Estado y primeros pasos"),
    ("menu", "Menú con todas las opciones"),
    ("ofertas", "Ofertas por revisar"),
    ("resumen", "Resumen de ofertas ahora"),
    ("escanear", "Analizar los últimos días de los grupos"),
    ("descartadas", "Ofertas descartadas automáticamente"),
    ("contador", "Usuarios nuevos contactados hoy"),
    ("aplicadas", "Historial de aplicaciones"),
    ("stats", "Estadísticas"),
    ("reevaluar", "Volver a puntuar ofertas recientes"),
    ("pausa", "Pausar los avisos inmediatos"),
    ("reanudar", "Reanudar los avisos inmediatos"),
    ("cv", "Subir o reemplazar tu CV (PDF)"),
    ("perfil", "Ver tu perfil extraído del CV"),
    ("grupos", "Grupos y canales monitorizados"),
    ("grupo_add", "Añadir un grupo o canal"),
    ("salario", "Ver o cambiar el salario mínimo"),
    ("umbral", "Puntuación mínima para avisarte"),
    ("estado", "Salud del bot"),
    ("cancelar", "Cancelar la acción en curso"),
    ("ayuda", "Ayuda"),
]


def create_bot(settings: Settings) -> TelegramClient:
    settings.bot_session_name.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    return TelegramClient(
        str(settings.bot_session_name),
        settings.tg_api_id,
        settings.tg_api_hash.get_secret_value(),
    )


async def start_bot(bot: TelegramClient, settings: Settings) -> None:
    assert settings.bot_token is not None
    await bot.start(bot_token=settings.bot_token.get_secret_value())
    bot.parse_mode = "html"


async def publish_commands(bot: TelegramClient) -> None:
    await bot(
        SetBotCommandsRequest(
            scope=BotCommandScopeDefault(),
            lang_code="",
            commands=[BotCommand(command=c, description=d) for c, d in PUBLISHED_COMMANDS],
        )
    )
