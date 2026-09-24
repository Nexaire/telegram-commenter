import asyncio
import html
from datetime import datetime, timedelta, timezone

import structlog
from telethon import TelegramClient, errors, events, utils

from .leads import CoverRequestDetector

log = structlog.get_logger()
TELEGRAM_MESSAGE_LIMIT = 4096


def message_url(entity, message_id: int) -> str | None:
    """Build a Telegram link for public entities and private supergroups/channels."""
    username = getattr(entity, "username", None)
    if username:
        return f"https://t.me/{username}/{message_id}"
    entity_id = getattr(entity, "id", None)
    if entity_id and (
        getattr(entity, "megagroup", False) or getattr(entity, "broadcast", False)
    ):
        return f"https://t.me/c/{entity_id}/{message_id}"
    return None


def notification_text(title: str, text: str, url: str | None) -> str:
    safe_title = html.escape(title)
    safe_text = html.escape(" ".join(text.split()))
    link = (
        f'\n\n<a href="{html.escape(url, quote=True)}">Открыть исходное сообщение</a>'
        if url
        else "\n\nСсылка недоступна: источник — обычная приватная группа."
    )
    prefix = (
        "🎸 <b>Найдена заявка на кавер-группу</b>"
        f"\n\n<b>Источник:</b> {safe_title}\n\n"
    )
    available = max(0, TELEGRAM_MESSAGE_LIMIT - len(prefix) - len(link) - 1)
    if len(safe_text) > available:
        safe_text = safe_text[: max(0, available - 1)].rstrip() + "…"
    return prefix + safe_text + link


class CoverMonitorService:
    def __init__(self, settings, db):
        self.settings = settings
        self.db = db
        self.client = TelegramClient(
            settings.telegram_session,
            settings.telegram_api_id,
            settings.telegram_api_hash,
        )
        self.sources = {}
        self.entities = {}
        self.target = None
        self.detector = None

    async def start(self):
        config = self.settings.monitor_config()
        matching = config.get("matching", {})
        self.detector = CoverRequestDetector(
            keywords=matching.get("keywords", ["кавер-группа"]),
            intent_phrases=matching.get("intent_phrases", []),
            exclude_phrases=matching.get("exclude_phrases", []),
            require_intent=matching.get("require_intent", False),
        )
        await self.client.start()
        # Заполняет entity cache, чтобы числовые ID приватных каналов и групп
        # надёжно разрешались после создания или добавления аккаунта.
        await self.client.get_dialogs()
        self.target = await self.client.get_entity(config["target_channel"])

        for item in config["sources"]:
            reference = item.get("entity") or item.get("username")
            if not reference:
                raise ValueError("Every source needs entity or username")
            entity = await self.client.get_entity(reference)
            peer_id = utils.get_peer_id(entity)
            self.sources[peer_id] = item
            self.entities[peer_id] = entity
            log.info(
                "source_registered",
                peer_id=peer_id,
                title=getattr(entity, "title", str(reference)),
            )

        self.client.add_event_handler(
            self.on_message,
            events.NewMessage(chats=list(self.entities.values())),
        )
        await self.catch_up()
        await asyncio.gather(
            self.poll_loop(),
            self.publisher_loop(),
            self.client.run_until_disconnected(),
        )

    async def on_message(self, event):
        await self.process(event.message)

    async def poll_loop(self):
        while True:
            await asyncio.sleep(self.settings.monitor_poll_seconds)
            await self.catch_up()

    async def publisher_loop(self):
        while True:
            now = datetime.now(timezone.utc).isoformat()
            rows = await self.db.all(
                "SELECT * FROM matches WHERE status IN ('new','error') "
                "AND (next_attempt_at IS NULL OR next_attempt_at<=?) "
                "ORDER BY created_at LIMIT 20",
                (now,),
            )
            for match in rows:
                await self.publish_match(match)
            await asyncio.sleep(5)

    async def catch_up(self):
        since = datetime.now(timezone.utc) - timedelta(
            hours=self.settings.monitor_lookback_hours
        )
        for peer_id, entity in self.entities.items():
            cursor_key = f"source_cursor:{peer_id}"
            last_id = int(await self.db.meta(cursor_key) or 0)
            max_id = last_id
            if last_id:
                messages = self.client.iter_messages(
                    entity, min_id=last_id, reverse=True
                )
            else:
                # Telegram отдаёт новые сообщения первыми. На первом запуске
                # останавливаемся у границы lookback, не сканируя всю историю.
                messages = self.client.iter_messages(entity)
            async for message in messages:
                if not last_id and message.date < since:
                    break
                await self.process(message)
                max_id = max(max_id, message.id)
            if max_id > last_id:
                await self.db.set_meta(cursor_key, str(max_id))

    async def process(self, message):
        text = (message.raw_text or "").strip()
        if not text or message.action or message.out:
            return
        detector = self.detector
        if detector is None:
            raise RuntimeError("Detector is not initialized")
        result = detector.detect(text)
        if not result:
            return

        entity = await message.get_chat()
        peer_id = utils.get_peer_id(entity)
        if peer_id not in self.sources:
            return
        title = (
            getattr(entity, "title", None)
            or getattr(entity, "username", None)
            or str(peer_id)
        )
        url = message_url(entity, message.id)
        match_id = await self.db.add_match(
            source_peer_id=peer_id,
            source_message_id=message.id,
            source_title=title,
            source_url=url,
            text=text,
            matched_keywords=result.keywords,
            matched_intents=result.intents,
        )
        if match_id:
            log.info("match_detected", match_id=match_id, source_url=url)

    async def publish_match(self, match: dict):
        match_id = match["id"]
        body = notification_text(
            match["source_title"], match["text"], match["source_url"]
        )
        if self.settings.dry_run:
            await self.db.execute(
                "UPDATE matches SET status='dry_run' WHERE id=?", (match_id,)
            )
            log.info(
                "match_dry_run",
                match_id=match_id,
                source_url=match["source_url"],
                text=match["text"][:300],
            )
            return
        try:
            sent = await self.client.send_message(
                self.target,
                body,
                parse_mode="html",
                link_preview=False,
            )
            await self.db.execute(
                "UPDATE matches SET status='published', destination_message_id=?, "
                "published_at=?, next_attempt_at=NULL, error=NULL WHERE id=?",
                (sent.id, datetime.now(timezone.utc).isoformat(), match_id),
            )
            log.info(
                "match_published",
                match_id=match_id,
                destination_message_id=sent.id,
            )
        except errors.FloodWaitError as exc:
            retry_at = datetime.now(timezone.utc) + timedelta(seconds=exc.seconds + 5)
            await self.db.execute(
                "UPDATE matches SET status='error', attempt_count=attempt_count+1, "
                "next_attempt_at=?, error=? WHERE id=?",
                (retry_at.isoformat(), f"FLOOD_WAIT {exc.seconds}", match_id),
            )
            log.warning(
                "publish_flood_wait", match_id=match_id, seconds=exc.seconds
            )
        except Exception as exc:
            attempts = int(match.get("attempt_count") or 0) + 1
            delay_seconds = min(3600, 60 * (2 ** min(attempts - 1, 6)))
            retry_at = datetime.now(timezone.utc) + timedelta(seconds=delay_seconds)
            await self.db.execute(
                "UPDATE matches SET status='error', attempt_count=?, "
                "next_attempt_at=?, error=? WHERE id=?",
                (attempts, retry_at.isoformat(), str(exc)[:1000], match_id),
            )
            log.exception("match_publish_failed", match_id=match_id)
