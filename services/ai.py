import json
import logging
import re
from datetime import date

import anthropic

from config import ANTHROPIC_API_KEY, CLAUDE_MODEL

logger = logging.getLogger(__name__)

_client: anthropic.Anthropic | None = None


def _get_client() -> anthropic.Anthropic:
    global _client
    if _client is None:
        _client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    return _client


def _chat(system: str, user: str, max_tokens: int = 1024) -> str:
    response = _get_client().messages.create(
        model=CLAUDE_MODEL,
        max_tokens=max_tokens,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    return response.content[0].text.strip()


# ---------------------------------------------------------------------------
# Extraction helpers
# ---------------------------------------------------------------------------

_EXPENSE_SYSTEM = """Você é um assistente de finanças pessoais.
Sua única tarefa é extrair dados de gastos de mensagens em português e retornar JSON válido.
Hoje é {today}.

Retorne APENAS um objeto JSON com as chaves:
  Data (YYYY-MM-DD), Valor (número), Estabelecimento, Categoria, Banco, Tipo (débito/crédito/pix/dinheiro), Obs

Regras:
- Se o usuário não informar o banco, use string vazia.
- Categorias possíveis: Alimentação, Transporte, Saúde, Lazer, Moradia, Vestuário, Educação, Serviços, Outros.
- Se não conseguir extrair um gasto, retorne {"erro": "motivo"}.
- Não inclua markdown, apenas JSON puro."""

_PDF_SYSTEM = """Você é um assistente de finanças pessoais.
Extraia TODAS as transações de uma fatura de cartão de crédito e retorne JSON válido.

Retorne APENAS um array JSON onde cada elemento tem:
  Data (YYYY-MM-DD), Valor (número positivo), Estabelecimento, Categoria, Banco, Tipo (crédito), Obs

Regras:
- Ignore totais, subtotais, pagamentos e encargos.
- Tente inferir a categoria pelo nome do estabelecimento.
- Se não houver transações, retorne [].
- Não inclua markdown, apenas JSON puro."""


def _parse_json(raw: str) -> any:
    """Extract and parse JSON even if the model wraps it in markdown."""
    # strip ```json ... ``` blocks
    cleaned = re.sub(r"```(?:json)?\s*", "", raw).strip().rstrip("`").strip()
    return json.loads(cleaned)


def extract_from_text(message: str) -> dict:
    """Extract a single expense from a user text message."""
    system = _EXPENSE_SYSTEM.format(today=date.today().isoformat())
    raw = _chat(system, message)
    try:
        data = _parse_json(raw)
        if "erro" in data:
            raise ValueError(data["erro"])
        return data
    except (json.JSONDecodeError, KeyError) as exc:
        logger.error("extract_from_text parse error: %s | raw: %s", exc, raw)
        raise ValueError(f"Não consegui entender o gasto: {raw}") from exc


def extract_from_pdf(pdf_text: str) -> list[dict]:
    """Extract all transactions from PDF text of a credit card statement."""
    raw = _chat(_PDF_SYSTEM, pdf_text, max_tokens=4096)
    try:
        data = _parse_json(raw)
        if not isinstance(data, list):
            raise ValueError("Esperava uma lista de transações")
        return data
    except (json.JSONDecodeError, ValueError) as exc:
        logger.error("extract_from_pdf parse error: %s | raw: %s", exc, raw)
        raise ValueError("Não consegui extrair transações do PDF") from exc


# ---------------------------------------------------------------------------
# Analysis helpers
# ---------------------------------------------------------------------------

_SUMMARY_SYSTEM = """Você é um assistente de finanças pessoais objetivo e direto.
Responda sempre em português, de forma concisa, usando Markdown simples.
Mostre totais por categoria, destaque os maiores gastos e dê 1-2 dicas práticas."""


def generate_daily_summary(expenses: list[dict], for_date: str) -> str:
    """Generate a daily expense summary using Claude."""
    if not expenses:
        return f"Nenhum gasto registrado em {for_date}."
    expenses_text = json.dumps(expenses, ensure_ascii=False, indent=2)
    prompt = f"Gere um resumo dos gastos do dia {for_date}:\n\n{expenses_text}"
    return _chat(_SUMMARY_SYSTEM, prompt, max_tokens=1024)


def generate_monthly_summary(expenses: list[dict], year: int, month: int) -> str:
    """Generate a monthly expense summary using Claude."""
    if not expenses:
        return f"Nenhum gasto registrado em {year:04d}-{month:02d}."
    expenses_text = json.dumps(expenses, ensure_ascii=False, indent=2)
    prompt = (
        f"Gere um resumo mensal completo dos gastos de {month:02d}/{year}:\n\n{expenses_text}"
    )
    return _chat(_SUMMARY_SYSTEM, prompt, max_tokens=2048)


def answer_query(question: str, expenses: list[dict], context: str = "") -> str:
    """Answer a free-form financial question given a list of expenses."""
    expenses_text = json.dumps(expenses, ensure_ascii=False, indent=2)
    context_block = f"\nContexto adicional: {context}" if context else ""
    prompt = (
        f"Pergunta: {question}{context_block}\n\n"
        f"Dados de gastos disponíveis:\n{expenses_text}"
    )
    return _chat(_SUMMARY_SYSTEM, prompt, max_tokens=1536)
