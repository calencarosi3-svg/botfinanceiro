import asyncio
import logging
import os
import sys

from telegram import Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    MessageHandler,
    filters,
)

from config import TELEGRAM_TOKEN, ALLOWED_USER_ID, LOGS_DIR
from services.db import init_db
from handlers.text import handle_text, handle_text_callback
from handlers.pdf import handle_document, handle_confirmation_callback
import scheduler

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(os.path.join(LOGS_DIR, "bot.log"), encoding="utf-8"),
    ],
)
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Command handlers
# ---------------------------------------------------------------------------

async def cmd_start(update: Update, context) -> None:
    if ALLOWED_USER_ID and update.effective_user.id != ALLOWED_USER_ID:
        return
    await update.message.reply_text(
        "Bot financeiro ativo!\n\n"
        "Como usar:\n"
        "• Envie um texto com o gasto (ex: 'gastei 50 reais no mercado')\n"
        "• Envie um PDF de fatura de cartão\n"
        "• Pergunte sobre seus gastos (ex: 'quanto gastei este mês?')"
    )


async def cmd_help(update: Update, context) -> None:
    if ALLOWED_USER_ID and update.effective_user.id != ALLOWED_USER_ID:
        return
    await update.message.reply_text(
        "*Comandos disponíveis:*\n\n"
        "/start — Boas-vindas\n"
        "/help — Esta ajuda\n\n"
        "*Registrar gasto:*\n"
        "Envie uma mensagem de texto descrevendo o gasto.\n"
        "Ex: _'50 reais no Mercado Livre, débito no Nubank'_\n\n"
        "*Importar fatura:*\n"
        "Envie o PDF da fatura diretamente.\n\n"
        "*Consultas:*\n"
        "_'quanto gastei este mês'_\n"
        "_'resumo do mês passado'_\n"
        "_'total de hoje'_",
        parse_mode="Markdown",
    )


# ---------------------------------------------------------------------------
# Scheduler periodic job
# ---------------------------------------------------------------------------

async def _scheduler_job(context) -> None:
    bot = context.bot
    await scheduler.check_daily_summary(bot)
    await scheduler.check_monthly_summary(bot)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    if not TELEGRAM_TOKEN:
        logger.error("TELEGRAM_TOKEN not set")
        sys.exit(1)

    init_db()

    app = (
        Application.builder()
        .token(TELEGRAM_TOKEN)
        .build()
    )

    # Commands
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))

    # PDF and CSV documents
    app.add_handler(MessageHandler(filters.Document.PDF | filters.Document.MimeType("text/csv"), handle_document))

    # Text messages (expenses + queries)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    # Confirmation callbacks (buttons)
    app.add_handler(CallbackQueryHandler(handle_text_callback, pattern=r"^(confirm|cancel)_txt_"))
    app.add_handler(CallbackQueryHandler(handle_confirmation_callback, pattern=r"^(confirm|cancel|reprocess)_(csv|pdf)_"))

    # Scheduler: run every 60 seconds
    app.job_queue.run_repeating(_scheduler_job, interval=60, first=10)

    logger.info("Bot starting (polling)…")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
