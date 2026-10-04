import asyncio
import html
import json
from datetime import datetime, timedelta, timezone

import structlog
from telethon import TelegramClient, errors, events, utils

from .analyzer import GigaChatAnalyzer

log = structlog.get_logger()


def message_url(entity, message_id: int) -> str | None:
    username = getattr(entity, "username", None)
    if username:
        return f"https://t.me/{username}/{message_id}"
    entity_id = getattr(entity, "id", None)
    if entity_id and (
        getattr(entity, "megagroup", False) or getattr(entity, "broadcast", False)
    ):
        return f"https://t.me/c/{entity_id}/{message_id}"
    return None


def material_text(insight: dict) -> str:
    def esc(value):
        return html.escape(str(value or ""))

    def short(value, limit):
        value = str(value or "").strip()
        return value if len(value) <= limit else value[: limit - 1].rstrip() + "…"

    lines = [
        "🧩 <b>Наблюдение из сообщества</b>",
        f"\n<b>Тема:</b> {esc(short(insight['topic'], 120))}",
        f"\n<b>Боль:</b> {esc(short(insight['pain'], 500))}",
    ]
    if insight.get("situation"):
        lines.append(f"\n<b>Контекст:</b> {esc(short(insight['situation'], 350))}")
    if insight.get("need"):
        lines.append(f"\n<b>Потребность:</b> {esc(short(insight['need'], 350))}")
    angles = insight.get("content_angles", [])
    if angles:
        lines.append("\n<b>Идеи для материалов:</b>")
        lines.extend(f"\n• {esc(short(angle, 140))}" for angle in angles[:3])
    lines.extend(
        [
            f"\n\n<b>Источник:</b> {esc(short(insight['source_title'], 100))}",
            f"\n<b>Дата:</b> {esc(insight['message_date'])[:16]}",
        ]
    )
    if insight.get("source_url"):
        lines.append(
            f'\n<a href="{html.escape(insight["source_url"], quote=True)}">'
            "Открыть исходное сообщение</a>"
        )
    return "".join(lines)[:3900]


