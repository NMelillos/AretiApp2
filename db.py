# =========================
# FILE: db.py
# =========================
import sqlite3
from datetime import datetime
from pathlib import Path

import pandas as pd

DB_PATH = str((Path(__file__).resolve().parent / "transactions.db").resolve())

DEFAULT_CATEGORIES = [
    "Subscriptions",
    "Bank Fees",
    "Own Funds",
    "Utilities",
    "Rent",
    "Salaries",
    "Office Supplies",
    "Travel",
    "Insurance",
    "Taxes",
    "Professional Services",
    "Online Shopping",
    "Entertainment",
    "Marketing",
    "Software",
    "Telephone",
    "Other",
]


def get_connection():
    return sqlite3.connect(DB_PATH, check_same_thread=False)


def init_db():
    conn = get_connection()
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS category_list (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            category TEXT UNIQUE NOT NULL
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS transaction_memory (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            normalized_description TEXT NOT NULL,
            beneficiary TEXT,
            transaction_type TEXT,
            category TEXT NOT NULL,
            first_seen TEXT,
            last_seen TEXT,
            times_seen INTEGER DEFAULT 1,
            UNIQUE(normalized_description, category)
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS classified_transactions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            txn_date TEXT,
            original_description TEXT,
            normalized_description TEXT,
            amount REAL,
            beneficiary TEXT,
            transaction_type TEXT,
            category TEXT,
            match_type TEXT,
            confidence REAL,
            reviewed INTEGER DEFAULT 0,
            created_at TEXT
        )
    """)

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
        CREATE TABLE IF NOT EXISTS monthly_rates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            report_month TEXT NOT NULL,
            source_currency TEXT NOT NULL,
            rate_to_usd REAL NOT NULL,
            UNIQUE(report_month, source_currency)
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS account_registry (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            account_name TEXT NOT NULL,
            bank TEXT,
            account_number TEXT NOT NULL,
            currency TEXT NOT NULL,
            UNIQUE(account_name, account_number)
        )
    """)

    existing_columns = {
        row[1] for row in cur.execute("PRAGMA table_info(classified_transactions)").fetchall()
    }
    if "currency" not in existing_columns:
        cur.execute("ALTER TABLE classified_transactions ADD COLUMN currency TEXT")
    if "usd_amount" not in existing_columns:
        cur.execute("ALTER TABLE classified_transactions ADD COLUMN usd_amount REAL")
    if "account_name" not in existing_columns:
        cur.execute("ALTER TABLE classified_transactions ADD COLUMN account_name TEXT")
    if "account_number" not in existing_columns:
        cur.execute("ALTER TABLE classified_transactions ADD COLUMN account_number TEXT")
    if "bank" not in existing_columns:
        cur.execute("ALTER TABLE classified_transactions ADD COLUMN bank TEXT")

    conn.commit()

    cur.execute("SELECT COUNT(*) FROM category_list")
    count = cur.fetchone()[0]
    if count == 0:
        for cat in DEFAULT_CATEGORIES:
            cur.execute(
                "INSERT OR IGNORE INTO category_list (category) VALUES (?)",
                (cat,)
            )
        conn.commit()

    conn.close()


def get_categories():
    conn = get_connection()
    df = pd.read_sql_query(
        "SELECT category FROM category_list ORDER BY category ASC",
        conn
    )
    conn.close()
    return df["category"].tolist()


def get_category_records():
    conn = get_connection()
    df = pd.read_sql_query(
        "SELECT id, category FROM category_list ORDER BY id ASC",
        conn
    )
    conn.close()
    return df


def get_monthly_rates():
    conn = get_connection()
    df = pd.read_sql_query(
        """
        SELECT id, report_month, source_currency, rate_to_usd
        FROM monthly_rates
        ORDER BY report_month ASC, source_currency ASC
        """,
        conn,
    )
    conn.close()
    return df


def get_account_registry():
    conn = get_connection()
    df = pd.read_sql_query(
        """
        SELECT id, account_name, bank, account_number, currency
        FROM account_registry
        ORDER BY account_name ASC, bank ASC, account_number ASC
        """,
        conn,
    )
    conn.close()
    return df


def add_category(category: str):
    category = str(category).strip()
    if not category:
        return
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        "INSERT OR IGNORE INTO category_list (category) VALUES (?)",
        (category,)
    )
    conn.commit()
    conn.close()


def replace_categories(categories):
    cleaned_categories = []
    seen = set()

    for category in categories:
        value = str(category).strip()
        if not value or value in seen:
            continue
        cleaned_categories.append(value)
        seen.add(value)

    if not cleaned_categories:
        return

    conn = get_connection()
    cur = conn.cursor()
    cur.execute("DELETE FROM category_list")
    cur.executemany(
        "INSERT INTO category_list (category) VALUES (?)",
        [(category,) for category in cleaned_categories],
    )
    conn.commit()
    conn.close()


