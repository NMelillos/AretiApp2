import re
from datetime import datetime

import pandas as pd

from utils import extract_beneficiary, infer_transaction_type, normalize_description, simplify_merchant


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


DATE_NAMES = {"date", "transaction date", "booking date", "value date", "posted date"}
AMOUNT_NAMES = {"amount", "transaction amount", "paid in", "paid out"}
DEBIT_NAMES = {"debit", "withdrawal", "paid out", "out"}
CREDIT_NAMES = {"credit", "deposit", "paid in", "in"}
IGNORE_TEXT_HINTS = {"balance", "currency", "rate", "account", "iban", "number"}
MONTH_DATE_RE = re.compile(
    r"^(?P<date>(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)\s+"
    r"\d{1,2},\s+\d{4})\s+(?P<rest>.+)$",
    re.IGNORECASE,
)
NUMERIC_DATE_RE = re.compile(r"^(?P<date>\d{1,2}[/-]\d{1,2}[/-]\d{2,4})\s+(?P<rest>.+)$")
MONEY_RE = re.compile(r"-?\s*(?:[$€£]|USD|EUR|GBP|M\$)?\s*\(?\d[\d,]*\.\d{2}\)?", re.IGNORECASE)
PDF_SKIP_PREFIXES = (
    "EUR Statement",
    "Generated on",
    "Revolut Bank",
    "Report lost",
    "Get help",
    "Scan the QR",
    "IBAN",
    "BIC",
    "Date Description",
    "Transaction Merchant Name",
    "Product Opening",
    "Total ",
    "The balance on",
    "Account transactions",
    "© ",
    "Page ",
    "To contact us",
    "Information About",
    "How to Avoid",
    "Calculation table",
    "Annual percentage",
    "Balance type",
    "PURCHASES",
    "PURCHASE",
    "PAYMENTS",
    "Payments Amount",
    "Credits Amount",
    "Spend Amount",
    "Detail ",
    "Detail Continued",
    "Continued on",
    "Summary",
    "CARDHOLDER SUMMARY",
    "ACCOUNT SUMMARY",
)
PDF_SKIP_CONTAINS = (
    "registered address",
    "European Central Bank",
    "Bank of Lithuania",
    "deposit guarantee",
    "iidraudimas",
    "lost or stolen",
    "Customer Service",
    "Credit Reporting",
    "Minimum Payment Warning",
    "Late Payment Warning",
    "Your Rights",
    "Balance Subject",
    "Interest Charge Calculation",
    "AAdvantage",
    "American Airlines",
    "terms and conditions",
    "conditions apply",
    "Account messages",
    "totals year-to-date",
    "available credit",
    "For more information",
    "Public Institution",
    "Important information",
    "About Trailing Interest",
    "Member Agreement",
    "Variable APR",
    "pre-set spending limit",
    "minimum due",
    "Important Notices",
    "EFT Error",
    "Tell us your",
    "suspected error",
    "our investigation",
    "Benefit Removal",
    "Amex Experiences",
    "Centurion Lounge",
    "End of Important Notices",
    "Cards Warmly Welcomed",
    "Credit Limit",
    "Minimum payment",
    "Annual Percentage",
    "Billing Cycle",
    "Statement Date",
    "Variable Rate",
    "Loyalty Points",
    "Flight Symbol",
    "Rewards and Benefits",
    "with or without notice",
    "you may",
    "interest charges",
    "Balance Type Percentage",
    "Days in Billing Period",
    "Daily Balance Method",
    "Average Daily Balance",
    "My Chase Loan",
    "Citibank",
    "Citi and Arc Design",
    "Citigroup Inc",
    "marks used herein",
    "registered throughout",
    "Relay Service",
    "Message & data",
    "payment confirmation text",
    "recent income",
    "housing information",
    "Manage your account",
    "Chase Mobile",
    "Ultimate Rewards",
    "citi.com",
)


def _norm_col(name):
    return str(name).strip().lower().replace("_", " ")


