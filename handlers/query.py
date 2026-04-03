import logging
import re
from datetime import date, timedelta
from calendar import monthrange

from telegram import Update
from telegram.ext import ContextTypes

from config import ALLOWED_USER_ID
from services import ai, sheets

logger = logging.getLogger(__name__)


def _parse_period(question: str) -> tuple[str, str, str]:
    """
    Tries to detect date period from the question text.
    Returns (start_date, end_date, context_label) as ISO strings.
    Defaults to current month if no period is detected.
    """
    lower = question.lower()
    today = date.today()

    # "mês passado" / "mes passado"
    if "mês passado" in lower or "mes passado" in lower:
        first_this = today.replace(day=1)
        last_month_end = first_this - timedelta(days=1)
        last_month_start = last_month_end.replace(day=1)
        return (
            last_month_start.isoformat(),
            last_month_end.isoformat(),
            f"{last_month_end.month:02d}/{last_month_end.year}",
        )

    # "esta semana" / "essa semana"
    if "semana" in lower:
        start = today - timedelta(days=today.weekday())
        return start.isoformat(), today.isoformat(), "esta semana"

    # "hoje"
    if "hoje" in lower:
        return today.isoformat(), today.isoformat(), "hoje"

    # "este mês" / "esse mês" or default
    year, month = today.year, today.month
    last_day = monthrange(year, month)[1]
    start = date(year, month, 1).isoformat()
    end = date(year, month, last_day).isoformat()
    return start, end, f"{month:02d}/{year}"


async def handle_query(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    question: str,
) -> None:
    user_id = update.effective_user.id
    if ALLOWED_USER_ID and user_id != ALLOWED_USER_ID:
        await update.message.reply_text("Acesso negado.")
        return

    await update.message.reply_text("Consultando dados…")

    start, end, label = _parse_period(question)

    try:
        expenses = sheets.get_rows_for_period(start, end)
    except Exception as exc:
        logger.error("Sheets read failed: %s", exc)
        await update.message.reply_text("Erro ao ler dados do Sheets.")
        return

    if not expenses:
        await update.message.reply_text(f"Nenhum gasto encontrado para {label}.")
        return

    try:
        answer = ai.answer_query(question, expenses, context=f"Período: {label}")
    except Exception as exc:
        logger.error("AI query failed: %s", exc)
        await update.message.reply_text("Erro ao gerar resposta.")
        return

    await update.message.reply_text(answer, parse_mode="Markdown")
