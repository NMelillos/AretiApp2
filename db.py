import hashlib
import os
import re
import sqlite3
from datetime import datetime
from io import BytesIO
from pathlib import Path

import pandas as pd

from utils import extract_beneficiary, infer_transaction_type, normalize_description, simplify_merchant


DATABASE_URL = os.getenv("DATABASE_URL") or os.getenv("POSTGRES_URL") or ""
USING_POSTGRES = bool(DATABASE_URL)
DEFAULT_SHARED_DIR = Path(os.getenv("ARETI_SHARED_FOLDER", r"C:\Users\Student\Dropbox\ARETI FILES ONE DRIVE"))
RENDER_DISK_DB_PATH = Path("/var/data/transactions.db")
DEFAULT_DB_PATH = (
    DEFAULT_SHARED_DIR / "transactions.db"
    if DEFAULT_SHARED_DIR.exists()
    else RENDER_DISK_DB_PATH
    if RENDER_DISK_DB_PATH.parent.exists()
    else Path(__file__).with_name("transactions.db")
)
DB_PATH = "PostgreSQL database" if USING_POSTGRES else os.getenv("ARETI_DB_PATH") or str(DEFAULT_DB_PATH)
_POSTGRES_POOL = None


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _postgres_query(query):
    sql = query.replace("INTEGER PRIMARY KEY AUTOINCREMENT", "SERIAL PRIMARY KEY")
    sql = sql.replace("INSERT OR IGNORE INTO", "INSERT INTO")

    if "INSERT OR REPLACE INTO rates" in query:
        sql = sql.replace("INSERT OR REPLACE INTO", "INSERT INTO")
        sql = sql.rstrip()
        sql += """
        ON CONFLICT(rate_month, rate_type) DO UPDATE SET
            rate_value = EXCLUDED.rate_value,
            created_at = EXCLUDED.created_at
        """
    elif "INSERT OR IGNORE INTO" in query:
        sql = sql.rstrip()
        sql += " ON CONFLICT DO NOTHING"

    return sql.replace("?", "%s")


class PostgresCursor:
    def __init__(self, cursor):
        self._cursor = cursor

    def execute(self, query, params=None):
        self._cursor.execute(_postgres_query(query), params)
        return self

    def executemany(self, query, params=None):
        self._cursor.executemany(_postgres_query(query), params or [])
        return self

    def fetchone(self):
        return self._cursor.fetchone()

    def fetchall(self):
        return self._cursor.fetchall()

    @property
    def rowcount(self):
        return self._cursor.rowcount

    @property
    def description(self):
        return self._cursor.description

    def close(self):
        self._cursor.close()


class PostgresConnection:
    def __init__(self, connection, pool=None):
        self._connection = connection
        self._pool = pool
        self._closed = False

    def cursor(self):
        return PostgresCursor(self._connection.cursor())

    def commit(self):
        self._connection.commit()

    def rollback(self):
        self._connection.rollback()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()

    def __del__(self):
        try:
            self.close()
        except Exception:
            pass

    def close(self):
        if self._closed:
            return
        self._closed = True
        if self._pool is not None:
            try:
                if not self._connection.closed:
                    self._connection.rollback()
                self._pool.putconn(self._connection, close=bool(self._connection.closed))
            except Exception:
                try:
                    self._pool.putconn(self._connection, close=True)
                except Exception:
                    try:
                        self._connection.close()
                    except Exception:
                        pass
            return
        self._connection.close()


def _reset_postgres_pool():
    global _POSTGRES_POOL
    if _POSTGRES_POOL is not None:
        try:
            _POSTGRES_POOL.closeall()
        except Exception:
            pass
    _POSTGRES_POOL = None


def _get_postgres_pool():
    global _POSTGRES_POOL
    if _POSTGRES_POOL is None:
        import psycopg2.pool

        pool_size = max(4, int(os.getenv("POSTGRES_POOL_SIZE", "12")))
        _POSTGRES_POOL = psycopg2.pool.SimpleConnectionPool(
            1,
            pool_size,
            DATABASE_URL,
            connect_timeout=10,
            sslmode=os.getenv("POSTGRES_SSLMODE", "require"),
        )
    return _POSTGRES_POOL


def get_connection():
    if USING_POSTGRES:
        pool = _get_postgres_pool()
        try:
            connection = pool.getconn()
        except Exception as exc:
            if "connection pool exhausted" not in str(exc).lower():
                raise
            _reset_postgres_pool()
            pool = _get_postgres_pool()
            connection = pool.getconn()
        if connection.closed:
            pool.putconn(connection, close=True)
            connection = pool.getconn()
        return PostgresConnection(connection, pool)
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    return sqlite3.connect(DB_PATH, check_same_thread=False)


def _table_columns(cur, table_name):
    if USING_POSTGRES:
        cur.execute("""
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = 'public' AND table_name = ?
        """, (table_name,))
        return {row[0] for row in cur.fetchall()}
    cur.execute(f"PRAGMA table_info({table_name})")
    return {row[1] for row in cur.fetchall()}


def _ensure_column(cur, table_name, column_name, definition):
    if column_name not in _table_columns(cur, table_name):
        cur.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {definition}")


