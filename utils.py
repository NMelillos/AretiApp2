import re

import pandas as pd


def format_currency(value):
    try:
        return f"{float(value):,.2f}"
    except Exception:
        return "0.00"


def previous_closed_month_period():
    today = pd.Timestamp.now().normalize()
    first_of_this_month = today.replace(day=1)
    last_of_prev_month = first_of_this_month - pd.Timedelta(days=1)
    return last_of_prev_month.to_period("M")


def normalize_description(desc):
    if pd.isna(desc):
        return ""
    text = str(desc).upper().strip()
    text = re.sub(r"\b[A-Z]{2}\d{2}[A-Z0-9]{8,}\b", " ", text)
    text = re.sub(r"\b\d{10,}\b", " ", text)
    text = re.sub(r"\b\d{1,2}[/-]\d{1,2}[/-]\d{2,4}\b", " ", text)
    text = re.sub(r"[^A-Z0-9\s&/\-]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def simplify_merchant(normalized_desc):
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
    return re.sub(r"\s+", " ", text).strip()


def extract_beneficiary(desc):
    if not desc:
        return ""
    text = simplify_merchant(normalize_description(desc))
    stop_words = {
        "PAYMENT", "POS", "CARD", "TRANSFER", "SEPA", "DIRECT", "DEBIT", "CREDIT",
        "PURCHASE", "ONLINE", "BANK", "FEE", "TO", "FROM", "ATM", "WITHDRAWAL",
        "PAY", "TRF", "TXN", "AUTH", "TRACE",
    }
    tokens = [token for token in text.split() if token not in stop_words]
    beneficiary = " ".join(tokens[:5]).strip()
    return beneficiary or text[:60]


def infer_transaction_type(desc, amount):
    text = normalize_description(desc)
    if any(token in text for token in ["BANK FEE", "ACCOUNT FEE", "COMMISSION"]):
        return "bank_fee"
    if any(token in text for token in ["OWN FUNDS", "OWN ACCOUNT", "INTERNAL TRANSFER"]):
        return "own_funds"
    if any(token in text for token in ["TRANSFER", "SEPA"]):
        return "transfer"
    if any(token in text for token in ["POS", "CARD", "PURCHASE"]):
        return "card_payment"
    if any(token in text for token in ["ATM", "WITHDRAWAL"]):
        return "cash_withdrawal"
    if amount > 0:
        return "incoming"
    return "expense"