def _parse_amount(value):
    if pd.isna(value):
        return 0.0
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if not text:
        return 0.0
    negative = text.startswith("(") and text.endswith(")")
    if text.replace(" ", "").startswith("-"):
        negative = True
    text = text.replace("(", "").replace(")", "")
    text = re.sub(r"[^0-9,\.\-]", "", text)
    if "," in text and "." in text:
        text = text.replace(",", "")
    elif "," in text and "." not in text:
        text = text.replace(",", ".")
    amount = pd.to_numeric(text, errors="coerce")
    if pd.isna(amount):
        return 0.0
    amount = float(amount)
    return -abs(amount) if negative else amount


def _parse_pdf_date(value):
    text = str(value).replace("Sept ", "Sep ").strip()
    if re.match(r"^\d{4}-\d{2}-\d{2}$", text):
        return text
    if re.match(r"^(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s+", text, re.IGNORECASE):
        parsed = pd.to_datetime(text, errors="coerce", format="%b %d, %Y")
    else:
        parsed = pd.to_datetime(text, errors="coerce", dayfirst=True)
    if pd.isna(parsed):
        return ""
    return parsed.strftime("%Y-%m-%d")


def _parse_any_date(value, default_year=None):
    text = str(value).replace("Sept ", "Sep ").strip()
    if not text:
        return ""
    if re.match(r"^[A-Za-z]+\s*\d{1,2}$", text) and default_year:
        text = f"{text}, {default_year}"
    if re.match(r"^\d{1,2}/\d{1,2}$", text) and default_year:
        text = f"{text}/{default_year}"
    if re.match(r"^\d{1,2}[/-]\d{1,2}[/-]\d{2,4}$", text):
        parsed = pd.to_datetime(text, errors="coerce", dayfirst=False)
    elif re.match(r"^[A-Za-z]+", text):
        compact = re.sub(r"([A-Za-z]+)(\d)", r"\1 \2", text)
        compact = re.sub(r",(\d)", r", \1", compact)
        parsed = pd.to_datetime(compact, errors="coerce")
    else:
        parsed = pd.to_datetime(text, errors="coerce", dayfirst=True)
    if pd.isna(parsed):
        return ""
    return parsed.strftime("%Y-%m-%d")


def _parse_tabular_date(value):
    if pd.isna(value):
        return ""
    if isinstance(value, datetime):
        parsed = pd.to_datetime(value, errors="coerce")
    else:
        text = str(value).strip()
        if not text:
            return ""
        if re.match(r"^\d{4}[-/]\d{1,2}[-/]\d{1,2}$", text):
            parsed = pd.to_datetime(text, errors="coerce", dayfirst=False)
        else:
            parsed = pd.to_datetime(text, errors="coerce", dayfirst=True)
    if pd.isna(parsed):
        return ""
    return parsed.strftime("%Y-%m-%d")


def _money_values(text):
    return [(match, _parse_amount(match.group(0))) for match in MONEY_RE.finditer(text)]


def _is_pdf_noise(line):
    if not line:
        return True
    lower = line.lower()
    plain = re.sub(r"(.)\1+", r"\1", lower)
    return (
        any(lower.startswith(prefix.lower()) for prefix in PDF_SKIP_PREFIXES)
        or any(token.lower() in lower or token.lower() in plain for token in PDF_SKIP_CONTAINS)
    )


def _append_detail(description, line):
    line = line.strip()
    if not line or _is_pdf_noise(line):
        return description
    if line in {"Page 1 of 4", "Page 2 of 4", "Page 3 of 4", "Page 4 of 4"}:
        return description
    return f"{description} | {line}" if description else line


def _parse_revolut_pdf_text(text):
    lines = [re.sub(r"\s+", " ", line).strip() for line in text.splitlines()]
    rows = []
    current = None
    previous_balance = None

    for line in lines:
        if not line:
            continue

        summary_match = re.search(r"Account \(Current Account\)\s+(.+)", line)
        if summary_match and previous_balance is None:
            amounts = _money_values(summary_match.group(1))
            if amounts:
                previous_balance = amounts[0][1]
            continue

        date_match = MONTH_DATE_RE.match(line)
        if date_match:
            if current:
                rows.append(current)

            date_value = _parse_pdf_date(date_match.group("date"))
            rest = date_match.group("rest")
            amounts = _money_values(rest)
            if len(amounts) < 2:
                current = None
                continue

            txn_abs = abs(amounts[-2][1])
            new_balance = amounts[-1][1]
            description = rest[: amounts[-2][0].start()].strip()

            amount = None
            if previous_balance is not None:
                delta = round(new_balance - previous_balance, 2)
                if abs(abs(delta) - txn_abs) <= 0.02:
                    amount = delta

            if amount is None:
                upper_desc = description.upper()
                incoming_hint = any(token in upper_desc for token in ["TRANSFER FROM", "TOP-UP", "REFUND"])
                amount = txn_abs if incoming_hint else -txn_abs

            previous_balance = new_balance
            current = [date_value, description, amount]
            continue

        if current:
            current[1] = _append_detail(current[1], line)

    if current:
        rows.append(current)

    return rows


