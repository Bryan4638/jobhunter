"""Composición de la aplicación: crea clientes, servicios y handlers, y arranca el loop."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import signal

from telethon import TelegramClient
from telethon.errors import RPCError

from jobhunter.analysis.pipeline import AnalysisWorker, OfferAnalyzer
from jobhunter.analysis.writer import DraftWriter
from jobhunter.config import AppConfig, Settings, effective_dry_run
from jobhunter.context import AppContext
from jobhunter.domain.preferences import PreferencesService
from jobhunter.ingestion.ingestor import Ingestor
from jobhunter.llm.gemini import GeminiProvider
from jobhunter.llm.prompt_store import PromptStore
from jobhunter.profile.profile_service import ProfileService
from jobhunter.scheduler import start_jobs
from jobhunter.sending.dispatcher import Dispatcher
from jobhunter.sending.gateways import SMTPEmail, TelethonDM
from jobhunter.storage.db import Database
from jobhunter.storage.messages import RawMessageRepo
from jobhunter.storage.offers import OfferRepo
from jobhunter.storage.profiles import ProfileRepo
from jobhunter.storage.repositories import SettingsRepo, SourceChatRepo
from jobhunter.storage.sending import Application, ApplicationRepo, ContactRepo
from jobhunter.telegram.bot import create_bot, publish_commands, start_bot
from jobhunter.telegram.folders import ApplicationsFolder
from jobhunter.telegram.handlers import register_all
from jobhunter.telegram.handlers.offers import send_card
from jobhunter.telegram.handlers.sending import on_result
from jobhunter.telegram.replies import register_reply_listener
from jobhunter.telegram.sources import (
    RuntimeState,
    SourceRegistry,
    catch_up,
    register_listener,
)
from jobhunter.telegram.status_board import StatusBoard
from jobhunter.telegram.userbot import create_userbot, secure_session

log = logging.getLogger(__name__)


class StartupError(RuntimeError):
    pass


def _display(me: object) -> str:
    name = " ".join(filter(None, [getattr(me, "first_name", ""), getattr(me, "last_name", "")]))
    username = getattr(me, "username", None)
    return f"{name} (@{username})" if username else name


async def login(settings: Settings) -> None:
    """Login interactivo del userbot (código de Telegram + 2FA si la tienes)."""
    client = create_userbot(settings)
    await client.start(phone=settings.tg_phone or (lambda: input("Teléfono (+código país): ")))
    me = await client.get_me()
    secure_session(settings.tg_session_name)
    print(f"\n✅ Sesión iniciada como {_display(me)}")
    print(f"   Tu user ID es {me.id}")
    if settings.owner_user_id != me.id:
        print(f"   → Pon OWNER_USER_ID={me.id} en tu .env (si el bot es para esta misma cuenta).")
    await client.disconnect()


async def run(settings: Settings, config: AppConfig) -> None:
    if missing := settings.missing_for_run():
        raise StartupError(f"Faltan variables en .env: {', '.join(missing)}")

    # Prompts inmutables: se cargan una vez y su hash queda en el log (docs/08 §4).
    prompts = PromptStore()
    log.info("Prompts cargados (%d) · sha256=%s", len(prompts.names), prompts.digest[:16])

    db = Database(settings.database_path)
    await db.connect()

    userbot = create_userbot(settings)
    bot = create_bot(settings)
    background: list[asyncio.Task[None]] = []
    try:
        await userbot.connect()
        if not await userbot.is_user_authorized():
            raise StartupError(
                "La cuenta no ha iniciado sesión. Ejecuta: python -m jobhunter login"
            )
        secure_session(settings.tg_session_name)
        me = await userbot.get_me()
        if me.id != settings.owner_user_id:
            log.warning(
                "OWNER_USER_ID (%s) no coincide con la cuenta del userbot (%s).",
                settings.owner_user_id,
                me.id,
            )
        await userbot.get_dialogs()  # precarga la caché de entidades (IDs de grupos)

        await start_bot(bot, settings)
        secure_session(settings.bot_session_name)
        bot_me = await bot.get_me()

        sources = SourceChatRepo(db)
        registry = SourceRegistry(sources)
        await registry.refresh()
        state = RuntimeState(me_display=_display(me), bot_username=bot_me.username or "")

        raw_messages = RawMessageRepo(db)
        profile_repo = ProfileRepo(db)
        offers = OfferRepo(db)
        settings_repo = SettingsRepo(db)
        prefs = PreferencesService(settings_repo, config)
        ingestor = Ingestor(raw_messages, sources, config)
        contacts = ContactRepo(db)
        applications = ApplicationRepo(db)
        dispatcher = Dispatcher(
            config=config,
            tz=settings.zoneinfo,
            dm=TelethonDM(userbot, config),
            email=SMTPEmail(settings, config),
            contacts=contacts,
            applications=applications,
            offers=offers,
            profiles=profile_repo,
            settings=settings_repo,
        )
        assert settings.owner_user_id is not None
        status_board = StatusBoard(
            bot,
            settings.owner_user_id,
            dispatcher,
            settings_repo,
            effective_dry_run(settings, config),
            config.sending.telegram_dm.pin_status_message,
        )

        llm = profiles = analyzer = None
        if settings.gemini_api_key:
            llm = GeminiProvider(settings.gemini_api_key.get_secret_value(), config.llm)
            profiles = ProfileService(profile_repo, llm, prompts, config)
            analyzer = OfferAnalyzer(
                llm, prompts, config, raw_messages, offers, profile_repo, prefs,
                DraftWriter(llm, prompts, config),
            )  # fmt: skip
        else:
            log.warning("GEMINI_API_KEY no definida: análisis de CV y de ofertas desactivado")

        ctx = AppContext(
            settings=settings,
            config=config,
            db=db,
            sources=sources,
            raw_messages=raw_messages,
            prefs=prefs,
            registry=registry,
            userbot=userbot,
            bot=bot,
            state=state,
            offers=offers,
            ingestor=ingestor,
            settings_repo=settings_repo,
            contacts=contacts,
            applications=applications,
            dispatcher=dispatcher,
            status_board=status_board,
            llm=llm,
            profiles=profiles,
            analyzer=analyzer,
            folder=ApplicationsFolder(userbot, config.sending.telegram_dm.folder_name),
        )
        if analyzer is not None:

            async def notify(offer_id: int) -> None:
                if await prefs.paused():  # /pausa: va al resumen en vez de tarjeta inmediata
                    await offers.set_status(offer_id, "pending_digest")
                    return
                await send_card(ctx, offer_id)

            ctx.worker = AnalysisWorker(analyzer, raw_messages, profile_repo, notify)
            ingestor.on_pending = ctx.worker.wake

        async def result_hook(app: Application) -> None:
            await on_result(ctx, app)

        dispatcher.on_result = result_hook

        register_listener(userbot, registry, ingestor, state)
        register_reply_listener(userbot, bot, settings.owner_user_id, applications)
        register_all(ctx)
        await publish_commands(bot)

        log.info("Userbot: %s · Bot: @%s", state.me_display, state.bot_username)
        await _notify_online(ctx)
        if config.sources.catch_up_on_start:
            background.append(asyncio.create_task(_catch_up(ctx, ingestor)))
        if ctx.worker is not None:
            background.append(asyncio.create_task(ctx.worker.run()))
        background.append(asyncio.create_task(dispatcher.run()))
        background.append(asyncio.create_task(_sync_folder(ctx)))
        background += start_jobs(ctx)
        await status_board.refresh()
        await _run_until_stopped(userbot, bot)
    finally:
        for task in background:
            task.cancel()
        for client in (bot, userbot):
            with contextlib.suppress(Exception):
                await client.disconnect()
        await db.close()
        log.info("JobHunter detenido")


async def _sync_folder(ctx: AppContext) -> None:
    """Al arrancar: mete en la carpeta a quien ya recibió un DM real y aún no está."""
    if ctx.folder is None or not ctx.folder.enabled:
        return
    added, error = await ctx.folder.add(await ctx.contacts.messaged_user_ids())
    if added:
        log.info("Carpeta «%s»: %s reclutadores añadidos", ctx.folder.name, added)
    if error:
        log.warning("Carpeta «%s»: %s", ctx.folder.name, error)


async def _catch_up(ctx: AppContext, ingestor: Ingestor) -> None:
    try:
        ctx.state.caught_up = await catch_up(
            ctx.userbot,
            ctx.registry,
            ctx.sources,
            ingestor,
            ctx.config.sources.catch_up_max_messages,
        )
    except Exception:
        log.exception("Error en el catch-up")


async def _notify_online(ctx: AppContext) -> None:
    dry = " · 🧪 simulación" if effective_dry_run(ctx.settings, ctx.config) else ""
    text = f"🟢 JobHunter en línea — escuchando {len(ctx.registry)} chats{dry}"
    if not len(ctx.registry):
        text += "\nAñade grupos con /grupo_add o /grupos."
    try:
        await ctx.bot.send_message(ctx.owner_id, text)
    except (ValueError, RPCError):
        log.warning(
            "No pude escribirte. Abre el chat con @%s y envía /start.", ctx.state.bot_username
        )


async def _run_until_stopped(*clients: TelegramClient) -> None:
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, stop.set)

    tasks = [asyncio.create_task(c.run_until_disconnected()) for c in clients]
    tasks.append(asyncio.create_task(stop.wait()))
    done, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
    for task in pending:
        task.cancel()
    for task in done:
        if (exc := task.exception()) is not None:
            raise exc
