# =========================
# FILE: utils.py
# =========================
import re

import pandas as pd


def format_currency(value) -> str:
    try:
        return f"{float(value):,.2f}"
    except Exception:
        return "0.00"


def previous_closed_month_period():
    today = pd.Timestamp.now().normalize()
    first_of_this_month = today.replace(day=1)
    last_of_prev_month = first_of_this_month - pd.Timedelta(days=1)
    return last_of_prev_month.to_period("M")


def normalize_description(desc: str) -> str:
    if pd.isna(desc):
        return ""

    text = str(desc).upper().strip()
    text = re.sub(r"\b[A-Z]{2}\d{2}[A-Z0-9]{8,}\b", " ", text)
    text = re.sub(r"\b\d{6,}\b", " ", text)
    text = re.sub(r"\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b", " ", text)
    text = re.sub(r"[^A-Z0-9\s&/\-]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def simplify_merchant(normalized_desc: str) -> str:
    if not normalized_desc:
        return ""

    replacements = [
        ("AMAZON EU", "AMAZON"),
        ("AMAZON DIGITAL", "AMAZON"),
        ("AMAZON MARKETPLACE", "AMAZON"),
        ("GOOGLE *", "GOOGLE"),
        ("APPLE.COM", "APPLE"),
        ("APPLE COM", "APPLE"),
        ("NETFLIX.COM", "NETFLIX"),
        ("MICROSOFT*", "MICROSOFT"),
        ("SPOTIFY AB", "SPOTIFY"),
    ]

    text = normalized_desc
    for old, new in replacements:
        text = text.replace(old, new)

    text = re.sub(r"\s+", " ", text).strip()
    return text


def extract_beneficiary(desc: str) -> str:
    if not desc:
        return ""

    text = normalize_description(desc)
    text = simplify_merchant(text)

    stop_words = {
        "PAYMENT", "POS", "CARD", "TRANSFER", "SEPA", "DIRECT", "DEBIT", "CREDIT",
        "PURCHASE", "ONLINE", "BANK", "FEE", "TO", "FROM", "ATM", "WITHDRAWAL",
        "PAY", "TRF", "TXN"
    }

    tokens = [t for t in text.split() if t not in stop_words]
    beneficiary = " ".join(tokens[:4]).strip()
    return beneficiary if beneficiary else text[:40]


def infer_transaction_type(desc: str, amount: float) -> str:
    text = normalize_description(desc)

    if any(x in text for x in ["BANK FEE", "ACCOUNT FEE", "CHARGE", "COMMISSION"]):
        return "bank_fee"
    if any(x in text for x in ["TRANSFER", "SEPA", "OWN FUNDS", "INTERNAL TRANSFER"]):
        return "transfer"
    if any(x in text for x in ["POS", "CARD", "PURCHASE"]):
        return "card_payment"
    if any(x in text for x in ["ATM", "WITHDRAWAL"]):
        return "cash_withdrawal"
    if amount > 0:
        return "incoming"
    return "expense"