def _statement_year(text):
    compact = re.sub(r"\s+", " ", text)
    patterns = [
        r"Opening/Closing Date\s+\d{1,2}/\d{1,2}/(\d{2,4})\s+-\s+\d{1,2}/\d{1,2}/(\d{2,4})",
        r"Closing Date\s*(\d{1,2}/\d{1,2}/(\d{2,4}))",
        r"New balance as of\s+\d{1,2}/\d{1,2}/(\d{2,4})",
        r"(\w+\s*\d{1,2},\s*\d{4})\s*to\s*(\w+\s*\d{1,2},\s*\d{4})",
        r"Account transactions from\s+\w+\s+\d{1,2},\s+(\d{4})\s+to\s+\w+\s+\d{1,2},\s+(\d{4})",
    ]
    for pattern in patterns:
        match = re.search(pattern, compact, re.IGNORECASE)
        if not match:
            continue
        for group in reversed(match.groups()):
            if not group:
                continue
            date_value = _parse_any_date(group)
            if date_value:
                return int(date_value[:4])
            if re.match(r"^\d{2}$", str(group)):
                return 2000 + int(group)
            if re.match(r"^\d{4}$", str(group)):
                return int(group)
    return datetime.now().year


def _parse_credit_card_pdf_text(text):
    default_year = _statement_year(text)
    rows = []
    current = None
    date_line_re = re.compile(
        r"^(?P<date>\d{1,2}/\d{1,2}(?:/\d{2,4})?)\s+"
        r"(?:(?P<post>\d{1,2}/\d{1,2}(?:/\d{2,4})?)\s+)?"
        r"(?P<rest>.+)$"
    )

    for raw_line in text.splitlines():
        line = re.sub(r"\s+", " ", raw_line).strip()
        if not line:
            continue

        match = date_line_re.match(line)
        if match:
            amounts = _money_values(match.group("rest"))
            if amounts:
                if current:
                    rows.append(current)
                amount_text = amounts[-1][0].group(0)
                amount = _parse_amount(amount_text)
                amount = abs(amount) if "-" in amount_text else -abs(amount)
                description = match.group("rest")[: amounts[-1][0].start()].strip()
                description = re.sub(r"^\d{1,2}/\d{1,2}(?:/\d{2,4})?\s+", "", description).strip()
                if not description or _is_pdf_noise(description):
                    current = None
                    continue
                current = [_parse_any_date(match.group("date"), default_year), description, amount]
                continue

        if current and _should_append_card_detail(line):
            current_description = str(current[1]).lower()
            if "interest charged" in current_description or "interest charge on" in current_description:
                continue
            current[1] = _append_detail(current[1], line)

    if current:
        rows.append(current)
    return rows


def _should_append_card_detail(line):
    if _is_pdf_noise(line):
        return False
    if len(line) > 90:
        return False
    if len(line) <= 1:
        return False
    if re.fullmatch(r"[\W\d\s]+", line):
        return False
    if len(_money_values(line)) >= 2:
        return False
    lower = line.lower()
    blocked = [
        "program",
        "conditions",
        "terms",
        "about trailing interest",
        "member agreement",
        "variable apr",
        "pre-set spending",
        "minimum due",
        "important notices",
        "eft error",
        "tell us your",
        "suspected error",
        "investigation",
        "benefit removal",
        "amex experiences",
        "centurion lounge",
        "cards warmly welcomed",
        "fees charged",
        "interest charged",
        "new charges",
        "pay in full",
        "pay over time",
        "interest rate",
        "cash advances",
        "balance transfers",
        "annual balance",
        "payments/credits",
        "minimum payment",
        "customer service",
        "credit limit",
        "important information",
        "american airlines",
        "loyalty points",
        "flight symbol",
        "citi.com",
        "you may",
        "received",
        "balance type",
        "balance method",
        "advances",
        "days in billing",
        "daily balance",
        "average daily",
        "my chase loan",
        "determined by",
        "citibank",
        "citi and arc",
        "citigroup",
        "marks used herein",
        "registered throughout",
        "text pay",
        "message & data",
        "confirm your identity",
        "payment confirmation",
        "account information",
        "recent income",
        "housing",
        "securely log",
        "timur bekmambetov",
        "tengri inc",
        "total ",
        "page ",
        "account ending",
        "card ending",
        "foreign",
    ]
    return not any(token in lower for token in blocked)


