import sqlite3
import logging
from datetime import date, datetime
from config import DB_PATH

logger = logging.getLogger(__name__)


def _get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    """Create tables if they don't exist."""
    try:
        with _get_conn() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS expenses (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at  TEXT NOT NULL,
                    expense_date TEXT NOT NULL,
                    valor       REAL NOT NULL,
                    estabelecimento TEXT,
                    categoria   TEXT,
                    banco       TEXT,
                    tipo        TEXT,
                    obs         TEXT
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_expense_date ON expenses(expense_date)
            """)
            conn.commit()
        logger.info("Database initialized at %s", DB_PATH)
    except sqlite3.Error as exc:
        logger.critical("Failed to initialize database: %s", exc)
        raise RuntimeError(f"Erro ao inicializar banco de dados: {exc}") from exc


def record_expense(
    expense_date: str,
    valor: float,
    estabelecimento: str = "",
    categoria: str = "",
    banco: str = "",
    tipo: str = "",
    obs: str = "",
) -> int:
    """Insert one expense record. Returns the new row id."""
    now = datetime.utcnow().isoformat()
    try:
        with _get_conn() as conn:
            cur = conn.execute(
                """
                INSERT INTO expenses
                    (created_at, expense_date, valor, estabelecimento, categoria, banco, tipo, obs)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (now, expense_date, valor, estabelecimento, categoria, banco, tipo, obs),
            )
            conn.commit()
            return cur.lastrowid
    except sqlite3.Error as exc:
        logger.error("Failed to record expense: %s", exc)
        raise RuntimeError("Erro ao salvar gasto no banco de dados local.") from exc


def record_expenses_bulk(rows: list[dict]) -> int:
    """Insert multiple expense dicts at once. Returns count inserted."""
    if not rows:
        return 0
    now = datetime.utcnow().isoformat()
    data = [
        (
            now,
            r.get("Data", date.today().isoformat()),
            float(r.get("Valor", 0)),
            r.get("Estabelecimento", ""),
            r.get("Categoria", ""),
            r.get("Banco", ""),
            r.get("Tipo", ""),
            r.get("Obs", ""),
        )
        for r in rows
    ]
    try:
        with _get_conn() as conn:
            conn.executemany(
                """
                INSERT INTO expenses
                    (created_at, expense_date, valor, estabelecimento, categoria, banco, tipo, obs)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                data,
            )
            conn.commit()
        return len(data)
    except sqlite3.Error as exc:
        logger.error("Failed to bulk insert expenses: %s", exc)
        raise RuntimeError("Erro ao salvar gastos no banco de dados local.") from exc


def get_expense_count(for_date: str | None = None) -> int:
    """Return number of expenses for a given date (YYYY-MM-DD). Defaults to today."""
    target = for_date or date.today().isoformat()
    try:
        with _get_conn() as conn:
            row = conn.execute(
                "SELECT COUNT(*) FROM expenses WHERE expense_date = ?", (target,)
            ).fetchone()
            return row[0] if row else 0
    except sqlite3.Error as exc:
        logger.error("Failed to get expense count: %s", exc)
        return 0


def get_expenses_for_date(for_date: str) -> list[dict]:
    try:
        with _get_conn() as conn:
            rows = conn.execute(
                "SELECT * FROM expenses WHERE expense_date = ? ORDER BY id",
                (for_date,),
            ).fetchall()
            return [dict(r) for r in rows]
    except sqlite3.Error as exc:
        logger.error("Failed to get expenses for date %s: %s", for_date, exc)
        return []


def get_expenses_for_month(year: int, month: int) -> list[dict]:
    prefix = f"{year:04d}-{month:02d}"
    try:
        with _get_conn() as conn:
            rows = conn.execute(
                "SELECT * FROM expenses WHERE expense_date LIKE ? ORDER BY expense_date, id",
                (f"{prefix}%",),
            ).fetchall()
            return [dict(r) for r in rows]
    except sqlite3.Error as exc:
        logger.error("Failed to get expenses for month %s-%s: %s", year, month, exc)
        return []
