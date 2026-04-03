import os
from dotenv import load_dotenv

load_dotenv()

# Telegram
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
ALLOWED_USER_ID = int(os.getenv("ALLOWED_USER_ID", "0"))

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
