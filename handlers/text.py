import logging
from datetime import date

from telegram import Update
from telegram.ext import ContextTypes

from config import ALLOWED_USER_ID
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
    if ALLOWED_USER_ID and user_id != ALLOWED_USER_ID:
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

    # Fill missing date with today
    if not expense.get("Data"):
        expense["Data"] = date.today().isoformat()

    try:
        sheets.append_row(expense)
    except Exception as exc:
        logger.error("Sheets append failed: %s", exc)
        await update.message.reply_text(
            "Gasto extraído mas não salvei no Sheets (erro de conexão). "
            "Salvando apenas localmente."
        )

    db.record_expense(
        expense_date=expense.get("Data", date.today().isoformat()),
        valor=float(expense.get("Valor", 0)),
        estabelecimento=expense.get("Estabelecimento", ""),
        categoria=expense.get("Categoria", ""),
        banco=expense.get("Banco", ""),
        tipo=expense.get("Tipo", ""),
        obs=expense.get("Obs", ""),
    )

    valor = expense.get("Valor", 0)
    estabelecimento = expense.get("Estabelecimento", "?")
    categoria = expense.get("Categoria", "")
    banco = expense.get("Banco", "")
    tipo = expense.get("Tipo", "")
    data = expense.get("Data", "")

    lines = [
        f"Gasto registrado!",
        f"Data: {data}",
        f"Valor: R$ {float(valor):.2f}",
        f"Estabelecimento: {estabelecimento}",
        f"Categoria: {categoria}",
    ]
    if banco:
        lines.append(f"Banco: {banco}")
    if tipo:
        lines.append(f"Tipo: {tipo}")
    if expense.get("Obs"):
        lines.append(f"Obs: {expense['Obs']}")

    await update.message.reply_text("\n".join(lines))
