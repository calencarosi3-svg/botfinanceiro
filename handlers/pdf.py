import csv
import io
import logging
import os
import tempfile
from datetime import date

import pdfplumber
from telegram import Update
from telegram.ext import ContextTypes

from config import ALLOWED_USER_ID, SHEET_COLUMNS
from services import ai, sheets, db

logger = logging.getLogger(__name__)


async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    if ALLOWED_USER_ID and user_id != ALLOWED_USER_ID:
        await update.message.reply_text("Acesso negado.")
        return

    document = update.message.document
    filename = (document.file_name or "").lower()

    if not document or not (filename.endswith(".pdf") or filename.endswith(".csv")):
        await update.message.reply_text("Envie um arquivo PDF ou CSV de fatura.")
        return

    if filename.endswith(".csv"):
        await _handle_csv(update, context, document)
        return

    await update.message.reply_text("Baixando e processando PDF…")

    # Download PDF to a temp file
    try:
        tg_file = await context.bot.get_file(document.file_id)
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            await tg_file.download_to_drive(tmp.name)
            tmp_path = tmp.name
    except Exception as exc:
        logger.error("PDF download failed: %s", exc)
        await update.message.reply_text("Erro ao baixar o PDF.")
        return

    # Extract text
    try:
        pdf_text = _extract_text(tmp_path)
    except Exception as exc:
        logger.error("PDF text extraction failed: %s", exc)
        await update.message.reply_text("Erro ao ler o conteúdo do PDF.")
        return
    finally:
        os.unlink(tmp_path)

    if not pdf_text.strip():
        await update.message.reply_text("O PDF parece estar vazio ou protegido.")
        return

    # Extract expenses via AI
    try:
        expenses = ai.extract_from_pdf(pdf_text)
    except ValueError as exc:
        await update.message.reply_text(str(exc))
        return

    if not expenses:
        await update.message.reply_text("Nenhuma transação encontrada no PDF.")
        return

    # Fill missing dates with today
    for e in expenses:
        if not e.get("Data"):
            e["Data"] = date.today().isoformat()

    # Save to Sheets
    try:
        sheets.append_rows(expenses)
    except Exception as exc:
        logger.error("Sheets batch append failed: %s", exc)
        await update.message.reply_text(
            "Transações extraídas mas houve erro ao salvar no Sheets. "
            "Salvando apenas localmente."
        )

    # Save to SQLite
    count = db.record_expenses_bulk(expenses)

    total = sum(float(e.get("Valor", 0)) for e in expenses)
    await update.message.reply_text(
        f"PDF processado!\n"
        f"Transações importadas: {count}\n"
        f"Total: R$ {total:.2f}"
    )


def _extract_text(pdf_path: str) -> str:
    """Extract all text from a PDF file using pdfplumber."""
    pages = []
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            text = page.extract_text()
            if text:
                pages.append(text)
    return "\n".join(pages)


async def _handle_csv(update, context, document) -> None:
    await update.message.reply_text("Baixando e processando CSV…")

    try:
        tg_file = await context.bot.get_file(document.file_id)
        raw = await tg_file.download_as_bytearray()
        text = raw.decode("utf-8-sig")  # utf-8-sig strips BOM if present
    except Exception as exc:
        logger.error("CSV download failed: %s", exc)
        await update.message.reply_text("Erro ao baixar o CSV.")
        return

    try:
        expenses = _parse_csv(text)
    except Exception as exc:
        logger.error("CSV parse failed: %s", exc)
        await update.message.reply_text("Erro ao ler o CSV. Verifique o formato.")
        return

    if not expenses:
        await update.message.reply_text("Nenhuma transação encontrada no CSV.")
        return

    for e in expenses:
        if not e.get("Data"):
            e["Data"] = date.today().isoformat()

    try:
        sheets.append_rows(expenses)
    except Exception as exc:
        logger.error("Sheets batch append failed: %s", exc)
        await update.message.reply_text(
            "Transações extraídas mas houve erro ao salvar no Sheets. "
            "Salvando apenas localmente."
        )

    count = db.record_expenses_bulk(expenses)
    total = sum(float(e.get("Valor", 0)) for e in expenses)
    await update.message.reply_text(
        f"CSV processado!\n"
        f"Transações importadas: {count}\n"
        f"Total: R$ {total:.2f}"
    )


def _parse_csv(text: str) -> list[dict]:
    """
    Parse a CSV file into a list of expense dicts.

    Accepts two formats:
    1. Columns matching SHEET_COLUMNS (Data, Valor, Estabelecimento, …)
    2. Generic bank export with any columns — passed to AI for extraction.
    """
    reader = csv.DictReader(io.StringIO(text))
    rows = list(reader)
    if not rows:
        return []

    headers = [h.strip() for h in (reader.fieldnames or [])]

    # Format 1: headers already match our schema
    if "Data" in headers and "Valor" in headers:
        result = []
        for row in rows:
            cleaned = {k.strip(): v.strip() for k, v in row.items() if k}
            if not cleaned.get("Valor"):
                continue
            try:
                float(cleaned["Valor"].replace(",", "."))
            except ValueError:
                continue
            cleaned["Valor"] = cleaned["Valor"].replace(",", ".")
            result.append(cleaned)
        return result

    # Format 2: unknown headers — convert to text and let AI extract
    expenses = ai.extract_from_pdf(text)  # reuses same PDF extraction prompt
    return expenses
