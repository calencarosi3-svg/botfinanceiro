import os
from dotenv import load_dotenv

load_dotenv()

# Telegram
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")

def _parse_user_ids() -> set[int]:
    """Parse ALLOWED_USER_IDS env var (comma-separated) into a set of ints."""
    raw = os.getenv("ALLOWED_USER_IDS", os.getenv("ALLOWED_USER_ID", ""))
    ids = set()
    for part in raw.split(","):
        part = part.strip()
        if part.isdigit():
            ids.add(int(part))
    return ids

ALLOWED_USER_IDS: set[int] = _parse_user_ids()
# Kept for scheduler backward compat — first user in the set (or 0 if empty)
ALLOWED_USER_ID: int = next(iter(ALLOWED_USER_IDS), 0)

# Anthropic
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
CLAUDE_MODEL = os.getenv("CLAUDE_MODEL", "claude-sonnet-4-6")

# Google Sheets
GOOGLE_CREDENTIALS_FILE = os.getenv("GOOGLE_CREDENTIALS_FILE", "credentials.json")
SPREADSHEET_ID = os.getenv("SPREADSHEET_ID", "")
SHEET_NAME = os.getenv("SHEET_NAME", "Gastos")

# Paths
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "data")
LOGS_DIR = os.path.join(BASE_DIR, "logs")
DB_PATH = os.path.join(DATA_DIR, "financeiro.db")
LOCK_FILE = os.path.join(DATA_DIR, "bot.pid")

# Scheduler flags
DAILY_FLAG_PREFIX = "daily_sent_"    # daily_sent_YYYY-MM-DD
MONTHLY_FLAG_PREFIX = "monthly_sent_"  # monthly_sent_YYYY-MM

# Daily summary hour (24h)
DAILY_SUMMARY_HOUR = int(os.getenv("DAILY_SUMMARY_HOUR", "22"))

# Expense columns in Sheets (order matters)
SHEET_COLUMNS = ["Data", "Valor", "Estabelecimento", "Categoria", "Banco", "Tipo", "Obs"]

# Ensure dirs exist
os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(LOGS_DIR, exist_ok=True)