def init_db():
    conn = get_connection()
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS category_list (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            category TEXT NOT NULL,
            subcategory TEXT DEFAULT '',
            report_group TEXT DEFAULT '',
            created_at TEXT,
            UNIQUE(category, subcategory)
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS account_list (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            account_name TEXT NOT NULL,
            bank TEXT,
            account_number TEXT,
            currency TEXT,
            rate_type TEXT,
            created_at TEXT,
            UNIQUE(account_name, bank, account_number)
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS rates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            rate_month TEXT NOT NULL,
            rate_type TEXT NOT NULL,
            rate_value REAL NOT NULL,
            created_at TEXT,
            UNIQUE(rate_month, rate_type)
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS statement_imports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            statement_hash TEXT UNIQUE NOT NULL,
            statement_name TEXT,
            imported_at TEXT,
            transaction_count INTEGER DEFAULT 0,
            duplicate_attempts INTEGER DEFAULT 0,
            last_duplicate_at TEXT
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS statement_balances (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            statement_hash TEXT UNIQUE NOT NULL,
            statement_name TEXT,
            account_name TEXT,
            bank TEXT,
            account_number TEXT,
            currency TEXT,
            period_start TEXT,
            period_end TEXT,
            opening_balance REAL,
            money_out REAL,
            money_in REAL,
            closing_balance REAL,
            source TEXT DEFAULT '',
            notes TEXT DEFAULT '',
            imported_at TEXT,
            updated_at TEXT
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS transaction_memory (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            original_description TEXT,
            normalized_description TEXT NOT NULL,
            beneficiary TEXT,
            transaction_type TEXT,
            category TEXT NOT NULL,
            subcategory TEXT DEFAULT '',
            first_seen TEXT,
            last_seen TEXT,
            times_seen INTEGER DEFAULT 1,
            UNIQUE(normalized_description, category, subcategory)
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS classified_transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            statement_hash TEXT,
            statement_name TEXT,
            row_hash TEXT,
            txn_date TEXT,
            original_description TEXT,
            normalized_description TEXT,
            amount REAL,
            currency TEXT,
            rate_type TEXT,
            fx_rate REAL,
            amount_usd REAL,
            account_name TEXT,
            bank TEXT,
            account_number TEXT,
            beneficiary TEXT,
            transaction_type TEXT,
            category TEXT,
            subcategory TEXT DEFAULT '',
            suggested_category TEXT,
            suggested_subcategory TEXT DEFAULT '',
            match_type TEXT,
            confidence REAL,
            reviewed INTEGER DEFAULT 0,
            status TEXT DEFAULT 'pending',
            dup_flag INTEGER DEFAULT 0,
            created_at TEXT,
            reviewed_at TEXT
        )
    """)

    for column_name, definition in {
        "duplicate_attempts": "INTEGER DEFAULT 0",
        "last_duplicate_at": "TEXT",
    }.items():
        _ensure_column(cur, "statement_imports", column_name, definition)

    for column_name, definition in {
        "statement_hash": "TEXT",
        "statement_name": "TEXT",
        "row_hash": "TEXT",
        "currency": "TEXT",
        "rate_type": "TEXT",
        "fx_rate": "REAL",
        "amount_usd": "REAL",
        "account_name": "TEXT",
        "bank": "TEXT",
        "account_number": "TEXT",
        "subcategory": "TEXT DEFAULT ''",
        "suggested_category": "TEXT",
        "suggested_subcategory": "TEXT DEFAULT ''",
        "status": "TEXT DEFAULT 'pending'",
        "dup_flag": "INTEGER DEFAULT 0",
        "reviewed_at": "TEXT",
    }.items():
        _ensure_column(cur, "classified_transactions", column_name, definition)

    for column_name, definition in {
        "original_description": "TEXT",
        "subcategory": "TEXT DEFAULT ''",
    }.items():
        _ensure_column(cur, "transaction_memory", column_name, definition)

    _ensure_column(cur, "category_list", "report_group", "TEXT DEFAULT ''")

    cur.execute("""
        CREATE TABLE IF NOT EXISTS report_delivery_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            report_month TEXT NOT NULL,
            delivery_type TEXT NOT NULL,
            recipient TEXT NOT NULL,
            status TEXT NOT NULL,
            sent_at TEXT NOT NULL,
            UNIQUE(report_month, delivery_type, recipient)
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS report_group_settings (
            report_group TEXT PRIMARY KEY,
            visible INTEGER DEFAULT 1,
            updated_at TEXT
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS transaction_change_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            transaction_id INTEGER NOT NULL,
            field_name TEXT NOT NULL,
            old_value TEXT,
            new_value TEXT,
            source TEXT DEFAULT '',
            changed_at TEXT NOT NULL
        )
    """)

    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_classified_status
        ON classified_transactions(status, reviewed)
    """)
    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_classified_statement
        ON classified_transactions(statement_hash)
    """)
    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_classified_row_hash
        ON classified_transactions(row_hash)
    """)
    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_balances_account
        ON statement_balances(account_name, period_end)
    """)
    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_balances_statement
        ON statement_balances(statement_hash)
    """)
    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_memory_normalized
        ON transaction_memory(normalized_description, transaction_type)
    """)
    cur.execute("""
        CREATE INDEX IF NOT EXISTS idx_memory_category
        ON transaction_memory(category, subcategory)
    """)
    conn.commit()
    conn.close()


def _clean(value):
    if pd.isna(value):
        return ""
    return str(value).strip()


def _float_or_none(value):
    if value is None or pd.isna(value):
        return None
    text = str(value).strip()
    if not text:
        return None
    text = text.replace(",", "")
    number = pd.to_numeric(text, errors="coerce")
    if pd.isna(number):
        return None
    return float(number)


def _canonical_date(value):
    parsed = pd.to_datetime(value, errors="coerce")
    if not pd.isna(parsed):
        return parsed.strftime("%Y-%m-%d")
    return _clean(value)


def _canonical_text(value):
    return re.sub(r"\s+", " ", _clean(value)).strip().upper()


def _canonical_account_number(value):
    text = _canonical_text(value)
    compact = re.sub(r"[^A-Z0-9]", "", text)
    return compact or text


_RATE_TYPE_TOKENS = [
    ("EUR", ("EUR", "EURO")),
    ("GBP", ("GBP", "POUND", "STERLING")),
    ("ILS", ("ILS", "SHEKEL", "SHEKELS")),
    ("KZT", ("KZT", "TENGE")),
    ("RUB", ("RUB", "RUBLE", "ROUBLE")),
    ("USD", ("USD", "DOLLAR")),
]


def _rate_type_for_currency(currency):
    currency = _clean(currency).upper()
    if not currency:
        return ""
    return "USD/USD" if currency == "USD" else f"{currency}/USD"


def _normalize_rate_type(rate_type, currency=""):
    text = _clean(rate_type).upper()
    if not text:
        return _rate_type_for_currency(currency)

    compact = re.sub(r"[^A-Z]", "", text)
    found = []
    for code, tokens in _RATE_TYPE_TOKENS:
        if any(token in compact for token in tokens):
            found.append(code)

    non_usd = [code for code in found if code != "USD"]
    if non_usd:
        return f"{non_usd[0]}/USD"
    if "USD" in found:
        return "USD/USD"

    return text.replace(" ", "")


def _transaction_line_key(row, include_account=True):
    date_value = _canonical_date(row.get("Date", row.get("txn_date", "")))
    amount = _float_or_none(row.get("Amount", row.get("amount", None)))
    normalized = _canonical_text(row.get("normalized_description", ""))
    if not normalized:
        normalized = _canonical_text(normalize_description(row.get("Description", row.get("original_description", ""))))
    if not date_value or amount is None or not normalized:
        return None

    key = (
        date_value,
        f"{round(float(amount), 2):.2f}",
        normalized,
        _canonical_text(row.get("currency", "")),
        _canonical_text(row.get("bank", "")),
    )
    if include_account is True:
        account_number = _canonical_account_number(row.get("account_number", ""))
        account_identity = account_number or _canonical_text(row.get("account_name", ""))
        key += (account_identity,)
    elif include_account is False:
        account_name = _canonical_text(row.get("account_name", ""))
        if account_name:
            key += (account_name,)
    return key


def _existing_transaction_line_keys(cur, include_account=True):
    return set(_existing_transaction_line_lookup(cur, include_account=include_account))


def _existing_transaction_line_lookup(cur, include_account=True):
    cur.execute("""
        SELECT id, statement_name, txn_date, original_description, normalized_description,
               amount, currency, bank, account_number, account_name
        FROM classified_transactions
        WHERE amount IS NOT NULL
    """)
    columns = [desc[0] for desc in cur.description]
    lookup = {}
    for values in cur.fetchall():
        row = dict(zip(columns, values))
        key = _transaction_line_key(row, include_account=include_account)
        if key and key not in lookup:
            lookup[key] = {
                "duplicate_source_id": row.get("id", ""),
                "duplicate_source_statement": row.get("statement_name", ""),
            }
    return lookup


def mark_duplicate_transactions(df):
    out = df.copy()
    if out.empty:
        out["dup_flag"] = False
        out["duplicate_reason"] = ""
        out["duplicate_source_statement"] = ""
        out["duplicate_source_id"] = ""
        return out

    conn = get_connection()
    cur = conn.cursor()
    try:
        existing_lookup = _existing_transaction_line_lookup(cur)
        relaxed_lookup = _existing_transaction_line_lookup(cur, include_account=False)
        bank_level_lookup = _existing_transaction_line_lookup(cur, include_account=None)
    finally:
        conn.close()

    flags = []
    reasons = []
    source_statements = []
    source_ids = []
    for _, row in out.iterrows():
        key = _transaction_line_key(row)
        if not key:
            flags.append(False)
            reasons.append("")
            source_statements.append("")
            source_ids.append("")
            continue
        existing_match = existing_lookup.get(key)
        if not existing_match:
            existing_match = relaxed_lookup.get(_transaction_line_key(row, include_account=False))
        if not existing_match and _canonical_text(row.get("bank", "")):
            existing_match = bank_level_lookup.get(_transaction_line_key(row, include_account=None))
        if existing_match:
            flags.append(True)
            reasons.append("Already imported / overlapping statement")
            source_statements.append(existing_match.get("duplicate_source_statement", ""))
            source_ids.append(existing_match.get("duplicate_source_id", ""))
            continue
        flags.append(False)
        reasons.append("")
        source_statements.append("")
        source_ids.append("")

    out["dup_flag"] = flags
    out["duplicate_reason"] = reasons
    out["duplicate_source_statement"] = source_statements
    out["duplicate_source_id"] = source_ids
    return out


def _bool_from_value(value):
    if value is None or pd.isna(value):
        return False
    if isinstance(value, bool):
        return value
    text = str(value).strip().casefold()
    return text in {"1", "true", "yes", "y", "reviewed", "checked"}


def _norm_col(name):
    return str(name).strip().lower().replace("_", " ")


def _read_excel(uploaded_file):
    uploaded_file.seek(0)
    return pd.read_excel(uploaded_file)


def replace_categories_from_excel(uploaded_file):
    df = _read_excel(uploaded_file)
    columns = {_norm_col(c): c for c in df.columns}
    category_col = columns.get("category") or df.columns[0]
    subcategory_col = columns.get("subcategory") or columns.get("sub category")
    report_group_col = (
        columns.get("categorisation for reporting")
        or columns.get("categorization for reporting")
        or columns.get("reporting category")
        or columns.get("report group")
    )

    rows = []
    for _, row in df.iterrows():
        category = _clean(row.get(category_col))
        if not category:
            continue
        subcategory = _clean(row.get(subcategory_col)) if subcategory_col else ""
        report_group = _clean(row.get(report_group_col)) if report_group_col else ""
        rows.append((category, subcategory, report_group, _now()))

    conn = get_connection()
    cur = conn.cursor()
    cur.execute("DELETE FROM category_list")
    cur.executemany(
        """
        INSERT OR IGNORE INTO category_list
        (category, subcategory, report_group, created_at)
        VALUES (?, ?, ?, ?)
        """,
        rows,
    )
    conn.commit()
    conn.close()
    return len(rows)


def replace_accounts_from_excel(uploaded_file):
    df = _read_excel(uploaded_file)
    columns = {_norm_col(c): c for c in df.columns}

    account_col = columns.get("account name") or columns.get("name")
    bank_col = columns.get("bank")
    number_col = columns.get("account number") or columns.get("iban")
    currency_col = columns.get("currency")
    rate_col = columns.get("rate type")

    if not account_col:
        raise ValueError("The accounts file must contain an 'Account Name' column.")

    rows = []
    for _, row in df.iterrows():
        account_name = _clean(row.get(account_col))
        if not account_name:
            continue
        currency = _clean(row.get(currency_col)).upper() if currency_col else ""
        rate_type = _normalize_rate_type(row.get(rate_col) if rate_col else "", currency)
        rows.append((
            account_name,
            _clean(row.get(bank_col)) if bank_col else "",
            _clean(row.get(number_col)) if number_col else "",
            currency,
            rate_type,
            _now(),
        ))

    conn = get_connection()
    cur = conn.cursor()
    cur.execute("DELETE FROM account_list")
    cur.executemany("""
        INSERT OR IGNORE INTO account_list
        (account_name, bank, account_number, currency, rate_type, created_at)
        VALUES (?, ?, ?, ?, ?, ?)
    """, rows)
    conn.commit()
    conn.close()
    return len(rows)


def _rate_type_from_label(label):
    return _normalize_rate_type(label)


def replace_rates_from_excel(uploaded_file):
    uploaded_file.seek(0)
    raw = pd.read_excel(uploaded_file, header=None)
    rows = []

    for col in range(1, raw.shape[1]):
        month_value = None
        date_row = None
        for row in range(raw.shape[0]):
            candidate = pd.to_datetime(raw.iat[row, col], errors="coerce")
            if not pd.isna(candidate):
                month_value = raw.iat[row, col]
                date_row = row
                break
        month = pd.to_datetime(month_value, errors="coerce")
        if pd.isna(month):
            continue
        month_key = month.to_period("M").to_timestamp().strftime("%Y-%m-%d")
        for row in range((date_row or 0) + 1, raw.shape[0]):
            label = raw.iat[row, 0]
            value = pd.to_numeric(raw.iat[row, col], errors="coerce")
            if pd.isna(label) or pd.isna(value) or float(value) == 0:
                continue
            rows.append((month_key, _rate_type_from_label(label), float(value), _now()))

    conn = get_connection()
    cur = conn.cursor()
    cur.execute("DELETE FROM rates")
    cur.executemany("""
        INSERT OR REPLACE INTO rates (rate_month, rate_type, rate_value, created_at)
        VALUES (?, ?, ?, ?)
    """, rows)
    conn.commit()
    conn.close()
    return len(rows)


def get_categories(include_subcategories=False):
    conn = get_connection()
    try:
        if include_subcategories:
            return pd.read_sql_query("""
                SELECT category,
                       COALESCE(subcategory, '') AS subcategory,
                       COALESCE(report_group, '') AS report_group
                FROM category_list
                ORDER BY category, subcategory
            """, conn)
        df = pd.read_sql_query(
            "SELECT DISTINCT category FROM category_list ORDER BY category ASC",
            conn,
        )
    finally:
        conn.close()
    return df["category"].dropna().astype(str).tolist()


def get_subcategories(category=None):
    conn = get_connection()
    try:
        if category:
            df = pd.read_sql_query("""
                SELECT DISTINCT subcategory
                FROM category_list
                WHERE category = ? AND COALESCE(subcategory, '') <> ''
                ORDER BY subcategory
            """, conn, params=(category,))
        else:
            df = pd.read_sql_query("""
                SELECT DISTINCT subcategory
                FROM category_list
                WHERE COALESCE(subcategory, '') <> ''
                ORDER BY subcategory
            """, conn)
    finally:
        conn.close()
    return df["subcategory"].dropna().astype(str).tolist()


def _subcategory_parent_lookup(cur):
    cur.execute("""
        SELECT category, subcategory
        FROM category_list
        WHERE COALESCE(category, '') <> ''
          AND COALESCE(subcategory, '') <> ''
    """)
    parents = {}
    ambiguous = set()
    for category, subcategory in cur.fetchall():
        key = _clean(subcategory).casefold()
        if not key:
            continue
        category_value = _clean(category)
        existing = parents.get(key)
        if existing and existing != category_value:
            ambiguous.add(key)
            continue
        parents[key] = category_value
    for key in ambiguous:
        parents.pop(key, None)
    return parents


def get_report_group_settings():
    conn = get_connection()
    try:
        return pd.read_sql_query("""
            SELECT report_group, visible, updated_at
            FROM report_group_settings
            ORDER BY report_group
        """, conn)
    finally:
        conn.close()


def replace_report_group_settings(settings):
    conn = get_connection()
    cur = conn.cursor()
    now = _now()
    cur.execute("DELETE FROM report_group_settings")
    rows = [
        (_clean(row.get("report_group")), int(bool(row.get("visible"))), now)
        for _, row in settings.iterrows()
        if _clean(row.get("report_group"))
    ]
    cur.executemany("""
        INSERT INTO report_group_settings (report_group, visible, updated_at)
        VALUES (?, ?, ?)
    """, rows)
    conn.commit()
    conn.close()
    return len(rows)


def get_transaction_change_log(limit=300):
    conn = get_connection()
    try:
        return pd.read_sql_query("""
            SELECT l.id,
                   l.changed_at,
                   l.transaction_id,
                   t.txn_date,
                   t.account_name,
                   t.bank,
                   t.amount,
                   t.currency,
                   t.original_description,
                   l.field_name,
                   l.old_value,
                   l.new_value,
                   l.source
            FROM transaction_change_log l
            LEFT JOIN classified_transactions t ON t.id = l.transaction_id
            ORDER BY l.id DESC
            LIMIT ?
        """, conn, params=(int(limit),))
    finally:
        conn.close()


def _audit_transaction_change(cur, transaction_id, field_name, old_value, new_value, source):
    old_text = "" if old_value is None else str(old_value)
    new_text = "" if new_value is None else str(new_value)
    if old_text == new_text:
        return
    cur.execute("""
        INSERT INTO transaction_change_log
        (transaction_id, field_name, old_value, new_value, source, changed_at)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (int(transaction_id), field_name, old_text, new_text, source, _now()))


