from datetime import UTC, datetime, timedelta

from jobhunter.config import AppConfig
from jobhunter.ingestion.dedupe import find_near_duplicate
from jobhunter.ingestion.ingestor import Ingestor
from jobhunter.ingestion.models import IncomingMessage, build_message_link
from jobhunter.storage.db import Database
from jobhunter.storage.messages import RawMessageRepo
from jobhunter.storage.repositories import SourceChatRepo

OFFER = (
    "Estamos buscando Desarrollador Backend Python para trabajo remoto. Requisitos: 3 años "
    "con Django y PostgreSQL. Salario 1500 USD/mes. Envía tu CV a jobs@acme.com"
)
NOW = datetime(2026, 11, 2, 12, 0, tzinfo=UTC)


def msg(chat_id: int, message_id: int, text: str, **kw) -> IncomingMessage:
    data = {
        "chat_id": chat_id,
        "chat_username": None,
        "message_id": message_id,
        "sender_id": 42,
        "sender_username": "maria_rrhh",
        "sender_name": "María",
        "sender_is_user": True,
        "text": text,
        "hidden_urls": (),
        "posted_at": NOW,
    } | kw
    return IncomingMessage(**data)


async def make(db: Database) -> tuple[Ingestor, RawMessageRepo, SourceChatRepo]:
    sources = SourceChatRepo(db)
    await sources.add(-1001, "Grupo A", "grupo_a", "group")
    await sources.add(-1002, "Grupo B", None, "channel")
    raw = RawMessageRepo(db)
    return Ingestor(raw, sources, AppConfig()), raw, sources


async def test_offer_saved_and_cursor_advanced(db: Database) -> None:
    ingestor, raw, sources = await make(db)
    result = await ingestor.ingest(msg(-1001, 10, OFFER, chat_username="grupo_a"))
    assert result is not None and result.status == "new"

    stored = await raw.get(result.raw_id)
    assert stored is not None
    assert stored["link"] == "https://t.me/grupo_a/10"
    assert stored["sender_username"] == "maria_rrhh" and stored["sender_is_user"] == 1
    chat = await sources.get(-1001)
    assert chat is not None and chat.last_message_id == 10


async def test_same_message_twice_is_ignored(db: Database) -> None:
    ingestor, _, _ = await make(db)
    assert await ingestor.ingest(msg(-1001, 10, OFFER)) is not None
    assert await ingestor.ingest(msg(-1001, 10, OFFER)) is None  # catch-up + listener


async def test_exact_duplicate_in_other_group(db: Database) -> None:
    ingestor, _, _ = await make(db)
    first = await ingestor.ingest(msg(-1001, 10, OFFER))
    reposted = "🔥🔥 " + OFFER.upper() + " !!!"  # mismo texto normalizado
    second = await ingestor.ingest(msg(-1002, 99, reposted))
    assert first and second
    assert second.status == "duplicate" and second.duplicate_of == first.raw_id


async def test_near_duplicate(db: Database) -> None:
    ingestor, _, _ = await make(db)
    first = await ingestor.ingest(msg(-1001, 10, OFFER))
    edited = OFFER.replace("1500 USD/mes", "1600 USD/mes")
    second = await ingestor.ingest(msg(-1002, 11, edited))
    assert first and second and second.duplicate_of == first.raw_id


async def test_different_offer_is_not_duplicate(db: Database) -> None:
    ingestor, _, _ = await make(db)
    await ingestor.ingest(msg(-1001, 10, OFFER))
    other = (
        "Se busca programador Frontend React con TypeScript, contrato freelance de 3 meses, "
        "100% remoto. Interesados escribir a @talento_dev con portfolio."
    )
    result = await ingestor.ingest(msg(-1001, 11, other))
    assert result and result.status == "new"


async def test_duplicate_window(db: Database) -> None:
    ingestor, _, _ = await make(db)
    await ingestor.ingest(msg(-1001, 10, OFFER, posted_at=NOW - timedelta(days=30)))
    result = await ingestor.ingest(msg(-1002, 11, OFFER))
    assert result and result.status == "new"  # el original quedó fuera de la ventana


async def test_filtered_messages_are_not_originals(db: Database) -> None:
    ingestor, _, _ = await make(db)
    short = await ingestor.ingest(msg(-1001, 10, "Hola grupo"))
    assert short and short.status == "filtered"
    again = await ingestor.ingest(msg(-1002, 11, "Hola grupo"))
    assert again and again.status == "filtered"  # no "duplicate" de un filtrado


async def test_hidden_urls_kept(db: Database) -> None:
    ingestor, raw, _ = await make(db)
    result = await ingestor.ingest(msg(-1001, 10, OFFER, hidden_urls=("https://acme.jobs/1",)))
    assert result
    stored = await raw.get(result.raw_id)
    assert stored and '"https://acme.jobs/1"' in stored["hidden_urls"]


async def test_counts(db: Database) -> None:
    ingestor, raw, _ = await make(db)
    await ingestor.ingest(msg(-1001, 10, OFFER))
    await ingestor.ingest(msg(-1002, 11, OFFER))
    await ingestor.ingest(msg(-1002, 12, "hola"))
    counts = await raw.counts_since(datetime(2000, 1, 1, tzinfo=UTC))
    assert counts == {"new": 1, "duplicate": 1, "filtered": 1}


def test_message_links() -> None:
    assert build_message_link(-1001234567890, None, 5) == "https://t.me/c/1234567890/5"
    assert build_message_link(-1001234567890, "grupo", 5) == "https://t.me/grupo/5"
    assert build_message_link(-12345, None, 5) is None


def test_near_duplicate_ratio_not_subset() -> None:
    short = "python remoto"
    long_ = "buscamos python remoto con django postgres docker y aws para proyecto largo"
    assert find_near_duplicate(short, [(1, long_)], 92) is None
