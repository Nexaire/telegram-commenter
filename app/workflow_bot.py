import asyncio
import html
from contextlib import suppress
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import structlog
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, LinkPreviewOptions
from telegram.error import RetryAfter
from telegram.ext import Application, CallbackQueryHandler

log = structlog.get_logger()
REPORT_META_KEY = "last_workflow_report_date"
TELEGRAM_MESSAGE_LIMIT = 4096

TRANSITIONS = {
    ("new", "work"): "in_progress",
    ("new", "irrelevant"): "irrelevant",
    ("in_progress", "agreed"): "agreed",
    ("in_progress", "rejected"): "rejected",
}


def keyboard_for(
    match_id: int, status: str, source_url: str | None
) -> InlineKeyboardMarkup | None:
    rows = []
    if source_url:
        rows.append([InlineKeyboardButton("Открыть", url=source_url)])
    if status == "new":
        rows.append(
            [
                InlineKeyboardButton("В работу", callback_data=f"lead:work:{match_id}"),
                InlineKeyboardButton(
                    "Не заявка", callback_data=f"lead:irrelevant:{match_id}"
                ),
            ]
        )
    elif status == "in_progress":
        rows.append(
            [
                InlineKeyboardButton(
                    "Договорились", callback_data=f"lead:agreed:{match_id}"
                ),
                InlineKeyboardButton(
                    "Отказ", callback_data=f"lead:rejected:{match_id}"
                ),
            ]
        )
    return InlineKeyboardMarkup(rows) if rows else None


def split_report(header: str, lead_lines: list[str]) -> list[str]:
    """Split a report without cutting HTML entities or omitting new leads."""
    chunks = [header]
    if not lead_lines:
        chunks[0] += "\n\nНовых необработанных заявок нет."
        return chunks

    intro = "\n\n<b>Все заявки в статусе «Новые»:</b>"
    chunks[0] += intro
    for line in lead_lines:
        addition = "\n\n" + line
        if len(chunks[-1]) + len(addition) <= TELEGRAM_MESSAGE_LIMIT:
            chunks[-1] += addition
        else:
            chunks.append("<b>Новые — продолжение:</b>" + addition)
    return chunks