def add_category(category, subcategory=""):
    category = _clean(category)
    subcategory = _clean(subcategory)
    if not category:
        return
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        INSERT OR IGNORE INTO category_list (category, subcategory, created_at)
        VALUES (?, ?, ?)
    """, (category, subcategory, _now()))
    conn.commit()
    conn.close()


def get_accounts():
    conn = get_connection()
    try:
        df = pd.read_sql_query("""
            SELECT id, account_name, bank, account_number, currency, rate_type
            FROM account_list
            ORDER BY account_name, bank, account_number
        """, conn)
    finally:
        conn.close()
    if not df.empty:
        df["rate_type"] = df.apply(
            lambda row: _normalize_rate_type(row.get("rate_type", ""), row.get("currency", "")),
            axis=1,
        )
    return df


def get_rates():
    conn = get_connection()
    try:
        df = pd.read_sql_query("""
            SELECT rate_month, rate_type, rate_value
            FROM rates
            ORDER BY rate_month DESC, rate_type
        """, conn)
    finally:
        conn.close()
    if not df.empty:
        df["rate_type"] = df["rate_type"].apply(_normalize_rate_type)
    return df


def get_latest_rate(rate_type, txn_date=None):
    rate_type = _normalize_rate_type(rate_type)
    if not rate_type:
        return None
    return _lookup_rate(_load_rate_lookup(), rate_type, txn_date)


def _remember_transaction_with_cursor(cur, original_description, normalized_description, beneficiary, transaction_type, category, subcategory=""):
    now = _now()
    cur.execute("""
        SELECT id, times_seen
        FROM transaction_memory
        WHERE normalized_description = ? AND category = ? AND COALESCE(subcategory, '') = ?
    """, (normalized_description, category, subcategory or ""))
    row = cur.fetchone()
    if row:
        memory_id, times_seen = row
        cur.execute("""
            UPDATE transaction_memory
            SET original_description = ?, beneficiary = ?, transaction_type = ?,
                last_seen = ?, times_seen = ?
            WHERE id = ?
        """, (original_description, beneficiary, transaction_type, now, times_seen + 1, memory_id))
    else:
        cur.execute("""
            INSERT INTO transaction_memory
            (original_description, normalized_description, beneficiary, transaction_type,
             category, subcategory, first_seen, last_seen, times_seen)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1)
        """, (
            original_description,
            normalized_description,
            beneficiary,
            transaction_type,
            category,
            subcategory or "",
            now,
            now,
        ))


def remember_transaction(original_description, normalized_description, beneficiary, transaction_type, category, subcategory=""):
    conn = get_connection()
    cur = conn.cursor()
    _remember_transaction_with_cursor(
        cur,
        original_description,
        normalized_description,
        beneficiary,
        transaction_type,
        category,
        subcategory,
    )
    conn.commit()
    conn.close()


def get_memory():
    conn = get_connection()
    try:
        df = pd.read_sql_query("""
            SELECT id, original_description, normalized_description, beneficiary,
                   transaction_type, category, subcategory, first_seen, last_seen, times_seen
            FROM transaction_memory
            ORDER BY times_seen DESC, last_seen DESC
        """, conn)
    finally:
        conn.close()
    return df


def import_memory_from_excel(uploaded_file):
    df = _read_excel(uploaded_file)
    columns = {_norm_col(c): c for c in df.columns}
    normalized_col = columns.get("normalized description") or columns.get("normalized_description")
    original_col = columns.get("original description") or columns.get("original_description")
    beneficiary_col = columns.get("beneficiary")
    transaction_type_col = columns.get("transaction type") or columns.get("transaction_type")
    category_col = columns.get("category")
    subcategory_col = columns.get("subcategory") or columns.get("sub category")
    first_seen_col = columns.get("first seen") or columns.get("first_seen")
    last_seen_col = columns.get("last seen") or columns.get("last_seen")
    times_seen_col = columns.get("times seen") or columns.get("times_seen")

    if not category_col:
        raise ValueError("The memory file must contain a category column.")
    if not normalized_col and not original_col:
        raise ValueError("The memory file must contain original_description or normalized_description.")

    conn = get_connection()
    cur = conn.cursor()
    imported = 0
    now = _now()

    for _, row in df.iterrows():
        original = _clean(row.get(original_col)) if original_col else ""
        normalized = _clean(row.get(normalized_col)) if normalized_col else ""
        if not normalized and original:
            normalized = simplify_merchant(normalize_description(original))
        category = _clean(row.get(category_col))
        if not normalized or not category:
            continue
        subcategory = _clean(row.get(subcategory_col)) if subcategory_col else ""
        beneficiary = _clean(row.get(beneficiary_col)) if beneficiary_col else extract_beneficiary(original)
        transaction_type = _clean(row.get(transaction_type_col)) if transaction_type_col else infer_transaction_type(original, 0)
        first_seen = _clean(row.get(first_seen_col)) if first_seen_col else now
        last_seen = _clean(row.get(last_seen_col)) if last_seen_col else now
        raw_times_seen = pd.to_numeric(row.get(times_seen_col), errors="coerce") if times_seen_col else None
        times_seen = int(raw_times_seen) if raw_times_seen is not None and not pd.isna(raw_times_seen) else 1

        cur.execute("""
            INSERT INTO transaction_memory
            (original_description, normalized_description, beneficiary, transaction_type,
             category, subcategory, first_seen, last_seen, times_seen)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(normalized_description, category, subcategory) DO UPDATE SET
                original_description = CASE
                    WHEN COALESCE(excluded.original_description, '') = ''
                    THEN transaction_memory.original_description
                    ELSE excluded.original_description
                END,
                beneficiary = CASE
                    WHEN COALESCE(excluded.beneficiary, '') = ''
                    THEN transaction_memory.beneficiary
                    ELSE excluded.beneficiary
                END,
                transaction_type = CASE
                    WHEN COALESCE(excluded.transaction_type, '') = ''
                    THEN transaction_memory.transaction_type
                    ELSE excluded.transaction_type
                END,
                first_seen = CASE
                    WHEN COALESCE(transaction_memory.first_seen, '') = ''
                    THEN excluded.first_seen
                    ELSE transaction_memory.first_seen
                END,
                last_seen = CASE
                    WHEN COALESCE(excluded.last_seen, '') = ''
                    THEN transaction_memory.last_seen
                    ELSE excluded.last_seen
                END,
                times_seen = CASE
                    WHEN COALESCE(transaction_memory.times_seen, 0) > COALESCE(excluded.times_seen, 0)
                    THEN transaction_memory.times_seen
                    ELSE excluded.times_seen
                END
        """, (
            original,
            normalized,
            beneficiary,
            transaction_type,
            category,
            subcategory,
            first_seen,
            last_seen,
            times_seen,
        ))
        imported += 1

    conn.commit()
    conn.close()
    return imported


def statement_already_imported(statement_hash):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM statement_imports WHERE statement_hash = ?", (statement_hash,))
    exists = cur.fetchone()[0] > 0
    conn.close()
    return exists


def record_duplicate_statement_attempt(statement_hash):
    conn = get_connection()
    cur = conn.cursor()
    now = _now()
    cur.execute("""
        UPDATE statement_imports
        SET duplicate_attempts = COALESCE(duplicate_attempts, 0) + 1,
            last_duplicate_at = ?
        WHERE statement_hash = ?
    """, (now, statement_hash))
    conn.commit()
    changed = cur.rowcount
    conn.close()
    return changed


def statement_balance_exists(statement_hash):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM statement_balances WHERE statement_hash = ?", (statement_hash,))
    exists = cur.fetchone()[0] > 0
    conn.close()
    return exists


def get_statement_account(statement_hash):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT account_name, bank, account_number, currency, rate_type
        FROM classified_transactions
        WHERE statement_hash = ?
        ORDER BY id
        LIMIT 1
    """, (statement_hash,))
    row = cur.fetchone()
    conn.close()
    if not row:
        return {}
    return {
        "account_name": row[0] or "",
        "bank": row[1] or "",
        "account_number": row[2] or "",
        "currency": row[3] or "",
        "rate_type": row[4] or "",
    }


