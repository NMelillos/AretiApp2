# =========================
# FILE: db.py
# =========================
import sqlite3
from datetime import datetime

import pandas as pd

DB_PATH = "transactions.db"

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
             reviewed, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
