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


DATE_AT_START_PATTERN = re.compile(r"^(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})\s+")
AMOUNT_TOKEN_PATTERN = re.compile(r"-?\d[\d,]*\.\d{2}")
PDF_NOISE_MARKERS = [
    "bank of cyprus",
    "privacy statement",
    "privacystatement",
    "deposit guarantee scheme",
    "depositguaranteescheme",
    "analysis of interest rate",
    "please review the present statement",
    "if you wish to contact your branch",
    "you can reach this",
    "page ",
    "do not print",
    "www.",
    "http://",
    "https://",
]
PDF_BALANCE_SUFFIX_MARKERS = [
    "total / balance carried forward",
    "balance carried forward",
    "balance brought forward",
]


def extract_pdf_transaction_parts(line: str):
    normalized_line = re.sub(r"\s+", " ", str(line)).strip()
    if not normalized_line:
        return None

    date_match = DATE_AT_START_PATTERN.match(normalized_line)
    if not date_match:
        return None

    candidate_line = normalized_line
    candidate_line_lower = candidate_line.lower()
    for marker in PDF_BALANCE_SUFFIX_MARKERS:
        marker_index = candidate_line_lower.find(marker)
        if marker_index != -1:
            candidate_line = candidate_line[:marker_index].rstrip()
            break

    amount_matches = list(AMOUNT_TOKEN_PATTERN.finditer(candidate_line))
    if not amount_matches:
        return None

    selected_amount = amount_matches[-1]
    if len(amount_matches) >= 2 and amount_matches[-1].end() == len(candidate_line):
        # Many bank PDFs end the row with running balance, not transaction amount.
        selected_amount = amount_matches[-2]

    description = candidate_line[date_match.end():selected_amount.start()].strip()
    if not description:
        return None

    return (
        date_match.group(1),
        description,
        selected_amount.group(0).replace(",", ""),
    )


def is_pdf_noise_line(line: str):
    normalized_line = re.sub(r"\s+", " ", str(line)).strip().lower()
    if not normalized_line:
        return True

    if any(marker in normalized_line for marker in PDF_NOISE_MARKERS):
        return True

    compact_line = normalized_line.replace(" ", "")
    if "page" in normalized_line and re.search(r"\d+\s*/\s*\d+", normalized_line):
        return True

    if re.search(r"\biban\b|\bbic\b|\bbranch\b|\bstatement\b", normalized_line) and len(normalized_line) > 40:
        return True

    if normalized_line.count("%") >= 2:
        return True

    if "." in compact_line and len(re.findall(r"[a-z]", compact_line)) > 20 and compact_line.count(".") >= 3:
        return True

    return False


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
                        current_transaction = None

                        for line in lines:
                            line = re.sub(r"\s+", " ", line).strip()
                            if not line:
                                continue

                            match = extract_pdf_transaction_parts(line)

                            if match:
                                if current_transaction is not None:
                                    data.append(current_transaction)

                                date, desc, amount = match
                                current_transaction = [
                                    date,
                                    desc.strip(),
                                    amount.replace(",", ""),
                                ]
                            elif current_transaction is not None and not is_pdf_noise_line(line):
                                current_transaction[1] = (
                                    f"{current_transaction[1]} {line}"
                                ).strip()

                        if current_transaction is not None:
                            data.append(current_transaction)

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
