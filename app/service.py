import asyncio
import json
from datetime import datetime, timedelta, timezone

import structlog
from telethon import TelegramClient, events, errors, functions, utils

from .leads import detect_lead

log = structlog.get_logger()


class CommenterService:
    def __init__(self, settings, db, generator, bot):
        self.s, self.db, self.generator, self.bot = settings, db, generator, bot
        self.client = TelegramClient(settings.telegram_session, settings.telegram_api_id, settings.telegram_api_hash)
        self.channels = {}
        self.entities = {}
        self.discussions = {}

    async def start(self):
        await self.client.start()
        for item in self.s.channels():
            entity = await self.client.get_entity(item["username"])
            peer_id = utils.get_peer_id(entity)
            self.channels[peer_id] = item
            self.entities[peer_id] = entity
            if self.s.lead_monitor_enabled and item.get("lead_monitor", True):
                await self.register_discussion(entity, peer_id, item)
        self.client.add_event_handler(self.on_message, events.NewMessage(chats=list(self.entities.values())))
        if self.discussions:
            discussion_entities = [item["entity"] for item in self.discussions.values()]
            self.client.add_event_handler(self.on_discussion_message, events.NewMessage(chats=discussion_entities))
        await self.catch_up()
        if self.discussions:
            await self.catch_up_leads()
        tasks = [
            self.publisher_loop(),
            self.audit_loop(),
            self.poll_loop(),
            self.client.run_until_disconnected(),
        ]
        if self.discussions:
            tasks.append(self.lead_poll_loop())
        await asyncio.gather(*tasks)

    async def poll_loop(self):
        while True:
            await asyncio.sleep(self.s.monitor_poll_seconds)
            await self.catch_up()

    async def lead_poll_loop(self):
        while True:
            await asyncio.sleep(self.s.lead_poll_seconds)
            await self.catch_up_leads()

    async def register_discussion(self, channel, channel_id, cfg):
        try:
            full = await self.client(functions.channels.GetFullChannelRequest(channel))
            linked_id = full.full_chat.linked_chat_id
            if not linked_id:
                return
            discussion = await self.client.get_entity(linked_id)
            self.discussions[utils.get_peer_id(discussion)] = {
                "entity": discussion,
                "channel_id": channel_id,
                "channel_title": getattr(channel, "title", str(channel_id)),
                "config": cfg,
            }
        except Exception:
            log.exception("discussion_registration_failed", channel_id=channel_id)

    async def catch_up(self):
        since = datetime.now(timezone.utc) - timedelta(hours=self.s.monitor_lookback_hours)
        for channel_id, cfg in self.channels.items():
            entity = self.entities[channel_id]
            async for msg in self.client.iter_messages(entity, offset_date=datetime.now(timezone.utc), reverse=False):
                if msg.date < since: break
                await self.process(msg, cfg)

    async def on_message(self, event):
        await self.process(event.message, self.channels.get(event.chat_id, {}))

    async def on_discussion_message(self, event):
        info = self.discussions.get(event.chat_id)
        if info:
            await self.process_lead(event.message, info)

    async def catch_up_leads(self):
        since = datetime.now(timezone.utc) - timedelta(hours=self.s.monitor_lookback_hours)
        for info in self.discussions.values():
            discussion_id = utils.get_peer_id(info["entity"])
            cursor_key = f"lead_scan_cursor:{discussion_id}"
            last_id = int(await self.db.meta(cursor_key) or 0)
            max_id = last_id
            async for msg in self.client.iter_messages(info["entity"], min_id=last_id):
                if not last_id and msg.date < since:
                    break
                await self.process_lead(msg, info)
                max_id = max(max_id, msg.id)
            if max_id > last_id:
                await self.db.set_meta(cursor_key, str(max_id))

    async def process_lead(self, msg, info):
        text = (msg.raw_text or "").strip()
        if not text or msg.action or msg.out or not msg.reply_to:
            return
        top_id = msg.reply_to.reply_to_top_id or msg.reply_to.reply_to_msg_id
        if not top_id or top_id == msg.id:
            return
        try:
            root = await self.client.get_messages(info["entity"], ids=top_id)
            if not root or root.sender_id != info["channel_id"]:
                return
            matched_terms = detect_lead(text)
            if not matched_terms:
                return
            sender = await msg.get_sender()
            if not sender or getattr(sender, "bot", False):
                return
            sender_name = " ".join(
                part for part in (
                    getattr(sender, "first_name", None),
                    getattr(sender, "last_name", None),
                ) if part
            ) or getattr(sender, "title", None) or "Неизвестный пользователь"
            sender_username = getattr(sender, "username", None)
            discussion_id = utils.get_peer_id(info["entity"])
            username = getattr(info["entity"], "username", None)
            if username:
                comment_url = f"https://t.me/{username}/{msg.id}"
            else:
                comment_url = f"https://t.me/c/{info['entity'].id}/{msg.id}"
            lead_id = await self.db.add_lead(
                discussion_id=discussion_id,
                message_id=msg.id,
                channel_id=info["channel_id"],
                channel_title=info["channel_title"],
                post_message_id=top_id,
                text=text,
                sender_id=utils.get_peer_id(sender),
                sender_name=sender_name,
                sender_username=sender_username,
                comment_url=comment_url,
                matched_terms=matched_terms,
            )
            if lead_id:
                lead = await self.db.one("SELECT * FROM leads WHERE id=?", (lead_id,))
                await self.bot.send_lead(lead)
                log.info("lead_detected", lead_id=lead_id, channel_id=info["channel_id"])
        except Exception:
            log.exception("lead_processing_failed", discussion_id=utils.get_peer_id(info["entity"]), message_id=msg.id)

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

    async def audit_loop(self):
        while True:
            await self.audit_published()
            await asyncio.sleep(self.s.published_audit_seconds)

    async def audit_published(self):
        rows = await self.db.all(
            "SELECT * FROM posts WHERE status='published' "
            "AND published_peer_id IS NOT NULL AND published_message_id IS NOT NULL"
        )
        for post in rows:
            checked_at = datetime.now(timezone.utc).isoformat()
            try:
                message = await self.client.get_messages(
                    post["published_peer_id"], ids=post["published_message_id"]
                )
                if message is None:
                    await self.db.execute(
                        "UPDATE posts SET status='deleted', deleted_at=?, last_checked_at=?, audit_error=NULL WHERE id=?",
                        (checked_at, checked_at, post["id"]),
                    )
                    log.warning("published_comment_deleted", post_id=post["id"])
                else:
                    await self.db.execute(
                        "UPDATE posts SET last_checked_at=?, audit_error=NULL WHERE id=?",
                        (checked_at, post["id"]),
                    )
            except Exception as exc:
                await self.db.execute(
                    "UPDATE posts SET last_checked_at=?, audit_error=? WHERE id=?",
                    (checked_at, str(exc)[:1000], post["id"]),
                )
                log.exception("published_comment_audit_failed", post_id=post["id"])

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
            sent = await self.client.send_message(discussion.peer_id, comment, reply_to=discussion.id, send_as=send_as)
            published_peer_id = utils.get_peer_id(discussion.peer_id)
            await self.db.execute(
                "UPDATE posts SET status='published', published_at=?, published_peer_id=?, "
                "published_message_id=?, last_checked_at=?, error=NULL, audit_error=NULL WHERE id=?",
                (datetime.now(timezone.utc).isoformat(), published_peer_id, sent.id,
                 datetime.now(timezone.utc).isoformat(), post["id"]),
            )
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
