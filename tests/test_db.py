import pytest

from app.db import Database


@pytest.mark.asyncio
async def test_match_deduplication_and_queue(tmp_path):
    db = Database(str(tmp_path / "monitor.db"))
    await db.init()
    match = {
        "source_peer_id": -100123,
        "source_message_id": 42,
        "source_title": "Test chat",
        "source_url": "https://t.me/test/42",
        "text": "Ищем кавер-группу",
        "matched_keywords": ("кавер-групп",),
        "matched_intents": ("ищем",),
    }

    first_id = await db.add_match(**match)
    duplicate_id = await db.add_match(**match)
    queued = await db.all("SELECT * FROM matches WHERE status='new'")

    assert first_id is not None
    assert duplicate_id is None
    assert len(queued) == 1
    assert queued[0]["source_message_id"] == 42
