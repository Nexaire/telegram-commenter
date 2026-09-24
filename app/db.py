import json
from datetime import datetime, timezone
from pathlib import Path

import aiosqlite

SCHEMA = """
CREATE TABLE IF NOT EXISTS matches (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 source_peer_id INTEGER NOT NULL,
 source_message_id INTEGER NOT NULL,
 source_title TEXT NOT NULL,
 source_url TEXT,
 text TEXT NOT NULL,
 matched_keywords_json TEXT NOT NULL,
 matched_intents_json TEXT NOT NULL,
 status TEXT NOT NULL DEFAULT 'new',
 delivery_status TEXT NOT NULL DEFAULT 'pending',
 destination_chat_id INTEGER,
 destination_message_id INTEGER,
 created_at TEXT NOT NULL,
 published_at TEXT,
 attempt_count INTEGER NOT NULL DEFAULT 0,
 next_attempt_at TEXT,
 error TEXT,
 UNIQUE(source_peer_id, source_message_id)
);
CREATE INDEX IF NOT EXISTS idx_matches_status ON matches(status, created_at);
CREATE TABLE IF NOT EXISTS app_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""

MATCH_MIGRATIONS = {
    "attempt_count": "INTEGER NOT NULL DEFAULT 0",
    "next_attempt_at": "TEXT",
    "delivery_status": "TEXT NOT NULL DEFAULT 'pending'",
    "destination_chat_id": "INTEGER",
    "reviewed_at": "TEXT",
}


class Database:
    def __init__(self, path: str):
        self.path = path

    async def init(self):
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        async with aiosqlite.connect(self.path) as db:
            await db.executescript(SCHEMA)
            columns = {
                row[1]
                for row in await (
                    await db.execute("PRAGMA table_info(matches)")
                ).fetchall()
            }
            had_delivery_status = "delivery_status" in columns
            for name, sql_type in MATCH_MIGRATIONS.items():
                if name not in columns:
                    await db.execute(
                        f"ALTER TABLE matches ADD COLUMN {name} {sql_type}"
                    )
            if not had_delivery_status:
                # До появления workflow поле status описывало доставку. Старые
                # записи архивируем, чтобы они не попали в новую очередь и отчёты.
                await db.execute(
                    "UPDATE matches SET delivery_status=status "
                    "WHERE status IN ('dry_run','published','error')"
                )
                await db.execute(
                    "UPDATE matches SET status='archived' "
                    "WHERE status IN ('dry_run','published')"
                )
                await db.execute("UPDATE matches SET status='new' WHERE status='error'")
            await db.commit()

    async def add_match(
        self,
        *,
        source_peer_id: int,
        source_message_id: int,
        source_title: str,
        source_url: str | None,
        text: str,
        matched_keywords: tuple[str, ...],
        matched_intents: tuple[str, ...],
    ) -> int | None:
        try:
            async with aiosqlite.connect(self.path) as db:
                cursor = await db.execute(
                    "INSERT INTO matches(source_peer_id,source_message_id,source_title,source_url,text,"
                    "matched_keywords_json,matched_intents_json,created_at) VALUES(?,?,?,?,?,?,?,?)",
                    (
                        source_peer_id,
                        source_message_id,
                        source_title,
                        source_url,
                        text,
                        json.dumps(matched_keywords, ensure_ascii=False),
                        json.dumps(matched_intents, ensure_ascii=False),
                        datetime.now(timezone.utc).isoformat(),
                    ),
                )
                await db.commit()
                return cursor.lastrowid
        except aiosqlite.IntegrityError:
            return None

    async def execute(self, sql: str, params=()):
        async with aiosqlite.connect(self.path) as db:
            await db.execute(sql, params)
            await db.commit()

    async def transition_match(
        self, match_id: int, expected_status: str, new_status: str
    ) -> bool:
        """Atomically change a workflow status if the button is still current."""
        async with aiosqlite.connect(self.path) as db:
            cursor = await db.execute(
                "UPDATE matches SET status=?, reviewed_at=? "
                "WHERE id=? AND status=? AND delivery_status='published'",
                (
                    new_status,
                    datetime.now(timezone.utc).isoformat(),
                    match_id,
                    expected_status,
                ),
            )
            await db.commit()
            return cursor.rowcount == 1

    async def one(self, sql: str, params=()):
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(sql, params) as cursor:
                row = await cursor.fetchone()
                return dict(row) if row else None

    async def all(self, sql: str, params=()):
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(sql, params) as cursor:
                return [dict(row) for row in await cursor.fetchall()]

    async def meta(self, key: str) -> str | None:
        row = await self.one("SELECT value FROM app_meta WHERE key=?", (key,))
        return row["value"] if row else None

    async def set_meta(self, key: str, value: str):
        await self.execute(
            "INSERT INTO app_meta(key,value) VALUES(?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )
