import asyncio
import json
from datetime import datetime, timedelta, timezone

import structlog
from telethon import TelegramClient, events, errors, functions, utils

log = structlog.get_logger()


class CommenterService:
    def __init__(self, settings, db, generator, bot):
        self.s, self.db, self.generator, self.bot = settings, db, generator, bot
        self.client = TelegramClient(settings.telegram_session, settings.telegram_api_id, settings.telegram_api_hash)
        self.channels = {}
        self.entities = {}

    async def start(self):
        await self.client.start()
        for item in self.s.channels():
            entity = await self.client.get_entity(item["username"])
            peer_id = utils.get_peer_id(entity)
            self.channels[peer_id] = item
            self.entities[peer_id] = entity
        self.client.add_event_handler(self.on_message, events.NewMessage(chats=list(self.entities.values())))
        await self.catch_up()
        await asyncio.gather(self.publisher_loop(), self.poll_loop(), self.client.run_until_disconnected())

    async def poll_loop(self):
        while True:
            await asyncio.sleep(self.s.monitor_poll_seconds)
            await self.catch_up()

    async def catch_up(self):
        since = datetime.now(timezone.utc) - timedelta(hours=self.s.monitor_lookback_hours)
        for channel_id, cfg in self.channels.items():
            entity = self.entities[channel_id]
            async for msg in self.client.iter_messages(entity, offset_date=datetime.now(timezone.utc), reverse=False):
                if msg.date < since: break
                await self.process(msg, cfg)

    async def on_message(self, event):
        await self.process(event.message, self.channels.get(event.chat_id, {}))

    async def process(self, msg, cfg):
        text = (msg.raw_text or "").strip()
        if not text or msg.action: return
        chat = await msg.get_chat()
        peer_id = utils.get_peer_id(chat)
        post_id = await self.db.add_post(peer_id, msg.id, getattr(chat, "title", str(peer_id)), text, cfg.get("expertise", ""))
        if not post_id: return
        try:
            variants = await self.generator.generate(text, cfg.get("expertise", ""))
            if not variants:
                await self.db.execute("UPDATE posts SET status='filtered' WHERE id=?", (post_id,)); return
            await self.db.set_variants(post_id, variants)
            await self.bot.send_for_approval(post_id)
            log.info("approval_sent", post_id=post_id)
        except Exception as exc:
            log.exception("generation_failed", post_id=post_id)
            await self.db.execute("UPDATE posts SET status='error', error=? WHERE id=?", (str(exc)[:1000], post_id))

    async def publisher_loop(self):
        while True:
            rows = await self.db.all("SELECT * FROM posts WHERE status='scheduled' AND scheduled_at<=?", (datetime.now(timezone.utc).isoformat(),))
            for post in rows: await self.publish(post)
            await asyncio.sleep(5)

    async def publish(self, post):
        variants = json.loads(post["variants_json"])
        try:
            # Повторная проверка очищает метки и защищает уже сохранённые в очереди варианты.
            comment = self.generator.validate(variants[post["selected_variant"]])
            if self.s.dry_run:
                log.info("dry_run_publish", post_id=post["id"], comment=comment)
                await self.db.execute("UPDATE posts SET status='dry_run', published_at=? WHERE id=?", (datetime.now(timezone.utc).isoformat(), post["id"])); return
            result = await self.client(functions.messages.GetDiscussionMessageRequest(peer=post["channel_id"], msg_id=post["message_id"]))
            discussion = result.messages[0]
            send_as = await self.client.get_entity(self.s.send_as_channel) if self.s.send_as_channel else None
            await self.client.send_message(discussion.peer_id, comment, reply_to=discussion.id, send_as=send_as)
            await self.db.execute("UPDATE posts SET status='published', published_at=?, error=NULL WHERE id=?", (datetime.now(timezone.utc).isoformat(), post["id"]))
            log.info("published", post_id=post["id"])
        except errors.FloodWaitError as exc:
            retry = datetime.now(timezone.utc) + timedelta(seconds=exc.seconds + 5)
            await self.db.execute("UPDATE posts SET scheduled_at=?, error=? WHERE id=?", (retry.isoformat(), f"FLOOD_WAIT {exc.seconds}", post["id"]))
            log.warning("flood_wait", seconds=exc.seconds)
        except (errors.ChatWriteForbiddenError, errors.UserBannedInChannelError, errors.ChannelPrivateError) as exc:
            await self.db.execute("UPDATE posts SET status='permission_error', error=? WHERE id=?", (type(exc).__name__, post["id"]))
            log.warning("permission_error", post_id=post["id"], error=type(exc).__name__)
        except Exception as exc:
            await self.db.execute("UPDATE posts SET status='error', error=? WHERE id=?", (str(exc)[:1000], post["id"]))
            log.exception("publish_failed", post_id=post["id"])
