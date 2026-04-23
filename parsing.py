# =========================
# FILE: parsing.py (FIXED VERSION)
# =========================
import re
from datetime import datetime

import pandas as pd

from utils import (
    normalize_description,
    simplify_merchant,
    extract_beneficiary,
    infer_transaction_type,
)

try:
    import pdfplumber
except Exception:
    pdfplumber = None

try:
    import pytesseract
except Exception:
    pytesseract = None

try:
    from pdf2image import convert_from_bytes
except Exception:
    convert_from_bytes = None


def detect_columns(df: pd.DataFrame):
    date_col = None
    desc_col = None
    amount_col = None
    debit_col = None
    credit_col = None

    for c in df.columns:
        cl = str(c).lower().strip()

        if cl in ["date", "transaction date", "booking date", "value date"]:
            date_col = c

        if cl in ["description", "details", "narration", "transaction details", "memo"]:
            desc_col = c

        if cl in ["amount", "transaction amount"]:
            amount_col = c

        if cl in ["debit", "withdrawal"]:
            debit_col = c

        if cl in ["credit", "deposit"]:
            credit_col = c

    if desc_col is None:
        for c in df.columns:
            cl = str(c).lower()
            if "desc" in cl or "detail" in cl or "memo" in cl or "narration" in cl:
                desc_col = c
                break

    if date_col is None:
        for c in df.columns:
            if "date" in str(c).lower():
                date_col = c
                break

    return date_col, desc_col, amount_col, debit_col, credit_col


def merge_multiline_transactions(df: pd.DataFrame):
    """
    Merge multi-line descriptions into single transactions.
    Assumes rows without amount belong to previous transaction.
    """
    rows = []
    current = None

    for _, r in df.iterrows():
        amount = r["Amount"]

        if pd.notna(amount) and amount != 0:
            if current is not None:
                rows.append(current)
            current = r.copy()
        else:
            if current is not None:
                current["Description"] += " " + str(r["Description"])

    if current is not None:
        rows.append(current)

    return pd.DataFrame(rows)


def prepare_dataframe_from_tabular(df: pd.DataFrame):
    date_col, desc_col, amount_col, debit_col, credit_col = detect_columns(df)

    # 🔥 FIX DATE COLUMN (using detected column)
    if date_col in df.columns:
        df[date_col] = pd.to_datetime(df[date_col], errors="coerce")
        df = df.dropna(subset=[date_col])
        # 🔥 KEEP FULL ORIGINAL DESCRIPTION (ALL LINES)
    if desc_col in df.columns:
        df["original_full_description"] = df[desc_col].astype(str)

    if amount_col is None and debit_col and credit_col:
        df[debit_col] = pd.to_numeric(df[debit_col], errors="coerce").fillna(0)
        df[credit_col] = pd.to_numeric(df[credit_col], errors="coerce").fillna(0)
        df["Amount"] = df[credit_col] - df[debit_col]
        amount_col = "Amount"

    if not all([date_col, desc_col, amount_col]):
        raise ValueError(
            "Could not detect required columns (Date, Description, Amount)."
        )

    out = df[[date_col, desc_col, amount_col]].copy()
    out.columns = ["Date", "Description", "Amount"]

    # Clean basic fields
    out["Description"] = out["Description"].fillna("").astype(str)
    out["Amount"] = pd.to_numeric(out["Amount"], errors="coerce")

    # 🔥 FIX 1: Better date parsing (European format)
    out["Date"] = pd.to_datetime(
        out["Date"],
        errors="coerce",
        dayfirst=True
    )

    # 🔥 Detect date errors early
    if out["Date"].isna().any():
        raise ValueError("Date parsing failed for some rows. Check input format.")

    out["Date"] = out["Date"].dt.strftime("%Y-%m-%d")

    # 🔥 FIX 2: Merge multiline descriptions
    out = merge_multiline_transactions(out)

    # Continue processing
    out["normalized_description"] = out["Description"].apply(normalize_description)
    out["normalized_description"] = out["normalized_description"].apply(simplify_merchant)
    out["beneficiary"] = out["Description"].apply(extract_beneficiary)
    out["transaction_type"] = out.apply(
        lambda r: infer_transaction_type(r["Description"], r["Amount"]),
        axis=1
    )

    # 🔥 Add currency (default EUR for now)
    out["currency"] = "EUR"

    # Placeholder for USD (will calculate later)
    out["usd_amount"] = out["Amount"]

    return out


def parse_csv(uploaded_file):
    uploaded_file.seek(0)
    df = pd.read_csv(uploaded_file)
    return prepare_dataframe_from_tabular(df)


def parse_excel(uploaded_file):
    uploaded_file.seek(0)
    df = pd.read_excel(uploaded_file)
    return prepare_dataframe_from_tabular(df)


def parse_pdf(uploaded_file):
    if pdfplumber is not None:
        try:
            uploaded_file.seek(0)
            data = []

            with pdfplumber.open(uploaded_file) as pdf:
                for page in pdf.pages:
                    text = page.extract_text()

                    if text:
                        lines = text.split("\n")

                        for line in lines:
                            line = re.sub(r"\s+", " ", line).strip()

                            match = re.search(
                                r"(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})\s+(.*?)\s+(-?\d[\d,]*\.\d{2})",
                                line
                            )

                            if match:
                                date, desc, amount = match.groups()
                                amount = amount.replace(",", "")
                                data.append([date, desc, amount])

            if data:
                df = pd.DataFrame(data, columns=["Date", "Description", "Amount"])
                df["Amount"] = pd.to_numeric(df["Amount"], errors="coerce").fillna(0)
                df["Date"] = pd.to_datetime(df["Date"], errors="coerce", dayfirst=True).dt.strftime("%Y-%m-%d")

                df["normalized_description"] = df["Description"].apply(normalize_description)
                df["normalized_description"] = df["normalized_description"].apply(simplify_merchant)
                df["beneficiary"] = df["Description"].apply(extract_beneficiary)
                df["transaction_type"] = df.apply(
                    lambda r: infer_transaction_type(r["Description"], r["Amount"]),
                    axis=1
                )
                return df
        except Exception:
            pass

    raise ValueError("Could not extract data from PDF.")
