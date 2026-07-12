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
CREATE TABLE IF NOT EXISTS app_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS leads (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 discussion_id INTEGER NOT NULL, message_id INTEGER NOT NULL,
 channel_id INTEGER, channel_title TEXT NOT NULL, post_message_id INTEGER,
 text TEXT NOT NULL, sender_id INTEGER, sender_name TEXT NOT NULL,
 sender_username TEXT, comment_url TEXT NOT NULL, matched_terms_json TEXT NOT NULL,
 status TEXT NOT NULL DEFAULT 'new', created_at TEXT NOT NULL, reviewed_at TEXT,
 UNIQUE(discussion_id, message_id)
);
CREATE INDEX IF NOT EXISTS idx_leads_status_created ON leads(status, created_at);
"""

POST_MIGRATIONS = {
    "published_peer_id": "INTEGER",
    "published_message_id": "INTEGER",
    "last_checked_at": "TEXT",
    "deleted_at": "TEXT",
    "audit_error": "TEXT",
}


class Database:
    def __init__(self, path: str):
        self.path = path

    async def init(self):
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        async with aiosqlite.connect(self.path) as db:
            await db.executescript(SCHEMA)
            columns = {row[1] for row in await (await db.execute("PRAGMA table_info(posts)")).fetchall()}
            for name, sql_type in POST_MIGRATIONS.items():
                if name not in columns:
                    await db.execute(f"ALTER TABLE posts ADD COLUMN {name} {sql_type}")
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

    async def meta(self, key: str) -> str | None:
        row = await self.one("SELECT value FROM app_meta WHERE key=?", (key,))
        return row["value"] if row else None

    async def set_meta(self, key: str, value: str):
        await self.execute(
            "INSERT INTO app_meta(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )

    async def add_lead(self, **lead) -> int | None:
        try:
            async with aiosqlite.connect(self.path) as db:
                cur = await db.execute(
                    "INSERT INTO leads(discussion_id,message_id,channel_id,channel_title,post_message_id,"
                    "text,sender_id,sender_name,sender_username,comment_url,matched_terms_json,created_at) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        lead["discussion_id"], lead["message_id"], lead.get("channel_id"),
                        lead["channel_title"], lead.get("post_message_id"), lead["text"],
                        lead.get("sender_id"), lead["sender_name"], lead.get("sender_username"),
                        lead["comment_url"], json.dumps(lead["matched_terms"], ensure_ascii=False),
                        datetime.now(timezone.utc).isoformat(),
                    ),
                )
                await db.commit()
                return cur.lastrowid
        except aiosqlite.IntegrityError:
            return None
