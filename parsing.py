# =========================
# FILE: parsing.py
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


def prepare_dataframe_from_tabular(df: pd.DataFrame):
    date_col, desc_col, amount_col, debit_col, credit_col = detect_columns(df)

    if amount_col is None and debit_col and credit_col:
        df[debit_col] = pd.to_numeric(df[debit_col], errors="coerce").fillna(0)
        df[credit_col] = pd.to_numeric(df[credit_col], errors="coerce").fillna(0)
        df["Amount"] = df[credit_col] - df[debit_col]
        amount_col = "Amount"

    if not all([date_col, desc_col, amount_col]):
        raise ValueError(
            "Could not detect the required columns. "
            "Expected something similar to Date, Description, Amount "
            "or separate Debit/Credit columns."
        )

    out = df[[date_col, desc_col, amount_col]].copy()
    out.columns = ["Date", "Description", "Amount"]

    out["Description"] = out["Description"].fillna("").astype(str)
    out["Amount"] = pd.to_numeric(out["Amount"], errors="coerce").fillna(0)
    out["Date"] = pd.to_datetime(out["Date"], errors="coerce").dt.strftime("%Y-%m-%d")

    out["normalized_description"] = out["Description"].apply(normalize_description)
    out["normalized_description"] = out["normalized_description"].apply(simplify_merchant)
    out["beneficiary"] = out["Description"].apply(extract_beneficiary)
    out["transaction_type"] = out.apply(
        lambda r: infer_transaction_type(r["Description"], r["Amount"]),
        axis=1
    )

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
                df["Date"] = pd.to_datetime(df["Date"], errors="coerce").dt.strftime("%Y-%m-%d")
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

    if pytesseract is not None and convert_from_bytes is not None:
        try:
            uploaded_file.seek(0)
            images = convert_from_bytes(uploaded_file.read())
            data = []

            for img in images:
                text = pytesseract.image_to_string(img)

                date_match = re.search(r"(\d{2}/\d{2}/\d{4})", text)
                date = date_match.group(1) if date_match else datetime.now().strftime("%Y-%m-%d")

                lines = text.split("\n")

                for line in lines:
                    line = re.sub(r"\s+", " ", line).strip()

                    match_statement = re.search(
                        r"(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})\s+(.*?)\s+(-?\d[\d,]*\.\d{2})",
                        line
                    )
                    if match_statement:
                        row_date, desc, amount = match_statement.groups()
                        amount = amount.replace(",", "")
                        data.append([row_date, desc, amount])
                        continue

                    match_item = re.search(r"(.*?)\s+(\d+\.\d{2})$", line)
                    if match_item:
                        desc, amount = match_item.groups()
                        if len(desc.strip()) > 3 and not desc.lower().startswith("total"):
                            data.append([date, desc, "-" + amount])

            if data:
                df = pd.DataFrame(data, columns=["Date", "Description", "Amount"])
                df["Amount"] = pd.to_numeric(df["Amount"], errors="coerce").fillna(0)
                df["Date"] = pd.to_datetime(df["Date"], errors="coerce").dt.strftime("%Y-%m-%d")
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

    raise ValueError(
        "Could not extract data from PDF. "
        "If this is a scanned PDF, install OCR support (pytesseract + pdf2image + Tesseract + Poppler)."
    )
