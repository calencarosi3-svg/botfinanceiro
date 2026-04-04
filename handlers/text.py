import logging
from datetime import date

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import ContextTypes

from config import ALLOWED_USER_IDS
from services import ai, sheets, db
from handlers.query import handle_query

logger = logging.getLogger(__name__)

# Keywords that indicate a query rather than an expense
_QUERY_KEYWORDS = [
    "quanto", "gastei", "resumo", "análise", "analise", "total",
    "relatório", "relatorio", "mostre", "mostra", "quanto gastei",
    "mês passado", "mes passado", "este mês", "esse mês",
    "hoje", "semana", "categoria",
]


def _looks_like_query(text: str) -> bool:
    lower = text.lower()
    return any(kw in lower for kw in _QUERY_KEYWORDS)


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    user_id = update.effective_user.id
    if ALLOWED_USER_IDS and user_id not in ALLOWED_USER_IDS:
        await update.message.reply_text("Acesso negado.")
        return

    text = update.message.text.strip()
    if not text:
        return

    if _looks_like_query(text):
        await handle_query(update, context, question=text)
        return

    await update.message.reply_text("Processando gasto…")
    try:
        expense = ai.extract_from_text(text)
    except ValueError as exc:
        await update.message.reply_text(f"Não entendi o gasto: {exc}")
        return

    if not expense.get("Data"):
        expense["Data"] = date.today().isoformat()

    # Store pending and ask for confirmation
    key = f"txt_{update.message.message_id}"
    context.user_data[f"pending_{key}"] = [expense]

    valor = float(expense.get("Valor", 0))
    estabelecimento = expense.get("Estabelecimento", "?")
    categoria = expense.get("Categoria", "")
    banco = expense.get("Banco", "")
    tipo = expense.get("Tipo", "")
    data = expense.get("Data", "")

    lines = [
        "*Confirme o gasto:*\n",
        f"Data: {data}",
        f"Valor: R$ {valor:.2f}",
        f"Estabelecimento: {estabelecimento}",
        f"Categoria: {categoria}",
    ]
    if banco:
        lines.append(f"Banco: {banco}")
    if tipo:
        lines.append(f"Tipo: {tipo}")
    if expense.get("Obs"):
        lines.append(f"Obs: {expense['Obs']}")

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("Confirmar", callback_data=f"confirm_{key}"),
            InlineKeyboardButton("Cancelar", callback_data=f"cancel_{key}"),
        ],
    ])

    await update.message.reply_text(
        "\n".join(lines),
        reply_markup=keyboard,
        parse_mode="Markdown",
    )


# ---------------------------------------------------------------------------
# Callback handler for text expense confirmations
# ---------------------------------------------------------------------------

async def handle_text_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    data = query.data

    if data.startswith("confirm_txt_"):
        key = data.replace("confirm_", "")
        expenses = context.user_data.pop(f"pending_{key}", None)
        if not expenses or not expenses[0]:
            await query.edit_message_text("Dados expirados. Envie novamente.")
            return

        expense = expenses[0]
        warnings = []

        sheets_ok = True
        try:
            sheets.append_row(expense)
        except RuntimeError as exc:
            logger.error("Sheets append failed: %s", exc)
            warnings.append(f"Google Sheets: {exc}")
            sheets_ok = False

        try:
            db.record_expense(
                expense_date=expense.get("Data", date.today().isoformat()),
                valor=float(expense.get("Valor", 0)),
                estabelecimento=expense.get("Estabelecimento", ""),
                categoria=expense.get("Categoria", ""),
                banco=expense.get("Banco", ""),
                tipo=expense.get("Tipo", ""),
                obs=expense.get("Obs", ""),
            )
        except RuntimeError as exc:
            logger.critical("SQLite insert failed: %s", exc)
            if not sheets_ok:
                warnings.append("Banco local: gasto NÃO foi salvo em lugar nenhum!")
            else:
                warnings.append("Banco local: falha ao salvar localmente, mas foi salvo no Sheets.")

        valor = float(expense.get("Valor", 0))
        msg = f"Gasto registrado! R$ {valor:.2f} — {expense.get('Estabelecimento', '?')}"
        if warnings:
            msg += "\n\n⚠️ " + "\n⚠️ ".join(warnings)
        await query.edit_message_text(msg)

    elif data.startswith("cancel_txt_"):
        key = data.replace("cancel_", "")
        context.user_data.pop(f"pending_{key}", None)
        await query.edit_message_text("Gasto cancelado.")
