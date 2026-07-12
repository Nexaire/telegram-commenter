import json
import random
from datetime import datetime, timedelta, timezone

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, CallbackQueryHandler, ContextTypes


class ApprovalBot:
    def __init__(self, settings, db):
        self.settings, self.db = settings, db
        self.app = Application.builder().token(settings.approval_bot_token).build()
        self.app.add_handler(CallbackQueryHandler(self.on_action, pattern=r"^(pub|next|skip):\d+$"))

    def keyboard(self, post_id):
        return InlineKeyboardMarkup([[
            InlineKeyboardButton("Опубликовать", callback_data=f"pub:{post_id}"),
            InlineKeyboardButton("Другой вариант", callback_data=f"next:{post_id}"),
            InlineKeyboardButton("Пропустить", callback_data=f"skip:{post_id}"),
        ]])

    async def send_for_approval(self, post_id: int):
        post = await self.db.one("SELECT * FROM posts WHERE id=?", (post_id,))
        variants = json.loads(post["variants_json"])
        body = f"Канал: {post['channel_title']}\n\nПост:\n{post['text'][:1500]}\n\nВариант 1/2:\n{variants[0]}"
        for user_id in self.settings.approver_user_ids:
            await self.app.bot.send_message(user_id, body, reply_markup=self.keyboard(post_id))

    async def on_action(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        q = update.callback_query
        if q.from_user.id not in self.settings.approver_user_ids:
            await q.answer("Нет доступа", show_alert=True); return
        action, raw_id = q.data.split(":")
        post_id = int(raw_id)
        post = await self.db.one("SELECT * FROM posts WHERE id=?", (post_id,))
        if not post or post["status"] not in ("pending",):
            await q.answer("Уже обработано", show_alert=True); return
        variants = json.loads(post["variants_json"])
        if action == "next":
            idx = (post["selected_variant"] + 1) % len(variants)
            await self.db.execute("UPDATE posts SET selected_variant=? WHERE id=?", (idx, post_id))
            await q.edit_message_text(f"Канал: {post['channel_title']}\n\nПост:\n{post['text'][:1500]}\n\nВариант {idx+1}/2:\n{variants[idx]}", reply_markup=self.keyboard(post_id))
            await q.answer(); return
        if action == "skip":
            await self.db.execute("UPDATE posts SET status='skipped' WHERE id=?", (post_id,))
            await q.edit_message_reply_markup(None); await q.answer("Пропущено"); return
        if await self.db.today_count() >= self.settings.daily_comment_limit:
            await q.answer("Достигнут суточный лимит", show_alert=True); return
        delay = random.randint(self.settings.publish_delay_min_seconds, self.settings.publish_delay_max_seconds)
        scheduled = datetime.now(timezone.utc) + timedelta(seconds=delay)
        await self.db.execute("UPDATE posts SET status='scheduled', scheduled_at=? WHERE id=?", (scheduled.isoformat(), post_id))
        await q.edit_message_reply_markup(None)
        await q.answer(f"Запланировано через {delay} сек.")

    async def start(self):
        await self.app.initialize(); await self.app.start(); await self.app.updater.start_polling(drop_pending_updates=True)

    async def stop(self):
        await self.app.updater.stop(); await self.app.stop(); await self.app.shutdown()