class InsightsMonitor:
    def __init__(self, settings, db):
        self.settings = settings
        self.db = db
        self.config = settings.project_config()
        self.analyzer = GigaChatAnalyzer(settings)
        self.client = TelegramClient(
            settings.telegram_session,
            settings.telegram_api_id,
            settings.telegram_api_hash,
        )
        self.entities = {}
        self.min_length = int(self.config.get("analysis", {}).get("min_message_length", 30))
        self.confidence_threshold = float(
            self.config.get("analysis", {}).get("confidence_threshold", 0.60)
        )
        self.target = None

    async def start(self):
        await self.analyzer.start()
        await self.client.start()
        if not self.settings.dry_run:
            # Dry-run observations become deliverable when the operator enables
            # publishing, so the initial 24-hour scan can be reviewed first.
            await self.db.execute(
                "UPDATE insights SET delivery_status='pending' "
                "WHERE delivery_status='dry_run'"
            )
        await self.client.get_dialogs()
        self.target = await self.client.get_entity(self.config["target_chat"])
        for item in self.config["sources"]:
            reference = item.get("entity") or item.get("username")
            if not reference:
                raise ValueError("Every source needs an entity or username")
            entity = await self.client.get_entity(reference)
            peer_id = utils.get_peer_id(entity)
            self.entities[peer_id] = entity
            log.info(
                "source_registered",
                peer_id=peer_id,
                title=getattr(entity, "title", str(reference)),
            )
        self.client.add_event_handler(
            self.on_message, events.NewMessage(chats=list(self.entities.values()))
        )
        try:
            await self.catch_up()
            await asyncio.gather(
                self.poll_loop(), self.publisher_loop(), self.client.run_until_disconnected()
            )
        finally:
            await self.analyzer.close()
            await self.client.disconnect()

    async def on_message(self, event):
        try:
            await self.process(event.message)
        except Exception:
            log.exception("message_analysis_failed", message_id=event.message.id)

    async def process(self, message):
        text = (message.raw_text or "").strip()
        if not text or message.action or message.out:
            return
        entity = await message.get_chat()
        peer_id = utils.get_peer_id(entity)
        if peer_id not in self.entities:
            return
        if await self.db.exists(peer_id, message.id):
            return
        if len(text) < self.min_length:
            await self.db.mark_processed(peer_id, message.id, False)
            return

        analysis = await self.analyzer.analyze_with_retry(text)
        relevant = (
            analysis["relevant"]
            and analysis["confidence"] >= self.confidence_threshold
            and bool(analysis["topic"])
            and bool(analysis["pain"])
        )
        if not relevant:
            await self.db.mark_processed(peer_id, message.id, False)
            return

        title = (
            getattr(entity, "title", None)
            or getattr(entity, "username", None)
            or str(peer_id)
        )
        await self.db.add_insight(
            {
                "source_peer_id": peer_id,
                "source_message_id": message.id,
                "source_title": title,
                "source_url": message_url(entity, message.id),
                "message_date": message.date.astimezone(timezone.utc).isoformat(),
                "original_text": text,
                "topic": analysis["topic"],
                "pain": analysis["pain"],
                "situation": analysis["situation"],
                "need": analysis["need"],
                "content_angles_json": json.dumps(
                    analysis["content_angles"], ensure_ascii=False
                ),
                "confidence": analysis["confidence"],
            }
        )
        await self.db.mark_processed(peer_id, message.id, True)
        log.info("insight_detected", source_peer_id=peer_id, source_url=message_url(entity, message.id))

    async def catch_up(self):
        since = datetime.now(timezone.utc) - timedelta(
            hours=self.settings.monitor_lookback_hours
        )
        for peer_id, entity in self.entities.items():
            cursor_key = f"source_cursor:{peer_id}"
            last_id = int(await self.db.meta(cursor_key) or 0)
            max_id = last_id
            messages = (
                self.client.iter_messages(entity, min_id=last_id, reverse=True)
                if last_id
                else self.client.iter_messages(entity)
            )
            async for message in messages:
                if not last_id and message.date < since:
                    break
                await self.process(message)
                max_id = max(max_id, message.id)
            if max_id > last_id:
                await self.db.set_meta(cursor_key, str(max_id))

    async def poll_loop(self):
        while True:
            await asyncio.sleep(self.settings.monitor_poll_seconds)
            try:
                await self.catch_up()
            except Exception:
                log.exception("catch_up_failed")

    async def publisher_loop(self):
        while True:
            now = datetime.now(timezone.utc).isoformat()
            rows = await self.db.all(
                "SELECT * FROM insights WHERE delivery_status='pending' "
                "AND (next_attempt_at IS NULL OR next_attempt_at<=?) "
                "ORDER BY created_at LIMIT 20",
                (now,),
            )
            for row in rows:
                if self.settings.dry_run:
                    log.info(
                        "insight_dry_run",
                        insight_id=row["id"],
                        topic=row["topic"],
                        pain=row["pain"],
                        source_url=row["source_url"],
                    )
                    await self.db.execute(
                        "UPDATE insights SET delivery_status='dry_run' WHERE id=?",
                        (row["id"],),
                    )
                    continue
                try:
                    sent = await self.client.send_message(
                        self.target, material_text(row), parse_mode="html", link_preview=False
                    )
                    await self.db.execute(
                        "UPDATE insights SET delivery_status='published', "
                        "destination_message_id=?, next_attempt_at=NULL, last_error=NULL "
                        "WHERE id=?",
                        (sent.id, row["id"]),
                    )
                    log.info("insight_published", insight_id=row["id"], destination_message_id=sent.id)
                except errors.FloodWaitError as exc:
                    log.warning("flood_wait", seconds=exc.seconds)
                    retry_at = datetime.now(timezone.utc) + timedelta(seconds=exc.seconds + 2)
                    await self.db.execute(
                        "UPDATE insights SET next_attempt_at=?, last_error=? WHERE id=?",
                        (retry_at.isoformat(), f"FLOOD_WAIT {exc.seconds}", row["id"]),
                    )
                except Exception as exc:
                    log.exception("insight_publish_failed", insight_id=row["id"])
                    attempts = int(row["attempt_count"] or 0) + 1
                    retry_at = datetime.now(timezone.utc) + timedelta(
                        seconds=min(3600, 60 * (2 ** min(attempts - 1, 6)))
                    )
                    await self.db.execute(
                        "UPDATE insights SET attempt_count=?, next_attempt_at=?, "
                        "last_error=? WHERE id=?",
                        (attempts, retry_at.isoformat(), str(exc)[:1000], row["id"]),
                    )
            await asyncio.sleep(5)
