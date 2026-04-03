import logging
import os
from datetime import date, timedelta
from calendar import monthrange

from config import (
    ALLOWED_USER_ID,
    DATA_DIR,
    DAILY_FLAG_PREFIX,
    MONTHLY_FLAG_PREFIX,
    DAILY_SUMMARY_HOUR,
)
from services import ai, sheets, db

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Flag file helpers
# ---------------------------------------------------------------------------

def _flag_path(prefix: str, key: str) -> str:
    return os.path.join(DATA_DIR, f"{prefix}{key}")


def _flag_exists(prefix: str, key: str) -> bool:
    return os.path.exists(_flag_path(prefix, key))


def _create_flag(prefix: str, key: str) -> None:
    open(_flag_path(prefix, key), "w").close()


# ---------------------------------------------------------------------------
# Daily summary
# ---------------------------------------------------------------------------

async def check_daily_summary(bot) -> None:
    """Send the daily summary if it's past DAILY_SUMMARY_HOUR and not yet sent."""
    now = date.today()
    from datetime import datetime
    current_hour = datetime.now().hour

    if current_hour < DAILY_SUMMARY_HOUR:
        return

    today_str = now.isoformat()
    if _flag_exists(DAILY_FLAG_PREFIX, today_str):
        return

    expense_count = db.get_expense_count(today_str)
    if expense_count == 0:
        logger.info("No expenses today (%s), skipping daily summary", today_str)
        _create_flag(DAILY_FLAG_PREFIX, today_str)
        return

    try:
        expenses = sheets.get_rows_for_date(today_str)
    except Exception as exc:
        logger.error("Failed to read Sheets for daily summary: %s", exc)
        return

    try:
        summary = ai.generate_daily_summary(expenses, today_str)
    except Exception as exc:
        logger.error("Failed to generate daily summary: %s", exc)
        return

    try:
        await bot.send_message(
            chat_id=ALLOWED_USER_ID,
            text=f"*Resumo do dia {today_str}*\n\n{summary}",
            parse_mode="Markdown",
        )
        _create_flag(DAILY_FLAG_PREFIX, today_str)
        logger.info("Daily summary sent for %s", today_str)
    except Exception as exc:
        logger.error("Failed to send daily summary: %s", exc)


# ---------------------------------------------------------------------------
# Monthly summary
# ---------------------------------------------------------------------------

async def check_monthly_summary(bot) -> None:
    """Send the monthly summary on the 1st of each month for the previous month."""
    today = date.today()

    if today.day != 1:
        return

    # Previous month
    first_this = today.replace(day=1)
    last_month_end = first_this - timedelta(days=1)
    year = last_month_end.year
    month = last_month_end.month
    key = f"{year:04d}-{month:02d}"

    if _flag_exists(MONTHLY_FLAG_PREFIX, key):
        return

    try:
        expenses = sheets.get_rows_for_month(year, month)
    except Exception as exc:
        logger.error("Failed to read Sheets for monthly summary: %s", exc)
        return

    try:
        summary = ai.generate_monthly_summary(expenses, year, month)
    except Exception as exc:
        logger.error("Failed to generate monthly summary: %s", exc)
        return

    try:
        await bot.send_message(
            chat_id=ALLOWED_USER_ID,
            text=f"*Resumo mensal — {month:02d}/{year}*\n\n{summary}",
            parse_mode="Markdown",
        )
        _create_flag(MONTHLY_FLAG_PREFIX, key)
        logger.info("Monthly summary sent for %s", key)
    except Exception as exc:
        logger.error("Failed to send monthly summary: %s", exc)