def _parse_comerica_pdf_text(text):
    default_year = _statement_year(text)
    rows = []
    line_re = re.compile(
        r"(?P<date>(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s*\d{1,2})\s+"
        r"(?P<amount>-?\d[\d,]*\.\d{2})\s+"
        r"(?P<desc>.*?)(?=\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s*\d{1,2}\s+-?\d[\d,]*\.\d{2}\s+|$)",
        re.IGNORECASE,
    )

    for raw_line in text.splitlines():
        line = re.sub(r"\s+", " ", raw_line).strip()
        if not line:
            continue
        for match in line_re.finditer(line):
            description = match.group("desc").strip()
            if not description or _is_pdf_noise(description):
                continue
            rows.append([
                _parse_any_date(match.group("date"), default_year),
                description,
                _parse_amount(match.group("amount")),
            ])
    return rows


def _parse_generic_pdf_text(text):
    rows = []
    current = None

    for raw_line in text.splitlines():
        line = re.sub(r"\s+", " ", raw_line).strip()
        if not line:
            continue

        date_match = NUMERIC_DATE_RE.match(line)
        if date_match:
            rest = date_match.group("rest")
            amount_match = re.search(r"(-?\(?[\d,]+\.\d{2}\)?)\s*$", rest)
            if amount_match:
                if current:
                    rows.append(current)
                description = rest[: amount_match.start()].strip()
                current = [date_match.group("date"), description, amount_match.group(1)]
                continue

        if current:
            current[1] = _append_detail(current[1], line)

    if current:
        rows.append(current)
    return rows


def _detect_bank_name(text, file_name=""):
    sample = f"{file_name} {text[:2000]}".upper()
    plain_sample = re.sub(r"(.)\1+", r"\1", sample)
    if "REVOLUT" in sample or "REVOLUT" in plain_sample:
        return "Revolut"
    if "COMERICA" in sample or "COMERICA" in plain_sample:
        return "Comerica"
    if (
        "AMERICAN EXPRESS" in sample
        or "AMEX" in sample
        or "BUSINESS PLATINUM CARD" in sample
        or "AMERICAN EXPRESS" in plain_sample
    ):
        return "American Express"
    if "CITI" in sample or "CITICARDS" in sample or "CITI" in plain_sample:
        return "Citi"
    if (
        "CARDMEMBER SERVICE" in sample
        or "CHASE SAPPHIRE" in sample
        or "JPMORGAN CHASE" in sample
        or "CHASE.COM" in plain_sample
        or "CHASE ULTIMATE" in plain_sample
    ):
        return "Chase"
    return ""


