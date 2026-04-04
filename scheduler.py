import logging
import os
from datetime import date, datetime, timedelta
from calendar import monthrange

from config import (
    ALLOWED_USER_IDS,
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


async def _send_all(bot, text: str) -> None:
    """Send a message to all allowed users."""
    for user_id in ALLOWED_USER_IDS:
        try:
            await bot.send_message(chat_id=user_id, text=text, parse_mode="Markdown")
        except Exception as exc:
            logger.error("Failed to send message to user %s: %s", user_id, exc)


# ---------------------------------------------------------------------------
# Daily summary
# ---------------------------------------------------------------------------

async def check_daily_summary(bot) -> None:
    """Send the daily summary if it's past DAILY_SUMMARY_HOUR and not yet sent."""
    today = date.today()
    today_str = today.isoformat()

    if datetime.now().hour < DAILY_SUMMARY_HOUR:
        return

    if _flag_exists(DAILY_FLAG_PREFIX, today_str):
        return

    expense_count = db.get_expense_count(today_str)
    if expense_count == 0:
        logger.info("No expenses today (%s), skipping daily summary", today_str)
        _create_flag(DAILY_FLAG_PREFIX, today_str)
        return

    try:
        expenses = sheets.get_rows_for_date(today_str)
    except RuntimeError as exc:
        logger.error("Failed to read Sheets for daily summary: %s", exc)
        # Create flag to avoid infinite retry loop
        _create_flag(DAILY_FLAG_PREFIX, today_str)
        await _send_all(bot, f"⚠️ Resumo diário de {today_str} não pôde ser gerado: erro ao ler o Sheets.")
        return

    try:
        summary = ai.generate_daily_summary(expenses, today_str)
    except RuntimeError as exc:
        logger.error("Failed to generate daily summary: %s", exc)
        _create_flag(DAILY_FLAG_PREFIX, today_str)
        await _send_all(bot, f"⚠️ Resumo diário de {today_str} não pôde ser gerado: {exc}")
        return

    await _send_all(bot, f"*Resumo do dia {today_str}*\n\n{summary}")
    _create_flag(DAILY_FLAG_PREFIX, today_str)
    logger.info("Daily summary sent for %s", today_str)


# ---------------------------------------------------------------------------
# Monthly summary
# ---------------------------------------------------------------------------

async def check_monthly_summary(bot) -> None:
    """Send the monthly summary on the 1st of each month for the previous month."""
    today = date.today()

    if today.day != 1:
        return

    first_this = today.replace(day=1)
    last_month_end = first_this - timedelta(days=1)
    year = last_month_end.year
    month = last_month_end.month
    key = f"{year:04d}-{month:02d}"

    if _flag_exists(MONTHLY_FLAG_PREFIX, key):
        return

    try:
        expenses = sheets.get_rows_for_month(year, month)
    except RuntimeError as exc:
        logger.error("Failed to read Sheets for monthly summary: %s", exc)
        _create_flag(MONTHLY_FLAG_PREFIX, key)
        await _send_all(bot, f"⚠️ Resumo mensal de {month:02d}/{year} não pôde ser gerado: erro ao ler o Sheets.")
        return

    try:
        summary = ai.generate_monthly_summary(expenses, year, month)
    except RuntimeError as exc:
        logger.error("Failed to generate monthly summary: %s", exc)
        _create_flag(MONTHLY_FLAG_PREFIX, key)
        await _send_all(bot, f"⚠️ Resumo mensal de {month:02d}/{year} não pôde ser gerado: {exc}")
        return

    await _send_all(bot, f"*Resumo mensal — {month:02d}/{year}*\n\n{summary}")
    _create_flag(MONTHLY_FLAG_PREFIX, key)
    logger.info("Monthly summary sent for %s", key)
