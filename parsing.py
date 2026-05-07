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
REVOLUT_DATE_PATTERN = re.compile(r"^(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+\d{1,2},\s+\d{4}$")
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
REVOLUT_NOISE_MARKERS = [
    "revolut",
    "eur statement",
    "generated on",
    "revolut bank uab",
    "balance summary",
    "opening balance",
    "closing balance",
    "account transactions from",
    "report lost or stolen card",
    "get help directly in-app",
    "scan the qr code",
    "authorization code",
    "deposit protection",
    "www.lietuvosbankas.lt",
    "page ",
]


def build_parsed_transaction_df(df: pd.DataFrame, currency: str = "EUR"):
    work = df.copy()
    work["Amount"] = pd.to_numeric(work["Amount"], errors="coerce").fillna(0)
    work["Date"] = pd.to_datetime(work["Date"], errors="coerce", dayfirst=True)
    work = work.dropna(subset=["Date"]).copy()
    work["Date"] = work["Date"].dt.strftime("%Y-%m-%d")
    work["Description"] = work["Description"].fillna("").astype(str).str.strip()

    work["normalized_description"] = work["Description"].apply(normalize_description)
    work["normalized_description"] = work["normalized_description"].apply(simplify_merchant)
    work["beneficiary"] = work["Description"].apply(extract_beneficiary)
    work["transaction_type"] = work.apply(
        lambda r: infer_transaction_type(r["Description"], r["Amount"]),
        axis=1
    )
    work["currency"] = currency
    work["usd_amount"] = work["Amount"]
    return work


def group_pdf_words_by_line(words, tolerance: float = 3.0):
    grouped_lines = []

    for word in sorted(words, key=lambda item: (item["top"], item["x0"])):
        if not grouped_lines or abs(word["top"] - grouped_lines[-1]["top"]) > tolerance:
            grouped_lines.append({"top": word["top"], "words": [word]})
        else:
            grouped_lines[-1]["words"].append(word)

    for line in grouped_lines:
        line["words"] = sorted(line["words"], key=lambda item: item["x0"])

    return grouped_lines


def is_revolut_pdf_noise_line(text: str):
    normalized_text = re.sub(r"\s+", " ", str(text)).strip().lower()
    if not normalized_text:
        return True

    return any(marker in normalized_text for marker in REVOLUT_NOISE_MARKERS)


def detect_revolut_header_positions(line_words):
    positions = {}

    for word in line_words:
        text = str(word["text"]).strip().lower()
        if text == "date" and "date" not in positions:
            positions["date"] = word["x0"]
        elif text == "description":
            positions["description"] = word["x0"]
        elif text == "money":
            positions.setdefault("money", []).append(word["x0"])
        elif text == "balance":
            positions["balance"] = word["x0"]

    if "description" not in positions or "balance" not in positions or len(positions.get("money", [])) < 2:
        return None

    money_positions = sorted(positions["money"])
    return {
        "date": positions.get("date", 0),
        "description": positions["description"],
        "money_out": money_positions[0],
        "money_in": money_positions[1],
        "balance": positions["balance"],
    }


def parse_revolut_amount(text: str):
    cleaned = str(text).replace(",", "").replace("€", "").strip()
    match = re.search(r"-?\d[\d,]*\.\d{2}", cleaned)
    if not match:
        return None
    return float(match.group(0).replace(",", ""))


def parse_revolut_transaction_line(line_words, header_positions):
    date_boundary = header_positions["description"] - 8
    description_boundary = header_positions["money_out"] - 8
    money_in_boundary = header_positions["balance"] - 8

    date_parts = [word["text"] for word in line_words if word["x0"] < date_boundary]
    description_parts = [
        word["text"] for word in line_words
        if header_positions["description"] - 8 <= word["x0"] < description_boundary
    ]
    money_out_parts = [
        word["text"] for word in line_words
        if header_positions["money_out"] - 8 <= word["x0"] < header_positions["money_in"] - 8
    ]
    money_in_parts = [
        word["text"] for word in line_words
        if header_positions["money_in"] - 8 <= word["x0"] < money_in_boundary
    ]

    date_text = " ".join(date_parts).strip()
    description_text = " ".join(description_parts).strip()
    money_out_text = " ".join(money_out_parts).strip()
    money_in_text = " ".join(money_in_parts).strip()

    if not REVOLUT_DATE_PATTERN.match(date_text):
        return None

    money_out_amount = parse_revolut_amount(money_out_text)
    money_in_amount = parse_revolut_amount(money_in_text)

    if money_out_amount is None and money_in_amount is None:
        return None

    amount = money_in_amount if money_in_amount is not None else -money_out_amount
    return {
        "Date": date_text,
        "Description": description_text,
        "Amount": amount,
    }


def parse_revolut_pdf(uploaded_file):
    if pdfplumber is None:
        return None

    uploaded_file.seek(0)
    transactions = []

    with pdfplumber.open(uploaded_file) as pdf:
        first_page_text = (pdf.pages[0].extract_text() or "").lower() if pdf.pages else ""
        if "revolut" not in first_page_text:
            return None

        for page in pdf.pages:
            words = page.extract_words(x_tolerance=1, y_tolerance=3, keep_blank_chars=False)
            if not words:
                continue

            lines = group_pdf_words_by_line(words)
            header_positions = None
            current_transaction = None

            for line in lines:
                line_text = " ".join(word["text"] for word in line["words"]).strip()
                if not line_text:
                    continue

                if header_positions is None:
                    header_positions = detect_revolut_header_positions(line["words"])
                    continue

                if is_revolut_pdf_noise_line(line_text):
                    continue

                transaction = parse_revolut_transaction_line(line["words"], header_positions)
                if transaction is not None:
                    if current_transaction is not None:
                        transactions.append(current_transaction)
                    current_transaction = transaction
                    continue

                if current_transaction is None:
                    continue

                description_only_parts = [
                    word["text"]
                    for word in line["words"]
                    if header_positions["description"] - 8 <= word["x0"] < header_positions["money_out"] - 8
                ]
                continuation_text = " ".join(description_only_parts).strip()
                if continuation_text and not is_revolut_pdf_noise_line(continuation_text):
                    current_transaction["Description"] = (
                        f"{current_transaction['Description']} {continuation_text}"
                    ).strip()

            if current_transaction is not None:
                transactions.append(current_transaction)

    if not transactions:
        return None

    return build_parsed_transaction_df(pd.DataFrame(transactions), currency="EUR")


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
    revolut_df = parse_revolut_pdf(uploaded_file)
    if revolut_df is not None and not revolut_df.empty:
        return revolut_df

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
                return build_parsed_transaction_df(df, currency="EUR")
        except Exception:
            pass

    raise ValueError("Could not extract data from PDF.")