def _extract_account_number(text):
    patterns = [
        r"Account Number:\s*([X\d ]{4,})",
        r"Account\s*number\s*ending\s*in:\s*([X\d\- ]{3,})",
        r"Accountnumber\s*([X\d\- ]{3,})",
        r"Account Ending\s*([X\d\- ]{3,})",
        r"IBAN\s+([A-Z0-9]+)",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return re.sub(r"\s+", " ", match.group(1)).strip()
    return ""


def _extract_currency(text):
    head = text[:2000].upper()
    if "EUR STATEMENT" in head or "EUR" in head or "€" in head:
        return "EUR"
    if "EUR STATEMENT" in head or "€" in head:
        return "EUR"
    if "$" in head or "USD" in head:
        return "USD"
    return ""


def _find_first_amount(flat_text, patterns):
    for pattern in patterns:
        match = re.search(pattern, flat_text, re.IGNORECASE)
        if match:
            return _parse_amount(match.group(1))
    return None


def _find_statement_period(flat_text):
    patterns = [
        r"Account transactions from\s+([A-Za-z]+\s*\d{1,2},\s*\d{4})\s+to\s+([A-Za-z]+\s*\d{1,2},\s*\d{4})",
        r"Account transactions from\s+(.+?)\s+to\s+(.+?)(?:\s+Date|\s+Balance|$)",
        r"Opening/Closing Date\s+(\d{1,2}/\d{1,2}/\d{2,4})\s+-\s+(\d{1,2}/\d{1,2}/\d{2,4})",
        r"Statement period from\s+([A-Za-z]+\s*\d{1,2},\s*\d{4})\s+to\s+([A-Za-z]+\s*\d{1,2},\s*\d{4})",
        r"([A-Za-z]+\s*\d{1,2},\s*\d{4})\s*to\s*([A-Za-z]+\s*\d{1,2},\s*\d{4})",
    ]
    for pattern in patterns:
        match = re.search(pattern, flat_text, re.IGNORECASE)
        if match:
            return _parse_any_date(match.group(1)), _parse_any_date(match.group(2))
    end_match = re.search(r"Closing Date\s*(\d{1,2}/\d{1,2}/\d{2,4})", flat_text, re.IGNORECASE)
    if end_match:
        return "", _parse_any_date(end_match.group(1))
    end_match = re.search(r"New balance as of\s+(\d{1,2}/\d{1,2}/\d{2,4})", flat_text, re.IGNORECASE)
    if end_match:
        return "", _parse_any_date(end_match.group(1))
    return "", ""


def extract_statement_balance_from_text(text, file_name=""):
    flat = re.sub(r"\s+", " ", text)
    bank = _detect_bank_name(text, file_name)
    period_start, period_end = _find_statement_period(flat)
    currency = _extract_currency(text)
    account_number = _extract_account_number(text)

    opening = closing = money_out = money_in = None

    revolut_line = re.search(r"Account \(Current Account\).+?(?:Total|The balance)", flat, re.IGNORECASE)
    if revolut_line:
        amounts = [value for _, value in _money_values(revolut_line.group(0))]
        if len(amounts) >= 4:
            opening, money_out, money_in, closing = amounts[:4]

    if opening is None and "Account Total" in flat:
        account_total = flat.split("Account Total", 1)[1]
        opening = _find_first_amount(account_total, [r"Previous Balance\s+(-?\$?\d[\d,]*\.\d{2})"])
        closing = _find_first_amount(account_total, [r"New Balance\s+(-?\$?\d[\d,]*\.\d{2})"])
        money_in = _find_first_amount(account_total, [r"Payments/Credits\s+(-?\$?\d[\d,]*\.\d{2})"])
        money_out = _find_first_amount(account_total, [r"New Charges\s+\+?(-?\$?\d[\d,]*\.\d{2})"])

    if bank == "Comerica":
        deposits = []
        for value in [
            _find_first_amount(flat, [r"Electronic deposits\s+(-?\$?\d[\d,]*\.\d{2})"]),
            _find_first_amount(flat, [r"Electronic deposits\s+\d+\s+(-?\$?\d[\d,]*\.\d{2})"]),
            _find_first_amount(flat, [r"Electronic\s*deposits\s+(-?\$?\d[\d,]*\.\d{2})"]),
            _find_first_amount(flat, [r"Paper deposits\s+(-?\$?\d[\d,]*\.\d{2})"]),
            _find_first_amount(flat, [r"Paper deposits\s+\d+\s+(-?\$?\d[\d,]*\.\d{2})"]),
            _find_first_amount(flat, [r"Paper\s*deposits\s+(-?\$?\d[\d,]*\.\d{2})"]),
        ]:
            if value is not None and all(abs(value - existing) > 0.005 for existing in deposits):
                deposits.append(value)
        if deposits:
            money_in = round(sum(deposits), 2)

    if opening is None:
        opening = _find_first_amount(flat, [
            r"Beginning\s*balance.*?(-?\$?\d[\d,]*\.\d{2})",
            r"Previous\s+Balance\s+(-?\$?\d[\d,]*\.\d{2})",
            r"Previous\s+balance\s+(-?\$?\d[\d,]*\.\d{2})",
        ])
    if closing is None:
        closing = _find_first_amount(flat, [
            r"Ending\s*balance\s+(-?\$?\d[\d,]*\.\d{2})",
            r"New\s+Balance\s*=\s*(-?\$?\d[\d,]*\.\d{2})",
            r"New\s+Balance\s+(-?\$?\d[\d,]*\.\d{2})",
            r"New balance as of\s+\d{1,2}/\d{1,2}/\d{2,4}:\s+(-?\$?\d[\d,]*\.\d{2})",
        ])
    if money_out is None:
        money_out = _find_first_amount(flat, [
            r"Money out\s+(-?\$?\d[\d,]*\.\d{2})",
            r"Purchases\s+\+?(-?\$?\d[\d,]*\.\d{2})",
            r"New Charges\s+\+?(-?\$?\d[\d,]*\.\d{2})",
            r"Electronic \(EFT\) withdrawals\s+(-?\$?\d[\d,]*\.\d{2})",
            r"Electronic \(EFT\) withdrawals\s+\d+\s+(-?\$?\d[\d,]*\.\d{2})",
            r"Electronic\s*\(EFT\)\s*withdrawals\s+(-?\$?\d[\d,]*\.\d{2})",
        ])
    if money_in is None:
        money_in = _find_first_amount(flat, [
            r"Money in\s+(-?\$?\d[\d,]*\.\d{2})",
            r"Payment, Credits\s+(-?\$?\d[\d,]*\.\d{2})",
            r"Payments/Credits\s+(-?\$?\d[\d,]*\.\d{2})",
            r"Payments\s+-\s+(-?\$?\d[\d,]*\.\d{2})",
            r"Payments\s+(-?\$?\d[\d,]*\.\d{2})",
            r"Electronic deposits\s+(-?\$?\d[\d,]*\.\d{2})",
            r"Electronic deposits\s+\d+\s+(-?\$?\d[\d,]*\.\d{2})",
            r"Electronic\s*deposits\s+(-?\$?\d[\d,]*\.\d{2})",
        ])

    return {
        "bank": bank,
        "account_number": account_number,
        "currency": currency,
        "period_start": period_start,
        "period_end": period_end,
        "opening_balance": opening,
        "money_out": money_out,
        "money_in": money_in,
        "closing_balance": closing,
        "source": "parsed" if opening is not None or closing is not None else "",
    }


def extract_statement_balance(uploaded_file, file_name=""):
    lower_name = str(file_name).lower()
    if not lower_name.endswith(".pdf") or pdfplumber is None:
        return {}
    uploaded_file.seek(0)
    with pdfplumber.open(uploaded_file) as pdf:
        text = "\n".join(page.extract_text() or "" for page in pdf.pages)
    return extract_statement_balance_from_text(text, file_name)


def detect_columns(df):
    date_col = None
    amount_col = None
    debit_col = None
    credit_col = None

    for col in df.columns:
        name = _norm_col(col)
        if name in DATE_NAMES or ("date" in name and date_col is None):
            date_col = col
        if name in AMOUNT_NAMES and amount_col is None:
            amount_col = col
        if name in DEBIT_NAMES and debit_col is None:
            debit_col = col
        if name in CREDIT_NAMES and credit_col is None:
            credit_col = col

    desc_cols = []
    for col in df.columns:
        name = _norm_col(col)
        if col in [date_col, amount_col, debit_col, credit_col]:
            continue
        if any(token in name for token in ["description", "details", "narration", "memo", "reference"]):
            desc_cols.append(col)

    if not desc_cols:
        for col in df.columns:
            if col in [date_col, amount_col, debit_col, credit_col]:
                continue
            name = _norm_col(col)
            if any(token in name for token in IGNORE_TEXT_HINTS):
                continue
            if df[col].dtype == object:
                desc_cols.append(col)

    return date_col, desc_cols, amount_col, debit_col, credit_col


def _combine_description(row, desc_cols):
    parts = []
    for col in desc_cols:
        value = row.get(col)
        if pd.isna(value):
            continue
        text = str(value).strip()
        if text and text.lower() != "nan":
            parts.append(text)
    return " ".join(parts)


def prepare_dataframe_from_tabular(df):
    df = df.copy()
    df = df.dropna(how="all")
    date_col, desc_cols, amount_col, debit_col, credit_col = detect_columns(df)

    if amount_col is None and debit_col and credit_col:
        debit = df[debit_col].apply(_parse_amount).abs()
        credit = df[credit_col].apply(_parse_amount).abs()
        df["Amount"] = credit - debit
        amount_col = "Amount"

    if not date_col or not desc_cols or not amount_col:
        raise ValueError(
            "Could not detect Date, Description, and Amount columns. "
            "Please check the statement format before import."
        )

    out = pd.DataFrame()
    out["Date"] = df[date_col].apply(_parse_tabular_date)
    out["Description"] = df.apply(lambda row: _combine_description(row, desc_cols), axis=1)
    out["Amount"] = df[amount_col].apply(_parse_amount)
    out = out[(out["Description"].str.strip() != "") | (out["Amount"] != 0)].copy()

    out["Date"] = out["Date"].fillna("")
    out["Description"] = out["Description"].fillna("").astype(str)
    out["normalized_description"] = out["Description"].apply(normalize_description).apply(simplify_merchant)
    out["beneficiary"] = out["Description"].apply(extract_beneficiary)
    out["transaction_type"] = out.apply(
        lambda row: infer_transaction_type(row["Description"], row["Amount"]),
        axis=1,
    )
    return out.reset_index(drop=True)


def parse_csv(uploaded_file):
    uploaded_file.seek(0)
    df = pd.read_csv(uploaded_file)
    return prepare_dataframe_from_tabular(df)


def parse_excel(uploaded_file):
    uploaded_file.seek(0)
    df = pd.read_excel(uploaded_file)
    return prepare_dataframe_from_tabular(df)


def _frame_from_pdf_rows(rows):
    df = pd.DataFrame(rows, columns=["Date", "Description", "Amount"])
    df["Date"] = df["Date"].apply(_parse_pdf_date)
    df["Amount"] = df["Amount"].apply(_parse_amount)
    df["Description"] = df["Description"].fillna("").astype(str)
    df["normalized_description"] = df["Description"].apply(normalize_description).apply(simplify_merchant)
    df["beneficiary"] = df["Description"].apply(extract_beneficiary)
    df["transaction_type"] = df.apply(
        lambda row: infer_transaction_type(row["Description"], row["Amount"]),
        axis=1,
    )
    return df.reset_index(drop=True)


def parse_pdf(uploaded_file):
    rows = []
    if pdfplumber is not None:
        uploaded_file.seek(0)
        try:
            with pdfplumber.open(uploaded_file) as pdf:
                text = "\n".join(page.extract_text() or "" for page in pdf.pages)
                if "Revolut Bank" in text or "Account transactions from" in text:
                    rows = _parse_revolut_pdf_text(text)
                elif "Comerica" in text or "Commercial Checking" in text:
                    rows = _parse_comerica_pdf_text(text)
                elif (
                    "CARDMEMBER SERVICE" in text
                    or "CITI" in text
                    or "American Express" in text
                    or "Account Ending" in text
                ):
                    rows = _parse_credit_card_pdf_text(text)
                if not rows:
                    rows = _parse_generic_pdf_text(text)
            if rows:
                return _frame_from_pdf_rows(rows)
        except Exception:
            rows = []

    if pytesseract is not None and convert_from_bytes is not None:
        uploaded_file.seek(0)
        try:
            images = convert_from_bytes(uploaded_file.read())
            fallback_date = datetime.now().strftime("%Y-%m-%d")
            for image in images:
                text = pytesseract.image_to_string(image)
                for line in text.splitlines():
                    line = re.sub(r"\s+", " ", line).strip()
                    match = re.search(
                        r"(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})\s+(.*?)\s+(-?\(?[\d,]+\.\d{2}\)?)",
                        line,
                    )
                    if match:
                        rows.append(list(match.groups()))
                        continue
                    match = re.search(r"(.+?)\s+(-?\(?[\d,]+\.\d{2}\)?)$", line)
                    if match and len(match.group(1).strip()) > 3:
                        rows.append([fallback_date, match.group(1), match.group(2)])
            if rows:
                return _frame_from_pdf_rows(rows)
        except Exception:
            pass

    raise ValueError("Could not extract statement rows from this PDF.")