class LeadWorkflowBot:
    def __init__(self, settings, db):
        self.settings = settings
        self.db = db
        self.application = None
        self.report_task = None
        self.target_chat_id = settings.monitor_config()["target_channel"]
        self.manager_ids = settings.manager_ids
        try:
            self.report_tz = ZoneInfo(settings.report_timezone)
        except ZoneInfoNotFoundError as exc:
            raise ValueError(
                f"Unknown REPORT_TIMEZONE: {settings.report_timezone}"
            ) from exc

    async def start(self):
        if not self.settings.lead_bot_token:
            raise ValueError("LEAD_BOT_TOKEN is required")
        self.application = (
            Application.builder().token(self.settings.lead_bot_token).build()
        )
        self.application.add_handler(
            CallbackQueryHandler(self.on_callback, pattern=r"^lead:[a-z_]+:\d+$")
        )
        await self.application.initialize()
        await self.application.start()
        if self.application.updater is None:
            raise RuntimeError("Telegram bot updater is unavailable")
        await self.application.updater.start_polling(drop_pending_updates=False)
        self.report_task = asyncio.create_task(
            self.report_loop(), name="daily-lead-report"
        )
        me = await self.application.bot.get_me()
        log.info("workflow_bot_started", username=me.username)

    async def stop(self):
        if self.report_task:
            self.report_task.cancel()
            with suppress(asyncio.CancelledError):
                await self.report_task
        if not self.application:
            return
        if self.application.updater and self.application.updater.running:
            await self.application.updater.stop()
        if self.application.running:
            await self.application.stop()
        await self.application.shutdown()

    async def publish_match(self, match: dict, body: str):
        if not self.application:
            raise RuntimeError("Workflow bot is not started")
        return await self.send_message(
            text=body,
            reply_markup=keyboard_for(
                match["id"], match["status"], match["source_url"]
            ),
        )

    async def send_message(self, *, text: str, reply_markup=None):
        if not self.application:
            raise RuntimeError("Workflow bot is not started")
        while True:
            try:
                return await self.application.bot.send_message(
                    chat_id=self.target_chat_id,
                    text=text,
                    parse_mode="HTML",
                    reply_markup=reply_markup,
                    link_preview_options=LinkPreviewOptions(is_disabled=True),
                )
            except RetryAfter as exc:
                delay = float(exc.retry_after) + 1
                log.warning("bot_flood_wait", seconds=delay)
                await asyncio.sleep(delay)

    async def on_callback(self, update, context):
        del context
        query = update.callback_query
        if query is None or query.from_user is None:
            return
        if query.from_user.id not in self.manager_ids:
            await query.answer("Нет доступа", show_alert=True)
            return

        _, action, raw_match_id = query.data.split(":", 2)
        match_id = int(raw_match_id)
        match = await self.db.one("SELECT * FROM matches WHERE id=?", (match_id,))
        if not match:
            await query.answer("Заявка не найдена", show_alert=True)
            return

        new_status = TRANSITIONS.get((match["status"], action))
        if new_status is None:
            await query.answer("Статус уже изменён")
            await query.edit_message_reply_markup(
                reply_markup=keyboard_for(
                    match_id, match["status"], match["source_url"]
                )
            )
            return

        changed = await self.db.transition_match(match_id, match["status"], new_status)
        if not changed:
            match = await self.db.one("SELECT * FROM matches WHERE id=?", (match_id,))
            await query.answer("Статус уже изменён")
            if match:
                await query.edit_message_reply_markup(
                    reply_markup=keyboard_for(
                        match_id, match["status"], match["source_url"]
                    )
                )
            return

        await query.edit_message_reply_markup(
            reply_markup=keyboard_for(match_id, new_status, match["source_url"])
        )
        await query.answer("Статус обновлён")
        log.info(
            "lead_status_changed",
            match_id=match_id,
            old_status=match["status"],
            new_status=new_status,
            user_id=query.from_user.id,
        )

    async def report_loop(self):
        while True:
            try:
                await self.send_report_if_due()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("daily_report_failed")
            await asyncio.sleep(60)

    async def send_report_if_due(self, now: datetime | None = None) -> bool:
        local_now = (
            now.astimezone(self.report_tz) if now else datetime.now(self.report_tz)
        )
        due = local_now.replace(
            hour=self.settings.report_hour,
            minute=self.settings.report_minute,
            second=0,
            microsecond=0,
        )
        report_date = local_now.date().isoformat()
        if local_now < due or await self.db.meta(REPORT_META_KEY) == report_date:
            return False

        end_utc = local_now.astimezone(timezone.utc)
        start_utc = end_utc - timedelta(hours=24)
        counts = await self.db.one(
            "SELECT COUNT(*) AS total, "
            "SUM(CASE WHEN status='new' THEN 1 ELSE 0 END) AS new_count, "
            "SUM(CASE WHEN status='in_progress' THEN 1 ELSE 0 END) AS work_count, "
            "SUM(CASE WHEN status='irrelevant' THEN 1 ELSE 0 END) AS irrelevant_count "
            "FROM matches WHERE delivery_status='published' "
            "AND created_at>=? AND created_at<?",
            (start_utc.isoformat(), end_utc.isoformat()),
        )
        new_rows = await self.db.all(
            "SELECT source_title,source_url,text FROM matches "
            "WHERE delivery_status='published' AND status='new' "
            "ORDER BY created_at",
        )
        header = (
            "📊 <b>Отчёт по заявкам за сутки</b>"
            f"\n\nНайдено заявок: <b>{counts['total'] or 0}</b>"
            f"\nНовые: <b>{counts['new_count'] or 0}</b>"
            f"\nВ работе: <b>{counts['work_count'] or 0}</b>"
            f"\nНе заявки: <b>{counts['irrelevant_count'] or 0}</b>"
            f"\n\nВсего новых в очереди: <b>{len(new_rows)}</b>"
        )
        lines = [
            self.report_lead_line(index, row) for index, row in enumerate(new_rows, 1)
        ]
        for chunk in split_report(header, lines):
            await self.send_message(text=chunk)
        await self.db.set_meta(REPORT_META_KEY, report_date)
        log.info("daily_report_sent", report_date=report_date, new_total=len(new_rows))
        return True

    @staticmethod
    def report_lead_line(index: int, row: dict) -> str:
        title = html.escape(row["source_title"])
        preview = html.escape(" ".join(row["text"].split()))
        if len(preview) > 220:
            preview = preview[:219].rstrip() + "…"
        if row["source_url"]:
            title = (
                f'<a href="{html.escape(row["source_url"], quote=True)}">{title}</a>'
            )
        return f"<b>{index}. {title}</b>\n{preview}"
