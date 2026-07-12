import json
import asyncio
import random
from collections import Counter
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

import structlog
from telegram import ForceReply, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler, filters

log = structlog.get_logger()


class ApprovalBot:
    def __init__(self, settings, db, generator):
        self.settings, self.db, self.generator = settings, db, generator
        self.app = Application.builder().token(settings.approval_bot_token).build()
        self.app.add_handler(CallbackQueryHandler(self.on_action, pattern=r"^(pub|next|edit|skip):\d+$"))
        self.app.add_handler(CommandHandler("cancel", self.cancel_edit))
        self.app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.on_edit_text))
        self.report_task = None

    def keyboard(self, post_id):
        return InlineKeyboardMarkup([
            [
                InlineKeyboardButton("Опубликовать", callback_data=f"pub:{post_id}"),
                InlineKeyboardButton("Другой вариант", callback_data=f"next:{post_id}"),
            ],
            [
                InlineKeyboardButton("Редактировать", callback_data=f"edit:{post_id}"),
                InlineKeyboardButton("Пропустить", callback_data=f"skip:{post_id}"),
            ],
        ])

    @staticmethod
    def approval_body(post, variants):
        idx = post["selected_variant"]
        return (
            f"Канал: {post['channel_title']}\n\nПост:\n{post['text'][:1500]}"
            f"\n\nВариант {idx + 1}/{len(variants)}:\n{variants[idx]}"
        )

    async def send_for_approval(self, post_id: int):
        post = await self.db.one("SELECT * FROM posts WHERE id=?", (post_id,))
        variants = json.loads(post["variants_json"])
        body = self.approval_body(post, variants)
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
            post["selected_variant"] = idx
            await q.edit_message_text(self.approval_body(post, variants), reply_markup=self.keyboard(post_id))
            await q.answer(); return
        if action == "edit":
            context.user_data["edit_post_id"] = post_id
            context.user_data["edit_chat_id"] = q.message.chat_id
            context.user_data["edit_message_id"] = q.message.message_id
            await q.answer()
            await q.message.reply_text(
                "Скопируйте комментарий ниже, отредактируйте и отправьте ответом. Для отмены: /cancel"
            )
            await q.message.reply_text(
                variants[post["selected_variant"]],
                reply_markup=ForceReply(
                    selective=True,
                    input_field_placeholder="Отредактированный комментарий",
                ),
            )
            return
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

    async def on_edit_text(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.effective_user.id not in self.settings.approver_user_ids:
            return
        post_id = context.user_data.get("edit_post_id")
        if not post_id:
            return
        post = await self.db.one("SELECT * FROM posts WHERE id=?", (post_id,))
        if not post or post["status"] != "pending":
            self.clear_edit(context)
            await update.message.reply_text("Карточка уже обработана, редактирование отменено.")
            return
        try:
            edited = self.generator.validate(update.message.text)
        except ValueError as exc:
            await update.message.reply_text(f"Текст не прошёл проверку: {exc}\nПришлите исправленный вариант.")
            return

        variants = json.loads(post["variants_json"])
        variants[post["selected_variant"]] = edited
        await self.db.execute(
            "UPDATE posts SET variants_json=? WHERE id=?",
            (json.dumps(variants, ensure_ascii=False), post_id),
        )
        await self.app.bot.edit_message_text(
            chat_id=context.user_data["edit_chat_id"],
            message_id=context.user_data["edit_message_id"],
            text=self.approval_body(post, variants),
            reply_markup=self.keyboard(post_id),
        )
        self.clear_edit(context)
        await update.message.reply_text("Комментарий обновлён. Теперь его можно опубликовать.")

    async def cancel_edit(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if context.user_data.get("edit_post_id"):
            self.clear_edit(context)
            await update.message.reply_text("Редактирование отменено.")

    @staticmethod
    def clear_edit(context):
        for key in ("edit_post_id", "edit_chat_id", "edit_message_id"):
            context.user_data.pop(key, None)

    async def start(self):
        await self.app.initialize(); await self.app.start(); await self.app.updater.start_polling(drop_pending_updates=True)
        self.report_task = asyncio.create_task(self.daily_report_loop())

    async def stop(self):
        if self.report_task:
            self.report_task.cancel()
            try:
                await self.report_task
            except asyncio.CancelledError:
                pass
        await self.app.updater.stop(); await self.app.stop(); await self.app.shutdown()

    async def daily_report_loop(self):
        while True:
            try:
                await self.send_due_daily_report()
            except Exception:
                # Ошибка отчёта не должна останавливать основной сервис.
                log.exception("daily_report_failed")
            await asyncio.sleep(60)

    async def send_due_daily_report(self):
        tz = ZoneInfo(self.settings.daily_report_timezone)
        now = datetime.now(tz)
        report_time = time(self.settings.daily_report_hour, self.settings.daily_report_minute)
        if now.time() < report_time:
            return
        report_date = now.date() - timedelta(days=1)
        if await self.db.meta("last_daily_report_date") == report_date.isoformat():
            return

        start_local = datetime.combine(report_date, time.min, tzinfo=tz)
        end_local = start_local + timedelta(days=1)
        start = start_local.astimezone(timezone.utc).isoformat()
        end = end_local.astimezone(timezone.utc).isoformat()
        rows = await self.db.all(
            "SELECT * FROM posts WHERE (created_at>=? AND created_at<?) "
            "OR (published_at>=? AND published_at<?) OR (deleted_at>=? AND deleted_at<?)",
            (start, end, start, end, start, end),
        )
        statuses = Counter(row["status"] for row in rows if start <= row["created_at"] < end)
        published = sum(1 for row in rows if row["published_at"] and start <= row["published_at"] < end)
        deleted = [row for row in rows if row["deleted_at"] and start <= row["deleted_at"] < end]
        failed = [row for row in rows if row["status"] in ("error", "permission_error")]

        lines = [
            f"Отчёт за {report_date.strftime('%d.%m.%Y')}",
            "",
            f"Опубликовано: {published}",
            f"Удалено: {len(deleted)}",
            f"Не опубликовано из-за ошибок: {len(failed)}",
            f"Пропущено вручную: {statuses['skipped']}",
            f"Отфильтровано: {statuses['filtered']}",
            f"Ожидает подтверждения: {statuses['pending']}",
            f"Запланировано: {statuses['scheduled']}",
            f"Dry run: {statuses['dry_run']}",
        ]
        if deleted:
            lines.extend(["", "Удалённые:"])
            lines.extend(f"• #{row['id']} — {row['channel_title']}" for row in deleted[:10])
        if failed:
            lines.extend(["", "Ошибки:"])
            lines.extend(
                f"• #{row['id']} — {row['channel_title']}: {(row['error'] or row['status'])[:160]}"
                for row in failed[:10]
            )
        report = "\n".join(lines)
        for user_id in self.settings.approver_user_ids:
            await self.app.bot.send_message(user_id, report)
        await self.db.set_meta("last_daily_report_date", report_date.isoformat())
