from datetime import datetime, timezone
from pathlib import Path

import aiosqlite


SCHEMA = """
CREATE TABLE IF NOT EXISTS processed_messages (
    source_peer_id INTEGER NOT NULL,
    source_message_id INTEGER NOT NULL,
    processed_at TEXT NOT NULL,
    relevant INTEGER NOT NULL,
    PRIMARY KEY (source_peer_id, source_message_id)
);
CREATE TABLE IF NOT EXISTS insights (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_peer_id INTEGER NOT NULL,
    source_message_id INTEGER NOT NULL,
    source_title TEXT NOT NULL,
    source_url TEXT,
    message_date TEXT NOT NULL,
    original_text TEXT NOT NULL,
    topic TEXT NOT NULL,
    pain TEXT NOT NULL,
    situation TEXT NOT NULL,
    need TEXT NOT NULL,
    content_angles_json TEXT NOT NULL,
    confidence REAL NOT NULL,
    delivery_status TEXT NOT NULL DEFAULT 'pending',
    attempt_count INTEGER NOT NULL DEFAULT 0,
    next_attempt_at TEXT,
    last_error TEXT,
    destination_message_id INTEGER,
    created_at TEXT NOT NULL,
    UNIQUE(source_peer_id, source_message_id)
);
CREATE TABLE IF NOT EXISTS app_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_insights_delivery
    ON insights(delivery_status, created_at);
"""


class Database:
    def __init__(self, path: str):
        self.path = path

    async def init(self):
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        async with aiosqlite.connect(self.path) as db:
            await db.executescript(SCHEMA)
            await db.commit()

    async def exists(self, peer_id: int, message_id: int) -> bool:
        async with aiosqlite.connect(self.path) as db:
            async with db.execute(
                "SELECT 1 FROM processed_messages WHERE source_peer_id=? "
                "AND source_message_id=?",
                (peer_id, message_id),
            ) as cursor:
                return await cursor.fetchone() is not None

    async def mark_processed(
        self, peer_id: int, message_id: int, relevant: bool
    ):
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                "INSERT OR IGNORE INTO processed_messages "
                "(source_peer_id,source_message_id,processed_at,relevant) "
                "VALUES(?,?,?,?)",
                (
                    peer_id,
                    message_id,
                    datetime.now(timezone.utc).isoformat(),
                    int(relevant),
                ),
            )
            await db.commit()

    async def add_insight(self, values: dict):
        async with aiosqlite.connect(self.path) as db:
            await db.execute(
                "INSERT OR IGNORE INTO insights "
                "(source_peer_id,source_message_id,source_title,source_url,message_date,"
                "original_text,topic,pain,situation,need,content_angles_json,confidence,created_at) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    values["source_peer_id"],
                    values["source_message_id"],
                    values["source_title"],
                    values["source_url"],
                    values["message_date"],
                    values["original_text"],
                    values["topic"],
                    values["pain"],
                    values["situation"],
                    values["need"],
                    values["content_angles_json"],
                    values["confidence"],
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
            await db.commit()

    async def all(self, sql: str, params=()) -> list[dict]:
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(sql, params) as cursor:
                return [dict(row) for row in await cursor.fetchall()]

    async def execute(self, sql: str, params=()):
        async with aiosqlite.connect(self.path) as db:
            await db.execute(sql, params)
            await db.commit()

    async def meta(self, key: str) -> str | None:
        async with aiosqlite.connect(self.path) as db:
            async with db.execute(
                "SELECT value FROM app_meta WHERE key=?", (key,)
            ) as cursor:
                row = await cursor.fetchone()
                return row[0] if row else None

    async def set_meta(self, key: str, value: str):
        await self.execute(
            "INSERT INTO app_meta(key,value) VALUES(?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )
