import csv
import io
import json
import logging
import os
import tempfile
from datetime import date

import pdfplumber
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from config import ALLOWED_USER_ID, SHEET_COLUMNS
from services import ai, sheets, db

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Confirmation helpers
# ---------------------------------------------------------------------------

def _build_preview(expenses: list[dict], source: str) -> str:
    """Build a summary message for user confirmation."""
    total = sum(float(e.get("Valor", 0)) for e in expenses)
    lines = [
        f"*{source} processado — Confirme a inserção:*\n",
        f"Transações: {len(expenses)}",
        f"Total: R$ {total:,.2f}\n",
        "*Amostra (primeiros 5):*",
    ]
    for e in expenses[:5]:
        valor = float(e.get("Valor", 0))
        desc = e.get("Estabelecimento", "?")[:30]
        data = e.get("Data", "")
        lines.append(f"  {data}  R$ {valor:.2f}  {desc}")
    if len(expenses) > 5:
        lines.append(f"  … e mais {len(expenses) - 5} transações")
    return "\n".join(lines)


def _confirmation_keyboard(key: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("Confirmar", callback_data=f"confirm_{key}"),
            InlineKeyboardButton("Cancelar", callback_data=f"cancel_{key}"),
        ],
        [
            InlineKeyboardButton("Reprocessar (IA)", callback_data=f"reprocess_{key}"),
        ],
    ])


async def save_expenses(expenses: list[dict], context_label: str) -> tuple[int, str]:
    """Save expenses to Sheets + SQLite. Returns (count, error_msg or '')."""
    error = ""
    try:
        sheets.append_rows(expenses)
    except Exception as exc:
        logger.error("Sheets batch append failed: %s", exc)
        error = "Salvo localmente, mas houve erro ao salvar no Sheets."

    count = db.record_expenses_bulk(expenses)
    total = sum(float(e.get("Valor", 0)) for e in expenses)
    return count, error


# ---------------------------------------------------------------------------
# Callback handler (called from bot.py)
# ---------------------------------------------------------------------------

async def handle_confirmation_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle confirm/cancel/reprocess button presses."""
    query = update.callback_query
    await query.answer()
    data = query.data

    if data.startswith("confirm_"):
        key = data.replace("confirm_", "")
        expenses = context.user_data.pop(f"pending_{key}", None)
        if not expenses:
            await query.edit_message_text("Dados expirados. Envie novamente.")
            return

        count, error = await save_expenses(expenses, key)
        total = sum(float(e.get("Valor", 0)) for e in expenses)
        msg = f"Inserido! {count} transações — R$ {total:,.2f}"
        if error:
            msg += f"\n{error}"
        await query.edit_message_text(msg)

    elif data.startswith("cancel_"):
        key = data.replace("cancel_", "")
        context.user_data.pop(f"pending_{key}", None)
        await query.edit_message_text("Inserção cancelada.")

    elif data.startswith("reprocess_"):
        key = data.replace("reprocess_", "")
        expenses = context.user_data.get(f"pending_{key}")
        if not expenses:
            await query.edit_message_text("Dados expirados. Envie novamente.")
            return

        await query.edit_message_text("Reprocessando com IA…")
        raw_text = json.dumps(expenses, ensure_ascii=False)
        try:
            reprocessed = ai.extract_from_pdf(raw_text)
        except Exception as exc:
            logger.error("Reprocess failed: %s", exc)
            await context.bot.send_message(
                chat_id=query.message.chat_id,
                text="Erro ao reprocessar. Tente enviar o arquivo novamente.",
            )
            return

        for e in reprocessed:
            if not e.get("Data"):
                e["Data"] = date.today().isoformat()

        context.user_data[f"pending_{key}"] = reprocessed
        preview = _build_preview(reprocessed, "Reprocessado")
        keyboard = _confirmation_keyboard(key)
        await context.bot.send_message(
            chat_id=query.message.chat_id,
            text=preview,
            reply_markup=keyboard,
            parse_mode="Markdown",
        )


# ---------------------------------------------------------------------------
# Document handler
# ---------------------------------------------------------------------------

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

    await _handle_pdf(update, context, document)


# ---------------------------------------------------------------------------
# PDF flow
# ---------------------------------------------------------------------------

async def _handle_pdf(update, context, document) -> None:
    await update.message.reply_text("Baixando e processando PDF…")

    try:
        tg_file = await context.bot.get_file(document.file_id)
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            await tg_file.download_to_drive(tmp.name)
            tmp_path = tmp.name
    except Exception as exc:
        logger.error("PDF download failed: %s", exc)
        await update.message.reply_text("Erro ao baixar o PDF.")
        return

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

    try:
        expenses = ai.extract_from_pdf(pdf_text)
    except ValueError as exc:
        await update.message.reply_text(str(exc))
        return

    if not expenses:
        await update.message.reply_text("Nenhuma transação encontrada no PDF.")
        return

    for e in expenses:
        if not e.get("Data"):
            e["Data"] = date.today().isoformat()

    # Store pending and ask for confirmation
    key = f"pdf_{update.message.message_id}"
    context.user_data[f"pending_{key}"] = expenses
    preview = _build_preview(expenses, "PDF")
    keyboard = _confirmation_keyboard(key)
    await update.message.reply_text(preview, reply_markup=keyboard, parse_mode="Markdown")


def _extract_text(pdf_path: str) -> str:
    pages = []
    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            text = page.extract_text()
            if text:
                pages.append(text)
    return "\n".join(pages)


# ---------------------------------------------------------------------------
# CSV flow
# ---------------------------------------------------------------------------

async def _handle_csv(update, context, document) -> None:
    await update.message.reply_text("Baixando e processando CSV…")

    try:
        tg_file = await context.bot.get_file(document.file_id)
        raw = await tg_file.download_as_bytearray()
        text = raw.decode("utf-8-sig")
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

    # Store pending and ask for confirmation
    key = f"csv_{update.message.message_id}"
    context.user_data[f"pending_{key}"] = expenses
    preview = _build_preview(expenses, "CSV")
    keyboard = _confirmation_keyboard(key)
    await update.message.reply_text(preview, reply_markup=keyboard, parse_mode="Markdown")


# ---------------------------------------------------------------------------
# CSV parser
# ---------------------------------------------------------------------------

def _parse_csv(text: str) -> list[dict]:
    """Parse a CSV into a list of expense dicts using column aliases."""
    reader = csv.DictReader(io.StringIO(text))
    rows = list(reader)
    if not rows:
        return []

    headers = [h.strip() for h in (reader.fieldnames or [])]
    h_lower = {h.lower(): h for h in headers}

    _DATE_ALIASES    = ["data", "date", "dt", "data lançamento", "data pagamento"]
    _VALOR_ALIASES   = ["valor", "value", "amount", "quantia", "vlr", "total"]
    _DESC_ALIASES    = ["estabelecimento", "lançamento", "lancamento", "descrição",
                        "descricao", "description", "historico", "histórico",
                        "comercio", "comércio", "memo", "nome", "title", "titulo",
                        "título"]
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

        data_raw = r.get(col_data, "") if col_data else ""
        data = _parse_date(data_raw)
        desc = r.get(col_desc, "") if col_desc else ""

        # Extract parcela info from description (e.g. "Produto - Parcela 1/3")
        obs = ""
        desc_lower = desc.lower()
        if " - parcela " in desc_lower:
            idx = desc_lower.index(" - parcela ")
            obs = desc[idx + 3:]  # "Parcela 1/3"
            desc = desc[:idx]     # "Produto"
        elif col_tipo:
            tipo_raw = r.get(col_tipo, "").lower()
            if "parcela" in tipo_raw:
                obs = tipo_raw

        cat_raw = r.get(col_cat, "").lower() if col_cat else ""
        categoria = _CAT_MAP.get(cat_raw, "Outros")
        banco = r.get(col_banco, "") if col_banco else ""

        result.append({
            "Data": data,
            "Valor": str(valor),
            "Estabelecimento": desc,
            "Categoria": categoria,
            "Banco": banco,
            "Tipo": "crédito",
            "Obs": obs,
        })

    return result


# ---------------------------------------------------------------------------
# Value & date parsers
# ---------------------------------------------------------------------------

def _parse_valor(raw: str) -> float | None:
    """Convert 'R$ 3,50', '-R$ 2.150,55', or '18.99' to float.

    Distinguishes BR format (comma decimal) from US format (dot decimal):
    - '1.234,56' → 1234.56  (BR full)
    - '18,99'    → 18.99    (BR simple)
    - '18.99'    → 18.99    (US decimal)
    - '1899'     → 1899.0   (integer)
    """
    if not raw:
        return None
    cleaned = raw.replace("R$", "").replace(" ", "")
    has_comma = "," in cleaned
    has_dot = "." in cleaned

    if has_comma and has_dot:
        # BR full format: 1.234,56
        cleaned = cleaned.replace(".", "").replace(",", ".")
    elif has_comma:
        # BR simple: 18,99
        cleaned = cleaned.replace(",", ".")
    # else: US format (18.99) or integer (1899)

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
