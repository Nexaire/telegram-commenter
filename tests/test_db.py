import sqlite3

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
    assert queued[0]["delivery_status"] == "pending"


@pytest.mark.asyncio
async def test_status_transition_is_atomic(tmp_path):
    db = Database(str(tmp_path / "monitor.db"))
    await db.init()
    match_id = await db.add_match(
        source_peer_id=-100123,
        source_message_id=42,
        source_title="Test chat",
        source_url="https://t.me/test/42",
        text="Ищем кавер-группу",
        matched_keywords=("кавер-групп",),
        matched_intents=("ищем",),
    )
    await db.execute(
        "UPDATE matches SET delivery_status='published' WHERE id=?", (match_id,)
    )

    assert await db.transition_match(match_id, "new", "in_progress") is True
    assert await db.transition_match(match_id, "new", "irrelevant") is False
    row = await db.one("SELECT status,reviewed_at FROM matches WHERE id=?", (match_id,))
    assert row["status"] == "in_progress"
    assert row["reviewed_at"] is not None


@pytest.mark.asyncio
async def test_old_delivery_statuses_are_archived_during_migration(tmp_path):
    path = tmp_path / "old.db"
    with sqlite3.connect(path) as old_db:
        old_db.executescript(
            """
            CREATE TABLE matches (
              id INTEGER PRIMARY KEY,
              source_peer_id INTEGER NOT NULL,
              source_message_id INTEGER NOT NULL,
              source_title TEXT NOT NULL,
              source_url TEXT,
              text TEXT NOT NULL,
              matched_keywords_json TEXT NOT NULL,
              matched_intents_json TEXT NOT NULL,
              status TEXT NOT NULL,
              destination_message_id INTEGER,
              created_at TEXT NOT NULL,
              published_at TEXT,
              attempt_count INTEGER NOT NULL DEFAULT 0,
              next_attempt_at TEXT,
              error TEXT
            );
            INSERT INTO matches VALUES (
              1,-1001,1,'Source',NULL,'Text','[]','[]','published',10,
              '2026-01-01T00:00:00+00:00',NULL,0,NULL,NULL
            );
            """
        )
    db = Database(str(path))
    await db.init()

    row = await db.one(
        "SELECT status,delivery_status,destination_chat_id FROM matches WHERE id=1"
    )
    assert row == {
        "status": "archived",
        "delivery_status": "published",
        "destination_chat_id": None,
    }