def save_statement_balance(statement_hash, statement_name, balance, account=None):
    balance = balance or {}
    account = account or {}

    values = {
        "statement_hash": statement_hash,
        "statement_name": statement_name,
        "account_name": _clean(account.get("account_name", "")),
        "bank": _clean(account.get("bank", "") or balance.get("bank", "")),
        "account_number": _clean(account.get("account_number", "") or balance.get("account_number", "")),
        "currency": _clean(balance.get("currency") or account.get("currency", "")),
        "period_start": _clean(balance.get("period_start", "")),
        "period_end": _clean(balance.get("period_end", "")),
        "opening_balance": _float_or_none(balance.get("opening_balance")),
        "money_out": _float_or_none(balance.get("money_out")),
        "money_in": _float_or_none(balance.get("money_in")),
        "closing_balance": _float_or_none(balance.get("closing_balance")),
        "source": _clean(balance.get("source", "")),
        "notes": _clean(balance.get("notes", "")),
    }

    useful_values = [
        values["bank"],
        values["account_number"],
        values["period_start"],
        values["period_end"],
        values["opening_balance"],
        values["money_out"],
        values["money_in"],
        values["closing_balance"],
    ]
    if not any(value not in ("", None) for value in useful_values):
        return 0

    now = _now()
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO statement_balances
        (statement_hash, statement_name, account_name, bank, account_number, currency,
         period_start, period_end, opening_balance, money_out, money_in, closing_balance,
         source, notes, imported_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(statement_hash) DO UPDATE SET
            statement_name = excluded.statement_name,
            account_name = excluded.account_name,
            bank = excluded.bank,
            account_number = excluded.account_number,
            currency = excluded.currency,
            period_start = excluded.period_start,
            period_end = excluded.period_end,
            opening_balance = excluded.opening_balance,
            money_out = excluded.money_out,
            money_in = excluded.money_in,
            closing_balance = excluded.closing_balance,
            source = excluded.source,
            notes = CASE
                WHEN COALESCE(statement_balances.notes, '') = '' THEN excluded.notes
                ELSE statement_balances.notes
            END,
            updated_at = excluded.updated_at
    """, (
        values["statement_hash"],
        values["statement_name"],
        values["account_name"],
        values["bank"],
        values["account_number"],
        values["currency"],
        values["period_start"],
        values["period_end"],
        values["opening_balance"],
        values["money_out"],
        values["money_in"],
        values["closing_balance"],
        values["source"],
        values["notes"],
        now,
        now,
    ))
    conn.commit()
    changed = cur.rowcount
    conn.close()
    return changed


def _apply_balance_reconciliation(df):
    if df.empty:
        return df
    out = df.copy()

    def calculate(row):
        opening = _float_or_none(row.get("opening_balance"))
        money_in = _float_or_none(row.get("money_in"))
        money_out = _float_or_none(row.get("money_out"))
        closing = _float_or_none(row.get("closing_balance"))
        if any(value is None for value in [opening, money_in, money_out, closing]):
            return pd.Series({
                "calculated_closing": None,
                "reconciliation_difference": None,
                "reconciliation_status": "Missing data",
            })

        bank = _clean(row.get("bank", "")).lower()
        account_name = _clean(row.get("account_name", "")).lower()
        credit_style = any(token in f"{bank} {account_name}" for token in [
            "american express",
            "amex",
            "chase",
            "citi",
            "saphire",
            "sapphire",
            "card",
        ])
        if credit_style:
            calculated = opening + money_in + money_out
        else:
            calculated = opening + money_in + (money_out if money_out < 0 else -money_out)
        difference = round(closing - calculated, 2)
        status = "OK" if abs(difference) <= 0.05 else "Needs review"
        return pd.Series({
            "calculated_closing": round(calculated, 2),
            "reconciliation_difference": difference,
            "reconciliation_status": status,
        })

    reconciliation = out.apply(calculate, axis=1)
    for column in reconciliation.columns:
        out[column] = reconciliation[column]
    return out


def get_statement_balances():
    conn = get_connection()
    try:
        df = pd.read_sql_query("""
            SELECT id, statement_name, account_name, bank, account_number, currency,
                   period_start, period_end, opening_balance, money_out, money_in,
                   closing_balance, source, notes, imported_at, updated_at, statement_hash
            FROM statement_balances
            ORDER BY COALESCE(period_end, '') DESC, imported_at DESC, id DESC
        """, conn)
    finally:
        conn.close()
    return _apply_balance_reconciliation(df)


def get_import_history():
    conn = get_connection()
    try:
        df = pd.read_sql_query("""
            WITH first_tx AS (
                SELECT statement_hash,
                       MIN(account_name) AS account_name,
                       MIN(bank) AS bank,
                       MIN(account_number) AS account_number,
                       MIN(currency) AS currency
                FROM classified_transactions
                GROUP BY statement_hash
            )
            SELECT si.id,
                   si.statement_name,
                   si.imported_at,
                   si.transaction_count,
                   COALESCE(ft.account_name, sb.account_name, '') AS account_name,
                   COALESCE(ft.bank, sb.bank, '') AS bank,
                   COALESCE(ft.account_number, sb.account_number, '') AS account_number,
                   COALESCE(ft.currency, sb.currency, '') AS currency,
                   sb.period_start,
                   sb.period_end,
                   sb.opening_balance,
                   sb.money_in,
                   sb.money_out,
                   sb.closing_balance,
                   COALESCE(si.duplicate_attempts, 0) AS duplicate_attempts,
                   si.last_duplicate_at,
                   CASE
                       WHEN COALESCE(si.duplicate_attempts, 0) > 0
                           THEN 'Duplicate blocked (' || CAST(COALESCE(si.duplicate_attempts, 0) AS TEXT) || ')'
                       ELSE 'Imported'
                   END AS duplicate_status,
                   si.statement_hash
            FROM statement_imports si
            LEFT JOIN first_tx ft ON ft.statement_hash = si.statement_hash
            LEFT JOIN statement_balances sb ON sb.statement_hash = si.statement_hash
            ORDER BY si.imported_at DESC, si.id DESC
        """, conn)
    finally:
        conn.close()
    df = _apply_balance_reconciliation(df)
    if not df.empty:
        df["balance_status"] = df["reconciliation_status"].fillna("Missing data")
    return df


def update_statement_balance_rows(df):
    if df.empty:
        return 0
    conn = get_connection()
    cur = conn.cursor()
    now = _now()
    updated = 0
    for _, row in df.iterrows():
        if pd.isna(row.get("id")):
            continue
        cur.execute("""
            UPDATE statement_balances
            SET account_name = ?, bank = ?, account_number = ?, currency = ?,
                period_start = ?, period_end = ?, opening_balance = ?,
                money_out = ?, money_in = ?, closing_balance = ?, notes = ?,
                updated_at = ?
            WHERE id = ?
        """, (
            _clean(row.get("account_name", "")),
            _clean(row.get("bank", "")),
            _clean(row.get("account_number", "")),
            _clean(row.get("currency", "")),
            _clean(row.get("period_start", "")),
            _clean(row.get("period_end", "")),
            _float_or_none(row.get("opening_balance")),
            _float_or_none(row.get("money_out")),
            _float_or_none(row.get("money_in")),
            _float_or_none(row.get("closing_balance")),
            _clean(row.get("notes", "")),
            now,
            int(row["id"]),
        ))
        updated += cur.rowcount
    conn.commit()
    conn.close()
    return updated


def _digits(value):
    text = str(value or "").strip()
    if re.fullmatch(r"-?\d+\.0+", text):
        text = text.split(".", 1)[0]
    return re.sub(r"\D", "", text)


def _amex_member_label(value):
    return re.sub(r"\s+", " ", _clean(value)).strip()


def _dynamic_amex_account(row, default_account, accounts):
    card_member = _amex_member_label(row.get("card_member", ""))
    suffix_digits = _digits(row.get("source_account_number", ""))
    if not card_member and not suffix_digits:
        return _append_amex_card_member(default_account, "", remove_instruction=True)

    candidates = accounts.copy()
    if not candidates.empty and "bank" in candidates.columns:
        candidates = candidates[candidates["bank"].fillna("").astype(str).str.contains("AMEX", case=False, na=False)]

    if not candidates.empty and suffix_digits:
        for _, account_row in candidates.iterrows():
            account_digits = _digits(account_row.get("account_number", ""))
            if account_digits and account_digits.endswith(suffix_digits):
                return _append_amex_card_member(account_row.to_dict(), card_member)

    dynamic_rows = pd.DataFrame()
    if not candidates.empty:
        dynamic_rows = candidates[
            candidates["account_number"].fillna("").astype(str).str.contains("append", case=False, na=False)
        ]
    source_account = (
        dynamic_rows.iloc[0].to_dict()
        if not dynamic_rows.empty
        else default_account
    )
    if not card_member:
        return _append_amex_card_member(source_account, "", remove_instruction=True)

    return _append_amex_card_member(source_account, card_member, remove_instruction=True)


def _append_amex_card_member(account, card_member, remove_instruction=False):
    out = dict(account)
    account_number = _clean(out.get("account_number", ""))
    if remove_instruction:
        account_number = re.sub(r"\s*\[.*?\]\s*", "", account_number).strip()
    if not card_member:
        out["account_number"] = account_number
        return out
    if card_member.casefold() not in account_number.casefold():
        account_number = f"{account_number} {card_member}".strip()
    out["account_number"] = account_number
    return out


def _load_rate_lookup():
    conn = get_connection()
    try:
        rates = pd.read_sql_query("""
            SELECT rate_month, rate_type, rate_value
            FROM rates
            ORDER BY rate_type, rate_month DESC
        """, conn)
    finally:
        conn.close()
    if rates.empty:
        return {}
    rates["rate_type"] = rates["rate_type"].apply(_normalize_rate_type)
    rates["rate_month"] = pd.to_datetime(rates["rate_month"], errors="coerce")
    return {
        rate_type: frame.sort_values("rate_month", ascending=False).reset_index(drop=True)
        for rate_type, frame in rates.groupby("rate_type")
        if rate_type
    }


def _lookup_rate(rate_lookup, rate_type, txn_date=None):
    rate_type = _normalize_rate_type(rate_type)
    if not rate_type:
        return None
    if rate_type == "USD/USD":
        return 1.0
    frame = rate_lookup.get(rate_type)
    if frame is None or frame.empty:
        return None
    month = pd.to_datetime(txn_date, errors="coerce")
    if pd.isna(month):
        return float(frame["rate_value"].iloc[0])
    month = month.to_period("M").to_timestamp()
    candidates = frame[frame["rate_month"] <= month]
    if candidates.empty:
        return float(frame["rate_value"].iloc[-1])
    return float(candidates["rate_value"].iloc[0])


def _usd_from_amount(amount, rate):
    if rate is None or rate == 0:
        return None
    parsed_amount = _float_or_none(amount)
    parsed_rate = _float_or_none(rate)
    if parsed_amount is None or parsed_rate is None or parsed_rate == 0:
        return None
    return round(parsed_amount * parsed_rate, 2)


def _rate_type_from_account(account):
    rate_type = _normalize_rate_type(account.get("rate_type", ""), account.get("currency", ""))
    if rate_type:
        return rate_type
    return ""


def apply_account_and_rates(df, account):
    out = df.copy()
    account = account or {}

    rate_lookup = _load_rate_lookup()
    accounts = get_accounts()
    account_names = []
    banks = []
    account_numbers = []
    currencies = []
    rate_types = []
    fx_rates = []
    usd_values = []

    for _, row in out.iterrows():
        row_account = _dynamic_amex_account(row, account, accounts)
        row_currency = _clean(
            row.get("statement_currency", "")
            or row.get("currency", "")
            or row_account.get("currency", "")
        ).upper()
        rate_type = _rate_type_for_currency(row_currency) if row_currency else _rate_type_from_account(row_account)
        rate = _lookup_rate(rate_lookup, rate_type, row.get("Date"))
        amount = float(row.get("Amount", 0) or 0)

        account_names.append(row_account.get("account_name", ""))
        banks.append(row_account.get("bank", ""))
        account_numbers.append(row_account.get("account_number", ""))
        currencies.append(row_currency)
        rate_types.append(rate_type)

        if rate is None or rate == 0:
            fx_rates.append(None)
            usd_values.append(None)
        else:
            fx_rates.append(rate)
            usd_values.append(_usd_from_amount(amount, rate))

    out["account_name"] = account_names
    out["bank"] = banks
    out["account_number"] = account_numbers
    out["currency"] = currencies
    out["rate_type"] = rate_types
    out["fx_rate"] = fx_rates
    out["amount_usd"] = usd_values
    return out


def build_statement_hash(file_bytes):
    return hashlib.sha256(file_bytes).hexdigest()


def save_pending_transactions(df, statement_name, statement_hash):
    if statement_already_imported(statement_hash):
        record_duplicate_statement_attempt(statement_hash)
        return 0, True, 0

    conn = get_connection()
    cur = conn.cursor()
    now = _now()
    inserted = 0
    duplicate_lines = 0
    existing_keys = _existing_transaction_line_keys(cur)
    relaxed_existing_keys = _existing_transaction_line_keys(cur, include_account=False)
    bank_level_existing_keys = _existing_transaction_line_keys(cur, include_account=None)

    for idx, row in df.reset_index(drop=True).iterrows():
        transaction_key = _transaction_line_key(row)
        relaxed_key = _transaction_line_key(row, include_account=False)
        bank_level_key = _transaction_line_key(row, include_account=None) if _canonical_text(row.get("bank", "")) else None
        if (
            bool(row.get("dup_flag", False))
            or (transaction_key and transaction_key in existing_keys)
            or (relaxed_key and relaxed_key in relaxed_existing_keys)
            or (bank_level_key and bank_level_key in bank_level_existing_keys)
        ):
            duplicate_lines += 1
            continue

        row_hash_src = "|".join([
            statement_hash,
            str(idx),
            str(row.get("Date", "")),
            str(row.get("Amount", "")),
            str(row.get("Description", "")),
        ])
        row_hash = hashlib.sha256(row_hash_src.encode("utf-8")).hexdigest()
        cur.execute("SELECT COUNT(*) FROM classified_transactions WHERE row_hash = ?", (row_hash,))
        if cur.fetchone()[0] > 0:
            continue

        cur.execute("""
            INSERT INTO classified_transactions
            (statement_hash, statement_name, row_hash, txn_date, original_description,
             normalized_description, amount, currency, rate_type, fx_rate, amount_usd,
             account_name, bank, account_number, beneficiary, transaction_type,
             category, subcategory, suggested_category, suggested_subcategory,
             match_type, confidence, reviewed, status, dup_flag, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 'pending', ?, ?)
        """, (
            statement_hash,
            statement_name,
            row_hash,
            str(row.get("Date", "")),
            str(row.get("Description", "")),
            str(row.get("normalized_description", "")),
            float(row.get("Amount", 0) or 0),
            str(row.get("currency", "")),
            str(row.get("rate_type", "")),
            None if pd.isna(row.get("fx_rate", None)) else row.get("fx_rate", None),
            None if pd.isna(row.get("amount_usd", None)) else row.get("amount_usd", None),
            str(row.get("account_name", "")),
            str(row.get("bank", "")),
            str(row.get("account_number", "")),
            str(row.get("beneficiary", "")),
            str(row.get("transaction_type", "")),
            str(row.get("suggested_category", "")),
            str(row.get("suggested_subcategory", "")),
            str(row.get("suggested_category", "")),
            str(row.get("suggested_subcategory", "")),
            str(row.get("match_type", "")),
            float(row.get("confidence", 0) or 0),
            int(bool(row.get("dup_flag", False))),
            now,
        ))
        inserted += 1

    cur.execute("""
        INSERT OR IGNORE INTO statement_imports
        (statement_hash, statement_name, imported_at, transaction_count)
        VALUES (?, ?, ?, ?)
    """, (statement_hash, statement_name, now, inserted))
    conn.commit()
    conn.close()
    return inserted, False, duplicate_lines


def get_pending_transactions():
    conn = get_connection()
    try:
        df = pd.read_sql_query("""
            SELECT *
            FROM classified_transactions
            WHERE COALESCE(status, 'pending') = 'pending' AND COALESCE(reviewed, 0) = 0
            ORDER BY txn_date, id
        """, conn)
    finally:
        conn.close()
    return df


def get_saved_transactions():
    conn = get_connection()
    try:
        df = pd.read_sql_query("""
            SELECT *
            FROM classified_transactions
            WHERE COALESCE(status, '') = 'reviewed' OR COALESCE(reviewed, 0) = 1
            ORDER BY txn_date DESC, id DESC
        """, conn)
    finally:
        conn.close()
    return df


def get_all_transactions():
    conn = get_connection()
    try:
        df = pd.read_sql_query("""
            SELECT *
            FROM classified_transactions
            ORDER BY id DESC
        """, conn)
    finally:
        conn.close()
    return df


def get_dashboard_counts():
    conn = get_connection()
    try:
        cur = conn.cursor()
        counts = {}
        queries = {
            "categories": "SELECT COUNT(*) FROM category_list",
            "accounts": "SELECT COUNT(*) FROM account_list",
            "rates": "SELECT COUNT(*) FROM rates",
            "pending": """
                SELECT COUNT(*)
                FROM classified_transactions
                WHERE COALESCE(status, 'pending') = 'pending' AND COALESCE(reviewed, 0) = 0
            """,
            "reviewed": """
                SELECT COUNT(*)
                FROM classified_transactions
                WHERE COALESCE(status, '') = 'reviewed' OR COALESCE(reviewed, 0) = 1
            """,
            "memory": "SELECT COUNT(*) FROM transaction_memory",
            "statements": "SELECT COUNT(*) FROM statement_imports",
        }
        for key, query in queries.items():
            cur.execute(query)
            counts[key] = int(cur.fetchone()[0])
    finally:
        conn.close()
    return counts


def save_reviewed_rows(df):
    if df.empty:
        return 0
    conn = get_connection()
    cur = conn.cursor()
    now = _now()
    saved = 0
    subcategory_parent_lookup = _subcategory_parent_lookup(cur)

    for _, row in df.iterrows():
        reviewed = bool(row.get("reviewed", False))
        if not reviewed:
            continue
        tx_id = int(row["id"])
        category = _clean(row.get("category"))
        subcategory = _clean(row.get("subcategory"))
        parent_category = subcategory_parent_lookup.get(subcategory.casefold()) if subcategory else None
        if parent_category:
            category = parent_category
        cur.execute("""
            SELECT category, subcategory, reviewed, status
            FROM classified_transactions
            WHERE id = ?
        """, (tx_id,))
        before = cur.fetchone()
        before_category = before[0] if before else ""
        before_subcategory = before[1] if before else ""
        before_reviewed = int(before[2] or 0) if before else 0
        before_status = before[3] if before else ""
        cur.execute("""
            UPDATE classified_transactions
            SET category = ?, subcategory = ?, reviewed = 1, status = 'reviewed', reviewed_at = ?
            WHERE id = ?
        """, (category, subcategory, now, tx_id))
        saved += 1
        _audit_transaction_change(cur, tx_id, "category", before_category, category, "pending_review_save")
        _audit_transaction_change(cur, tx_id, "subcategory", before_subcategory, subcategory, "pending_review_save")
        _audit_transaction_change(cur, tx_id, "reviewed", before_reviewed, 1, "pending_review_save")
        _audit_transaction_change(cur, tx_id, "status", before_status, "reviewed", "pending_review_save")

        cur.execute("""
            SELECT original_description, normalized_description, beneficiary, transaction_type
            FROM classified_transactions
            WHERE id = ?
        """, (tx_id,))
        stored = cur.fetchone()
        if not stored:
            continue

        _remember_transaction_with_cursor(
            cur,
            stored[0] or "",
            stored[1] or "",
            stored[2] or "",
            stored[3] or "",
            category,
            subcategory,
        )

    conn.commit()
    conn.close()
    return saved


def update_database_rows(df):
    if df.empty:
        return 0
    conn = get_connection()
    cur = conn.cursor()
    updated = 0
    rate_lookup = _load_rate_lookup()
    subcategory_parent_lookup = _subcategory_parent_lookup(cur)
    text_columns = [
        "txn_date",
        "original_description",
        "currency",
        "rate_type",
        "account_name",
        "bank",
        "account_number",
        "category",
        "subcategory",
        "status",
        "match_type",
    ]
    number_columns = ["amount", "fx_rate", "amount_usd", "confidence"]
    bool_columns = ["reviewed", "dup_flag"]
    account_update_columns = {"txn_date", "amount", "currency", "rate_type", "account_name", "bank", "account_number"}
    affected_statement_hashes = set()

    for _, row in df.iterrows():
        if "id" not in row or pd.isna(row.get("id")):
            continue
        row_id = int(row["id"])
        cur.execute("""
            SELECT statement_hash, txn_date, amount, currency, rate_type,
                   account_name, bank, account_number, category, subcategory,
                   status, reviewed
            FROM classified_transactions
            WHERE id = ?
        """, (row_id,))
        existing = cur.fetchone()
        if not existing:
            continue
        existing_row = {
            "statement_hash": existing[0],
            "txn_date": existing[1],
            "amount": existing[2],
            "currency": existing[3],
            "rate_type": existing[4],
            "account_name": existing[5],
            "bank": existing[6],
            "account_number": existing[7],
            "category": existing[8],
            "subcategory": existing[9],
            "status": existing[10],
            "reviewed": int(existing[11] or 0),
        }
        row_values = row.to_dict()
        if "subcategory" in row_values:
            subcategory_key = _clean(row_values.get("subcategory")).casefold()
            parent_category = subcategory_parent_lookup.get(subcategory_key)
            if parent_category:
                row_values["category"] = parent_category
        effective_text_columns = [column for column in text_columns if column in row_values]
        assignments = []
        params = []
        recalculate_fx = any(column in df.columns for column in account_update_columns)

        reviewed_value = None
        if "reviewed" in row_values:
            reviewed_value = int(_bool_from_value(row_values.get("reviewed")))

        for column in effective_text_columns:
            if column not in row_values:
                continue
            if recalculate_fx and column == "rate_type":
                continue
            value = _clean(row_values.get(column))
            if column in {"currency", "rate_type"}:
                value = value.upper()
            if column == "rate_type":
                value = _normalize_rate_type(value, row_values.get("currency", existing_row.get("currency", "")))
            if column == "status" and not value:
                value = "reviewed" if reviewed_value else "pending"
            elif column == "status" and value.casefold() in {"pending", "reviewed"}:
                value = value.casefold()
            assignments.append(f"{column} = ?")
            params.append(value)

        for column in number_columns:
            if column not in df.columns:
                continue
            if recalculate_fx and column in {"fx_rate", "amount_usd"}:
                continue
            assignments.append(f"{column} = ?")
            params.append(_float_or_none(row_values.get(column)))

        for column in bool_columns:
            if column not in row_values:
                continue
            value = int(_bool_from_value(row_values.get(column)))
            assignments.append(f"{column} = ?")
            params.append(value)

        if "status" not in df.columns and reviewed_value is not None:
            assignments.append("status = ?")
            params.append("reviewed" if reviewed_value else "pending")

        if recalculate_fx:
            merged_date = row_values.get("txn_date") if "txn_date" in df.columns else existing_row.get("txn_date")
            merged_amount = row_values.get("amount") if "amount" in df.columns else existing_row.get("amount")
            merged_currency = (
                _clean(row_values.get("currency")).upper()
                if "currency" in df.columns
                else _clean(existing_row.get("currency")).upper()
            )
            merged_rate_type = _normalize_rate_type(
                row_values.get("rate_type") if "rate_type" in df.columns else existing_row.get("rate_type"),
                merged_currency,
            )
            merged_fx_rate = _lookup_rate(rate_lookup, merged_rate_type, merged_date)
            merged_amount_usd = _usd_from_amount(merged_amount, merged_fx_rate)
            assignments.extend(["rate_type = ?", "fx_rate = ?", "amount_usd = ?"])
            params.extend([merged_rate_type, merged_fx_rate, merged_amount_usd])

        category = _clean(row_values.get("category")) if "category" in row_values else ""
        subcategory = _clean(row_values.get("subcategory")) if "subcategory" in row_values else ""
        status = _clean(row_values.get("status")).casefold() if "status" in row_values else ""
        if reviewed_value or status == "reviewed":
            assignments.append("reviewed_at = ?")
            params.append(_now())

        if not assignments:
            continue

        params.append(row_id)
        cur.execute(
            f"""
            UPDATE classified_transactions
            SET {', '.join(assignments)}
            WHERE id = ?
            """,
            params,
        )
        updated += cur.rowcount
        for column in effective_text_columns:
            if column in row_values and column in existing_row:
                new_value = _clean(row_values.get(column))
                if column in {"currency", "rate_type"}:
                    new_value = new_value.upper()
                if column == "status" and not new_value:
                    new_value = "reviewed" if reviewed_value else "pending"
                elif column == "status" and new_value.casefold() in {"pending", "reviewed"}:
                    new_value = new_value.casefold()
                _audit_transaction_change(
                    cur,
                    row_id,
                    column,
                    existing_row.get(column, ""),
                    new_value,
                    "database_edit",
                )
        for column in bool_columns:
            if column in row_values and column in existing_row:
                _audit_transaction_change(
                    cur,
                    row_id,
                    column,
                    existing_row.get(column, 0),
                    int(_bool_from_value(row_values.get(column))),
                    "database_edit",
                )
        if "status" not in df.columns and reviewed_value is not None:
            _audit_transaction_change(
                cur,
                row_id,
                "status",
                existing_row.get("status", ""),
                "reviewed" if reviewed_value else "pending",
                "database_edit",
            )
        if recalculate_fx and existing_row.get("statement_hash"):
            affected_statement_hashes.add(existing_row["statement_hash"])
        if reviewed_value or status == "reviewed":
            cur.execute("""
                SELECT original_description, normalized_description, beneficiary, transaction_type
                FROM classified_transactions
                WHERE id = ?
            """, (row_id,))
            stored = cur.fetchone()
            if stored and category:
                _remember_transaction_with_cursor(
                    cur,
                    stored[0] or "",
                    stored[1] or "",
                    stored[2] or "",
                    stored[3] or "",
                    category,
                    subcategory,
                )
    for statement_hash in affected_statement_hashes:
        cur.execute("""
            SELECT account_name, bank, account_number, currency
            FROM classified_transactions
            WHERE statement_hash = ?
              AND COALESCE(account_name, '') <> ''
            GROUP BY account_name, bank, account_number, currency
            ORDER BY COUNT(*) DESC, MIN(id)
            LIMIT 1
        """, (statement_hash,))
        account_row = cur.fetchone()
        if account_row:
            cur.execute("""
                UPDATE statement_balances
                SET account_name = ?, bank = ?, account_number = ?, currency = ?, updated_at = ?
                WHERE statement_hash = ?
            """, (
                account_row[0] or "",
                account_row[1] or "",
                account_row[2] or "",
                account_row[3] or "",
                _now(),
                statement_hash,
            ))
    conn.commit()
    conn.close()
    return updated


def backfill_missing_usd_amounts():
    conn = get_connection()
    try:
        rate_lookup = _load_rate_lookup()
        tx = pd.read_sql_query("""
            SELECT id, txn_date, amount, currency, rate_type, fx_rate
            FROM classified_transactions
            WHERE amount IS NOT NULL
              AND (
                  amount_usd IS NULL
                  OR CAST(amount_usd AS TEXT) = ''
                  OR (ABS(COALESCE(amount_usd, 0)) <= 0.005 AND ABS(COALESCE(amount, 0)) > 0.005)
              )
        """, conn)
        if tx.empty:
            return 0

        cur = conn.cursor()
        updated = 0
        for _, row in tx.iterrows():
            currency = _clean(row.get("currency", "")).upper()
            rate_type = _normalize_rate_type(row.get("rate_type", ""), currency)
            fx_rate = _float_or_none(row.get("fx_rate"))
            if fx_rate is None:
                fx_rate = _lookup_rate(rate_lookup, rate_type, row.get("txn_date"))
            amount_usd = _usd_from_amount(row.get("amount"), fx_rate)
            if amount_usd is None:
                continue
            cur.execute("""
                UPDATE classified_transactions
                SET rate_type = ?, fx_rate = ?, amount_usd = ?
                WHERE id = ?
            """, (rate_type, fx_rate, amount_usd, int(row["id"])))
            updated += cur.rowcount
        conn.commit()
        return updated
    finally:
        conn.close()


def import_database_updates_from_excel(uploaded_file):
    df = _read_excel(uploaded_file)
    if df.empty:
        return 0

    column_map = {}
    aliases = {
        "id": "id",
        "transaction id": "id",
        "txn id": "id",
        "date": "txn_date",
        "txn date": "txn_date",
        "transaction date": "txn_date",
        "status": "status",
        "reviewed": "reviewed",
        "reviewed box": "reviewed",
        "account": "account_name",
        "account name": "account_name",
        "company": "account_name",
        "entity": "account_name",
        "name": "account_name",
        "owner": "account_name",
        "who made the expense": "account_name",
        "bank": "bank",
        "bank name": "bank",
        "account number": "account_number",
        "iban": "account_number",
        "currency": "currency",
        "amount": "amount",
        "statement amount": "amount",
        "usd amount": "amount_usd",
        "amount usd": "amount_usd",
        "usd equivalent": "amount_usd",
        "fx rate": "fx_rate",
        "rate": "fx_rate",
        "rate type": "rate_type",
        "full statement description": "original_description",
        "statement description": "original_description",
        "original description": "original_description",
        "category": "category",
        "sub category": "subcategory",
        "subcategory": "subcategory",
        "match type": "match_type",
        "confidence": "confidence",
        "duplicate": "dup_flag",
        "dup flag": "dup_flag",
    }

    for column in df.columns:
        normalized = _norm_col(column)
        if normalized in aliases:
            column_map[column] = aliases[normalized]

    df = df.rename(columns=column_map)
    if "id" not in df.columns:
        raise ValueError("The uploaded Excel must include the ID column from the database export.")

    allowed = [
        "id",
        "txn_date",
        "status",
        "reviewed",
        "account_name",
        "bank",
        "account_number",
        "currency",
        "amount",
        "amount_usd",
        "fx_rate",
        "rate_type",
        "original_description",
        "category",
        "subcategory",
        "match_type",
        "confidence",
        "dup_flag",
    ]
    usable = [column for column in allowed if column in df.columns]
    return update_database_rows(df[usable].copy())


def insert_manual_transaction(txn_date, description, amount, category, subcategory, account):
    account = account or {}
    description = _clean(description)
    category = _clean(category)
    subcategory = _clean(subcategory)
    if not description:
        raise ValueError("Description is required.")
    if not category:
        raise ValueError("Category is required.")

    parsed_date = pd.to_datetime(txn_date, errors="coerce")
    date_text = parsed_date.strftime("%Y-%m-%d") if not pd.isna(parsed_date) else _clean(txn_date)
    amount = float(amount or 0)
    normalized = simplify_merchant(normalize_description(description))
    beneficiary = extract_beneficiary(description)
    transaction_type = infer_transaction_type(description, amount)
    rate_type = _normalize_rate_type(account.get("rate_type", ""), account.get("currency", ""))
    fx_rate = get_latest_rate(rate_type, date_text)
    amount_usd = _usd_from_amount(amount, fx_rate)
    now = _now()

    row_hash_src = "|".join([
        "manual",
        date_text,
        str(amount),
        description,
        _clean(account.get("account_name", "")),
    ])
    row_hash = hashlib.sha256(row_hash_src.encode("utf-8")).hexdigest()

    conn = get_connection()
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM classified_transactions WHERE row_hash = ?", (row_hash,))
    if cur.fetchone()[0] > 0:
        conn.close()
        return 0

    cur.execute("""
        INSERT OR IGNORE INTO classified_transactions
        (statement_hash, statement_name, row_hash, txn_date, original_description,
         normalized_description, amount, currency, rate_type, fx_rate, amount_usd,
         account_name, bank, account_number, beneficiary, transaction_type,
         category, subcategory, suggested_category, suggested_subcategory,
         match_type, confidence, reviewed, status, dup_flag, created_at, reviewed_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, 'reviewed', 0, ?, ?)
    """, (
        "manual",
        "Manual entry",
        row_hash,
        date_text,
        description,
        normalized,
        amount,
        _clean(account.get("currency", "")),
        rate_type,
        fx_rate,
        amount_usd,
        _clean(account.get("account_name", "")),
        _clean(account.get("bank", "")),
        _clean(account.get("account_number", "")),
        beneficiary,
        transaction_type,
        category,
        subcategory,
        category,
        subcategory,
        "manual",
        1.0,
        now,
        now,
    ))
    inserted = cur.rowcount
    conn.commit()
    conn.close()
    if inserted:
        remember_transaction(description, normalized, beneficiary, transaction_type, category, subcategory)
    return inserted


def dataframe_to_excel_bytes(sheets):
    output = BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        for name, frame in sheets.items():
            frame.to_excel(writer, index=False, sheet_name=name[:31])
    return output.getvalue()


def report_already_sent(report_month, delivery_type, recipient):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        SELECT COUNT(*)
        FROM report_delivery_log
        WHERE report_month = ? AND delivery_type = ? AND recipient = ?
    """, (report_month, delivery_type, recipient))
    exists = cur.fetchone()[0] > 0
    conn.close()
    return exists


def log_report_delivery(report_month, delivery_type, recipient, status):
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("""
        INSERT OR IGNORE INTO report_delivery_log
        (report_month, delivery_type, recipient, status, sent_at)
        VALUES (?, ?, ?, ?, ?)
    """, (report_month, delivery_type, recipient, status, _now()))
    conn.commit()
    conn.close()


def reset_runtime_data():
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("DELETE FROM classified_transactions")
    cur.execute("DELETE FROM transaction_memory")
    cur.execute("DELETE FROM statement_imports")
    cur.execute("DELETE FROM statement_balances")
    cur.execute("DELETE FROM report_delivery_log")
    conn.commit()
    conn.close()


def full_reset_database():
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("DELETE FROM classified_transactions")
    cur.execute("DELETE FROM transaction_memory")
    cur.execute("DELETE FROM statement_imports")
    cur.execute("DELETE FROM statement_balances")
    cur.execute("DELETE FROM report_delivery_log")
    cur.execute("DELETE FROM category_list")
    cur.execute("DELETE FROM account_list")
    cur.execute("DELETE FROM rates")
    conn.commit()
    conn.close()
