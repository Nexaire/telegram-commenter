import json
from datetime import datetime, timezone
from pathlib import Path

import aiosqlite


SCHEMA = """
CREATE TABLE IF NOT EXISTS posts (
 id INTEGER PRIMARY KEY AUTOINCREMENT, channel_id INTEGER NOT NULL, message_id INTEGER NOT NULL,
 channel_title TEXT NOT NULL, text TEXT NOT NULL, expertise TEXT, variants_json TEXT,
 selected_variant INTEGER NOT NULL DEFAULT 0, status TEXT NOT NULL DEFAULT 'new',
 created_at TEXT NOT NULL, scheduled_at TEXT, published_at TEXT, error TEXT,
 UNIQUE(channel_id, message_id)
);
CREATE INDEX IF NOT EXISTS idx_posts_status ON posts(status, scheduled_at);
"""


class Database:
    def __init__(self, path: str):
        self.path = path

    async def init(self):
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        async with aiosqlite.connect(self.path) as db:
            await db.executescript(SCHEMA)
            await db.commit()

    async def add_post(self, channel_id, message_id, title, text, expertise) -> int | None:
        try:
            async with aiosqlite.connect(self.path) as db:
                cur = await db.execute(
                    "INSERT INTO posts(channel_id,message_id,channel_title,text,expertise,created_at) VALUES(?,?,?,?,?,?)",
                    (channel_id, message_id, title, text, expertise, datetime.now(timezone.utc).isoformat()),
                )
                await db.commit()
                return cur.lastrowid
        except aiosqlite.IntegrityError:
            return None

    async def execute(self, sql, params=()):
        async with aiosqlite.connect(self.path) as db:
            await db.execute(sql, params)
            await db.commit()

    async def one(self, sql, params=()):
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(sql, params) as cur:
                row = await cur.fetchone()
                return dict(row) if row else None

    async def all(self, sql, params=()):
        async with aiosqlite.connect(self.path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(sql, params) as cur:
                return [dict(row) for row in await cur.fetchall()]

    async def set_variants(self, post_id: int, variants: list[str]):
        await self.execute("UPDATE posts SET variants_json=?, status='pending' WHERE id=?", (json.dumps(variants, ensure_ascii=False), post_id))

    async def today_count(self) -> int:
        row = await self.one("SELECT COUNT(*) AS n FROM posts WHERE status IN ('scheduled','published','dry_run') AND date(COALESCE(published_at,scheduled_at))=date('now')")
        return int(row["n"])

