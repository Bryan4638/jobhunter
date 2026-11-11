from datetime import UTC, datetime
from pathlib import Path

from jobhunter.storage.db import Database
from jobhunter.storage.repositories import SourceChatRepo


async def test_migrations_idempotent(tmp_path: Path) -> None:
    path = tmp_path / "t.db"
    for _ in range(2):
        db = Database(path)
        await db.connect()
        async with db.conn.execute("SELECT version FROM schema_migrations") as cur:
            assert [r[0] for r in await cur.fetchall()] == [
                "001_init",
                "002_messages_profiles",
                "003_offers",
                "004_sending",
                "005_offer_pages",
            ]
        await db.close()


async def test_source_chat_lifecycle(tmp_path: Path) -> None:
    db = Database(tmp_path / "t.db")
    await db.connect()
    repo = SourceChatRepo(db)
    try:
        assert await repo.add(-1001, "Python Jobs", "python_jobs", "group") is True
        assert await repo.add(-1002, "Remote Dev", None, "channel") is True
        assert await repo.enabled_ids() == {-1001, -1002}

        await repo.set_enabled(-1001, False)
        assert await repo.enabled_ids() == {-1002}

        # Volver a añadir reactiva y no duplica
        assert await repo.add(-1001, "Python Jobs LATAM", "python_jobs", "group") is False
        chat = await repo.get(-1001)
        assert chat is not None and chat.enabled and chat.title == "Python Jobs LATAM"

        await repo.touch(-1002, 50, datetime(2026, 11, 2, tzinfo=UTC))
        await repo.touch(-1002, 40, datetime(2026, 11, 2, tzinfo=UTC))  # no retrocede
        chat = await repo.get(-1002)
        assert chat is not None and chat.last_message_id == 50

        assert [c.title for c in await repo.list()] == ["Python Jobs LATAM", "Remote Dev"]
        assert await repo.remove(-1002) is True
        assert await repo.remove(-1002) is False
    finally:
        await db.close()
