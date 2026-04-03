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
    Parse a CSV into a list of expense dicts.

    Strategy:
    1. Try to find columns for date, amount, and description using common aliases.
    2. For each row: clean valor with _parse_valor, date with _parse_date, skip negatives.
    3. If no recognizable columns found, fall back to AI extraction.
    """
    reader = csv.DictReader(io.StringIO(text))
    rows = list(reader)
    if not rows:
        return []

    headers = [h.strip() for h in (reader.fieldnames or [])]
    h_lower = {h.lower(): h for h in headers}

    # --- Column aliases ---
    _DATE_ALIASES    = ["data", "date", "dt", "data lançamento", "data pagamento"]
    _VALOR_ALIASES   = ["valor", "value", "amount", "quantia", "vlr", "total"]
    _DESC_ALIASES    = ["estabelecimento", "lançamento", "lancamento", "descrição",
                        "descricao", "description", "historico", "histórico",
                        "comercio", "comércio", "memo", "nome"]
    _CAT_ALIASES     = ["categoria", "category"]
    _BANCO_ALIASES   = ["banco", "bank", "instituição", "instituicao"]
    _TIPO_ALIASES    = ["tipo", "type", "modalidade", "forma pagamento"]

    def _find(aliases) -> str | None:
        for a in aliases:
            if a in h_lower:
                return h_lower[a]
        return None

    col_data  = _find(_DATE_ALIASES)
    col_valor = _find(_VALOR_ALIASES)
    col_desc  = _find(_DESC_ALIASES)
    col_cat   = _find(_CAT_ALIASES)
    col_banco = _find(_BANCO_ALIASES)
    col_tipo  = _find(_TIPO_ALIASES)

    if not col_valor:
        # No recognizable amount column — let AI handle it
        return ai.extract_from_pdf(text)

    _CAT_MAP = {
        "restaurantes": "Alimentação",
        "supermercado": "Alimentação",
        "alimentacao": "Alimentação",
        "alimentação": "Alimentação",
        "transporte": "Transporte",
        "servicos": "Serviços",
        "serviços": "Serviços",
        "compras": "Outros",
        "saude": "Saúde",
        "saúde": "Saúde",
        "drogaria": "Saúde",
        "petshop": "Outros",
        "lazer": "Lazer",
        "moradia": "Moradia",
        "vestuario": "Vestuário",
        "vestuário": "Vestuário",
        "educacao": "Educação",
        "educação": "Educação",
        "outros": "Outros",
    }

    result = []
    for row in rows:
        r = {k.strip(): (v.strip() if v else "") for k, v in row.items() if k}

        valor = _parse_valor(r.get(col_valor, ""))
        if valor is None or valor <= 0:
            continue

        data  = _parse_date(r.get(col_data, "") if col_data else "")
        desc  = r.get(col_desc, "") if col_desc else ""
        cat_raw = r.get(col_cat, "").lower() if col_cat else ""
        categoria = _CAT_MAP.get(cat_raw, "Outros")
        banco = r.get(col_banco, "") if col_banco else ""
        tipo_raw = r.get(col_tipo, "").lower() if col_tipo else ""
        tipo  = "crédito"
        obs   = tipo_raw if "parcela" in tipo_raw else ""

        result.append({
            "Data": data,
            "Valor": str(valor),
            "Estabelecimento": desc,
            "Categoria": categoria,
            "Banco": banco,
            "Tipo": tipo,
            "Obs": obs,
        })

    return result


def _parse_valor(raw: str) -> float | None:
    """Convert 'R$ 3,50' or '-R$ 2.150,55' to float. Returns None if unparseable."""
    if not raw:
        return None
    cleaned = raw.replace("R$", "").replace(" ", "").replace(".", "").replace(",", ".")
    try:
        return float(cleaned)
    except ValueError:
        return None


def _parse_date(raw: str) -> str:
    """Convert DD/MM/YYYY to YYYY-MM-DD. Returns raw if already ISO or unparseable."""
    if not raw:
        return date.today().isoformat()
    if "/" in raw:
        parts = raw.split("/")
        if len(parts) == 3:
            d, m, y = parts
            return f"{y}-{m.zfill(2)}-{d.zfill(2)}"
    return raw
