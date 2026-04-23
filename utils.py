# =========================
# FILE: utils.py (FINAL FIXED)
# =========================
import re
import pandas as pd

def normalize_description(desc: str) -> str:
    if pd.isna(desc):
        return ""

    text = str(desc).upper()

    # 🔥 REMOVE DATES (ALL formats)
    text = re.sub(r"\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b", " ", text)
    text = re.sub(r"\b\d{4}-\d{2}-\d{2}\b", " ", text)

    # 🔥 REMOVE MASKED CARD NUMBERS (4****2832)
    text = re.sub(r"\d\*+\d*", " ", text)

    # 🔥 REMOVE LONG NUMBERS
    text = re.sub(r"\b\d{3,}\b", " ", text)

    # 🔥 REMOVE COMMON BANK WORDS
    remove_words = [
        "CARD", "DEBIT", "CREDIT", "TRANSACTION",
        "PAYMENT", "WITHDRAWAL", "PURCHASE"
    ]

    for w in remove_words:
        text = text.replace(w, " ")

    # 🔥 KEEP ONLY LETTERS
    text = re.sub(r"[^A-Z\s]", " ", text)

    # CLEAN SPACES
    text = re.sub(r"\s+", " ", text).strip()

    return text


def simplify_merchant(text: str) -> str:
    if not text:
        return ""
    words = text.split()
    return " ".join(words[:4])




import re

def extract_beneficiary(description: str) -> str:
    if not description:
        return ""

    text = description.upper()

    # 🔥 remove dates
    text = re.sub(r"\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b", " ", text)

    # 🔥 remove card patterns
    text = re.sub(r"CARD\s*\d*\*+\d*", " ", text)

    # 🔥 remove numbers
    text = re.sub(r"\b\d+\b", " ", text)

    # 🔥 keep only words
    text = re.sub(r"[^A-Z\s]", " ", text)

    # clean spaces
    text = re.sub(r"\s+", " ", text).strip()

    # 🔥 take first 2–3 words as supplier
    words = text.split()

    if len(words) >= 3:
        return " ".join(words[:3])
    elif len(words) >= 1:
        return " ".join(words)
    else:
        return ""

  


def infer_transaction_type(desc: str, amount: float) -> str:
    text = str(desc).upper()

    if "TRANSFER" in text:
        return "transfer"

    if "SALARY" in text:
        return "incoming"

    if amount > 0:
        return "incoming"

    if amount < 0:
        return "expense"

    return "card_payment"


def format_currency(val: float) -> str:
    try:
        return f"{val:,.2f}"
    except Exception:
        return str(val)


# 🔥 FIXED FUNCTION (proper indentation)
def previous_closed_month_period():
    today = pd.Timestamp.today()
    first_day_current_month = today.replace(day=1)

    previous_month_end = first_day_current_month - pd.Timedelta(days=1)
    previous_month = previous_month_end.to_period("M")

    return previous_month

def format_currency(value):
    try:
        return f"{float(value):,.2f}"
    except:
        return value