def remember_transaction(normalized_description, beneficiary, transaction_type, category):
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn = get_connection()
    cur = conn.cursor()

    cur.execute("""
        SELECT id, times_seen
        FROM transaction_memory
        WHERE normalized_description = ? AND category = ?
    """, (normalized_description, category))
    row = cur.fetchone()

    if row:
        memory_id, times_seen = row
        cur.execute("""
            UPDATE transaction_memory
            SET last_seen = ?, times_seen = ?
            WHERE id = ?
        """, (now, times_seen + 1, memory_id))
    else:
        cur.execute("""
            INSERT INTO transaction_memory
            (normalized_description, beneficiary, transaction_type, category, first_seen, last_seen, times_seen)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            normalized_description,
            beneficiary,
            transaction_type,
            category,
            now,
            now,
            1,
        ))

    conn.commit()
    conn.close()


def get_memory():
    conn = get_connection()
    df = pd.read_sql_query(
        "SELECT * FROM transaction_memory ORDER BY times_seen DESC, last_seen DESC",
        conn
    )
    conn.close()
    return df


def save_classified_transactions(df):
    conn = get_connection()
    cur = conn.cursor()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    for _, row in df.iterrows():
        cur.execute("""
            INSERT INTO classified_transactions
            (txn_date, original_description, normalized_description, amount,
             beneficiary, transaction_type, category, match_type, confidence,
             reviewed, created_at, currency, usd_amount, account_name,
             account_number, bank)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            str(row.get("Date", "")),
            str(row.get("Description", "")),
            str(row.get("normalized_description", "")),
            float(row.get("Amount", 0)),
            str(row.get("beneficiary", "")),
            str(row.get("transaction_type", "")),
            str(row.get("final_category", "")),
            str(row.get("match_type", "")),
            float(row.get("confidence", 0)),
            int(row.get("reviewed", 0)),
            now,
            str(row.get("currency", "USD")),
            float(row.get("usd_amount", row.get("Amount", 0))),
            str(row.get("account_name", "")),
            str(row.get("account_number", "")),
            str(row.get("bank", "")),
        ))

    conn.commit()
    conn.close()


def get_saved_transactions():
    conn = get_connection()
    df = pd.read_sql_query("""
        SELECT *
        FROM classified_transactions
        ORDER BY txn_date DESC, id DESC
    """, conn)
    conn.close()
    return df


def _normalize_db_value(value):
    if pd.isna(value):
        return None
    if hasattr(value, "item"):
        return value.item()
    return value


def _replace_table_from_dataframe(table_name, df, columns, required_columns=None):
    working_df = df.copy()
    working_df = working_df.reindex(columns=columns)

    cleaned_rows = []
    non_id_columns = [column for column in columns if column != "id"]

    for _, row in working_df.iterrows():
        normalized_row = {}

        for column in columns:
            value = _normalize_db_value(row[column])
            if isinstance(value, str):
                value = value.strip()
            normalized_row[column] = value

        if all(
            normalized_row[column] in (None, "")
            for column in non_id_columns
        ):
            continue

        if required_columns and any(
            normalized_row[column] in (None, "")
            for column in required_columns
        ):
            continue

        cleaned_rows.append(tuple(normalized_row[column] for column in columns))

    conn = get_connection()
    cur = conn.cursor()
    cur.execute(f"DELETE FROM {table_name}")

    if cleaned_rows:
        placeholders = ", ".join(["?"] * len(columns))
        cur.executemany(
            f"INSERT INTO {table_name} ({', '.join(columns)}) VALUES ({placeholders})",
            cleaned_rows,
        )

    conn.commit()
    conn.close()


def replace_category_records(df):
    _replace_table_from_dataframe(
        "category_list",
        df,
        ["id", "category"],
        required_columns=["category"],
    )


def replace_monthly_rate_records(df):
    _replace_table_from_dataframe(
        "monthly_rates",
        df,
        ["id", "report_month", "source_currency", "rate_to_usd"],
        required_columns=["report_month", "source_currency", "rate_to_usd"],
    )


def replace_account_registry_records(df):
    _replace_table_from_dataframe(
        "account_registry",
        df,
        ["id", "account_name", "bank", "account_number", "currency"],
        required_columns=["account_name", "account_number", "currency"],
    )


def replace_memory_records(df):
    _replace_table_from_dataframe(
        "transaction_memory",
        df,
        [
            "id",
            "normalized_description",
            "beneficiary",
            "transaction_type",
            "category",
            "first_seen",
            "last_seen",
            "times_seen",
        ],
        required_columns=["normalized_description", "category"],
    )


def replace_saved_transaction_records(df):
    _replace_table_from_dataframe(
        "classified_transactions",
        df,
        [
            "id",
            "txn_date",
            "original_description",
            "normalized_description",
            "amount",
            "beneficiary",
            "transaction_type",
            "category",
            "match_type",
            "confidence",
            "reviewed",
            "created_at",
            "currency",
            "usd_amount",
            "account_name",
            "account_number",
            "bank",
        ],
    )


def report_already_sent(report_month: str, delivery_type: str, recipient: str) -> bool:
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


def log_report_delivery(report_month: str, delivery_type: str, recipient: str, status: str):
    conn = get_connection()
    cur = conn.cursor()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cur.execute("""
        INSERT OR IGNORE INTO report_delivery_log
        (report_month, delivery_type, recipient, status, sent_at)
        VALUES (?, ?, ?, ?, ?)
    """, (report_month, delivery_type, recipient, status, now))
    conn.commit()
    conn.close()


def reset_runtime_data():
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("DELETE FROM classified_transactions")
    cur.execute("DELETE FROM transaction_memory")
    cur.execute("DELETE FROM report_delivery_log")
    conn.commit()
    conn.close()


def full_reset_database():
    conn = get_connection()
    cur = conn.cursor()
    cur.execute("DELETE FROM classified_transactions")
    cur.execute("DELETE FROM transaction_memory")
    cur.execute("DELETE FROM report_delivery_log")
    cur.execute("DELETE FROM category_list")
    conn.commit()
    conn.close()
    init_db()
