import logging
from datetime import date
from typing import Any

import gspread
from google.oauth2.service_account import Credentials

from config import GOOGLE_CREDENTIALS_FILE, SPREADSHEET_ID, SHEET_NAME, SHEET_COLUMNS

logger = logging.getLogger(__name__)

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive.readonly",
]

_client: gspread.Client | None = None
_worksheet: gspread.Worksheet | None = None


def _get_worksheet() -> gspread.Worksheet:
    global _client, _worksheet
    if _worksheet is None:
        creds = Credentials.from_service_account_file(GOOGLE_CREDENTIALS_FILE, scopes=SCOPES)
        _client = gspread.authorize(creds)
        spreadsheet = _client.open_by_key(SPREADSHEET_ID)
        try:
            _worksheet = spreadsheet.worksheet(SHEET_NAME)
        except gspread.WorksheetNotFound:
            _worksheet = spreadsheet.add_worksheet(SHEET_NAME, rows=10000, cols=len(SHEET_COLUMNS))
            _worksheet.append_row(SHEET_COLUMNS)
            logger.info("Created sheet '%s' with headers", SHEET_NAME)
    return _worksheet


def _reconnect() -> gspread.Worksheet:
    """Force reconnection (e.g. after token expiry)."""
    global _client, _worksheet
    _client = None
    _worksheet = None
    return _get_worksheet()


def ensure_header() -> None:
    """Make sure the first row contains the expected headers."""
    ws = _get_worksheet()
    first_row = ws.row_values(1)
    if first_row != SHEET_COLUMNS:
        ws.insert_row(SHEET_COLUMNS, index=1)
        logger.info("Inserted header row into sheet")


def _build_row(expense: dict) -> list:
    """Build a sheet row, keeping Valor as float for proper numeric formatting."""
    row = []
    for col in SHEET_COLUMNS:
        val = expense.get(col, "")
        if col == "Valor":
            try:
                row.append(float(str(val).replace(",", ".")))
            except (ValueError, TypeError):
                row.append(0.0)
        else:
            row.append(str(val))
    return row


def append_row(expense: dict) -> None:
    """Append a single expense dict as a new row."""
    row = _build_row(expense)
    try:
        ws = _get_worksheet()
        ws.append_row(row, value_input_option="USER_ENTERED")
    except Exception as exc:
        logger.warning("Sheet append failed (%s), reconnecting…", exc)
        ws = _reconnect()
        ws.append_row(row, value_input_option="USER_ENTERED")


def append_rows(expenses: list[dict]) -> None:
    """Append multiple expense dicts as new rows (batch)."""
    if not expenses:
        return
    rows = [_build_row(e) for e in expenses]
    try:
        ws = _get_worksheet()
        ws.append_rows(rows, value_input_option="USER_ENTERED")
    except Exception as exc:
        logger.warning("Sheet batch append failed (%s), reconnecting…", exc)
        ws = _reconnect()
        ws.append_rows(rows, value_input_option="USER_ENTERED")


def get_rows_for_date(for_date: str) -> list[dict]:
    """Return all rows matching the given date (YYYY-MM-DD)."""
    return _filter_rows(lambda r: r.get("Data", "") == for_date)


def get_rows_for_month(year: int, month: int) -> list[dict]:
    """Return all rows whose Data starts with YYYY-MM."""
    prefix = f"{year:04d}-{month:02d}"
    return _filter_rows(lambda r: r.get("Data", "").startswith(prefix))


def get_rows_for_period(start: str, end: str) -> list[dict]:
    """Return all rows where start <= Data <= end (ISO strings)."""
    return _filter_rows(lambda r: start <= r.get("Data", "") <= end)


def _filter_rows(predicate) -> list[dict]:
    try:
        ws = _get_worksheet()
        all_records = ws.get_all_records(expected_headers=SHEET_COLUMNS)
    except Exception as exc:
        logger.warning("Sheet read failed (%s), reconnecting…", exc)
        ws = _reconnect()
        all_records = ws.get_all_records(expected_headers=SHEET_COLUMNS)
    return [r for r in all_records if predicate(r)]
