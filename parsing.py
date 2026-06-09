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
CURRENCY_NAMES = {"currency", "curr", "ccy", "transaction currency", "original currency"}
IGNORE_TEXT_HINTS = {"balance", "currency", "rate", "account", "iban", "number"}
MONTH_DATE_RE = re.compile(
    r"^(?P<date>(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)\s+"
    r"\d{1,2},\s+\d{4})\s+(?P<rest>.+)$",
    re.IGNORECASE,
)
NUMERIC_DATE_RE = re.compile(r"^(?P<date>\d{1,2}[/-]\d{1,2}[/-]\d{2,4})\s+(?P<rest>.+)$")
MONEY_RE = re.compile(r"-?\s*(?:[$€£]|USD|EUR|GBP|M\$)?\s*\(?\d[\d,]*\.\d{2}\)?", re.IGNORECASE)
MONEY_TOKEN = r"-?\s*(?:[$€£]|USD|EUR|GBP|M\$)?\s*\(?\d[\d,]*\.\d{2}\)?"
MONEY_PREFIX_TOKEN = r"(?:US\$|M\$|\$|\u20ac|\u00a3|USD|EUR|GBP|\u00e2\u201a\u00ac|\u00c2\u00a3|\u03b2\u201a\u00ac|\u0392\u00a3)"
MONEY_RE = re.compile(rf"-?\s*(?:{MONEY_PREFIX_TOKEN})?\s*\(?\d[\d,]*\.\d{{2}}\)?", re.IGNORECASE)
MONEY_TOKEN = rf"-?\s*(?:{MONEY_PREFIX_TOKEN})?\s*\(?\d[\d,]*\.\d{{2}}\)?"
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


def _parse_us_tabular_date(value):
    if pd.isna(value):
        return ""
    if isinstance(value, datetime):
        parsed = pd.to_datetime(value, errors="coerce")
    else:
        parsed = pd.to_datetime(str(value).strip(), errors="coerce", dayfirst=False)
    if pd.isna(parsed):
        return ""
    return parsed.strftime("%Y-%m-%d")


def _money_values(text):
    return [(match, _parse_amount(match.group(0))) for match in MONEY_RE.finditer(text)]


def _money_values_with_spans(text):
    return [(match.span(), match.group(0), _parse_amount(match.group(0))) for match in MONEY_RE.finditer(text)]


def _currency_from_money_text(value):
    text = str(value or "").upper()
    if "$" in text or "USD" in text:
        return "USD"
    if "€" in text or "EUR" in text:
        return "EUR"
    if "£" in text or "GBP" in text:
        return "GBP"
    return ""


def _currency_from_money_text(value):
    raw = str(value or "")
    text = raw.upper()
    if "US$" in text or "$" in raw or "USD" in text or "DOLLAR" in text:
        return "USD"
    if (
        "\u20ac" in raw
        or "\u00e2\u201a\u00ac" in raw
        or "\u03b2\u201a\u00ac" in raw
        or "EUR" in text
        or "EURO" in text
    ):
        return "EUR"
    if (
        "\u00a3" in raw
        or "\u00c2\u00a3" in raw
        or "\u0392\u00a3" in raw
        or "GBP" in text
        or "POUND" in text
        or "STERLING" in text
    ):
        return "GBP"
    return ""


def _normalize_currency_value(value):
    return _currency_from_money_text(value)


def _currency_source(value):
    return "statement row symbol" if _currency_from_money_text(value) else ""


def _currency_from_row_values(row, columns):
    for col in columns:
        if col is None:
            continue
        currency = _currency_from_money_text(row.get(col, ""))
        if currency:
            return currency
    return ""


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
    section_currency = ""
    in_account_transactions = False

    def add_current():
        nonlocal current
        if not current or not current["amounts"]:
            current = None
            return

        token_text, amount = current["amounts"][0]
        currency = _currency_from_money_text(token_text) or current.get("currency", "")
        currency_source = _currency_source(token_text) or "statement account section"
        description = current["description"].strip()
        if description and amount != 0:
            rows.append([current["date"], description, amount, currency, currency_source])
        current = None

    for line in lines:
        if not line:
            continue

        section_match = re.search(r"Personal Account\s*\((EUR|USD|GBP)\)", line, re.IGNORECASE)
        if section_match:
            add_current()
            section_currency = section_match.group(1).upper()
            in_account_transactions = False
            continue

        lower_line = line.lower()
        if lower_line.startswith("account transactions from ") or lower_line == "transaction statement":
            add_current()
            in_account_transactions = True
            continue

        if lower_line.startswith(("pending from ", "reverted from ")):
            add_current()
            in_account_transactions = False
            continue

        date_match = MONTH_DATE_RE.match(line)
        if date_match:
            if not in_account_transactions:
                continue
            add_current()

            date_value = _parse_pdf_date(date_match.group("date"))
            rest = date_match.group("rest")
            amounts = _money_values_with_spans(rest)
            description = rest
            money_values = []
            if amounts:
                first_span, token_text, amount = amounts[0]
                description = rest[: first_span[0]].strip()
                money_values.append((token_text, amount))

            current = {
                "date": date_value,
                "description": description,
                "amounts": money_values,
                "currency": section_currency,
            }
            continue

        if current:
            if line.lower().startswith("total "):
                add_current()
                continue

            amounts = _money_values_with_spans(line)
            if amounts:
                first_span, token_text, amount = amounts[0]
                prefix = line[: first_span[0]].strip()
                if prefix:
                    current["description"] = _append_detail(current["description"], prefix)
                current["amounts"].append((token_text, amount))
                continue

            current["description"] = _append_detail(current["description"], line)

    add_current()

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
                description = match.group("rest")[: amounts[-1][0].start()].strip()
                description = re.sub(r"^\d{1,2}/\d{1,2}(?:/\d{2,4})?\s+", "", description).strip()
                if not description or _is_pdf_noise(description):
                    current = None
                    continue
                upper_desc = description.upper()
                credit_hint = any(token in upper_desc for token in [
                    "PAYMENT",
                    "CREDIT",
                    "REFUND",
                    "REVERSAL",
                    "ADJUSTMENT",
                    "THANK YOU",
                ])
                amount = abs(_parse_amount(amount_text)) if credit_hint else -abs(_parse_amount(amount_text))
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
    section_sign = None
    line_re = re.compile(
        r"(?P<date>(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s*\d{1,2})\s+"
        rf"(?P<amount>{MONEY_TOKEN})\s+"
        rf"(?P<desc>.*?)(?=\s+(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s*\d{{1,2}}\s+{MONEY_TOKEN}\s+|$)",
        re.IGNORECASE,
    )
    date_first_re = re.compile(
        r"^(?P<date>(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s*\d{1,2}|\d{1,2}/\d{1,2})(?P<rest>\s+.+)$",
        re.IGNORECASE,
    )

    for raw_line in text.splitlines():
        line = re.sub(r"\s+", " ", raw_line).strip()
        if not line:
            continue
        compact = re.sub(r"[^a-z]", "", line.lower())
        if any(token in compact for token in ["withdrawal", "debit", "checks"]):
            section_sign = -1
        elif any(token in compact for token in ["deposit", "credit"]):
            section_sign = 1

        found_line_row = False
        for match in line_re.finditer(line):
            description = match.group("desc").strip()
            if not description or _is_pdf_noise(description):
                continue
            amount = _parse_amount(match.group("amount"))
            if section_sign == -1 and amount > 0:
                amount = -abs(amount)
            elif section_sign == 1 and amount < 0:
                amount = abs(amount)
            rows.append([
                _parse_any_date(match.group("date"), default_year),
                description,
                amount,
            ])
            found_line_row = True
        if found_line_row:
            continue

        date_match = date_first_re.match(line)
        if not date_match:
            continue
        rest = date_match.group("rest").strip()
        amounts = _money_values_with_spans(rest)
        if not amounts:
            continue
        (start, end), _, amount = amounts[0]
        description = re.sub(r"\s+", " ", f"{rest[:start]} {rest[end:]}").strip()
        while True:
            trailing_amounts = _money_values_with_spans(description)
            if not trailing_amounts or trailing_amounts[-1][0][1] != len(description.rstrip()):
                break
            description = description[: trailing_amounts[-1][0][0]].strip()
        if not description or _is_pdf_noise(description):
            continue
        if section_sign == -1 and amount > 0:
            amount = -abs(amount)
        elif section_sign == 1 and amount < 0:
            amount = abs(amount)
        rows.append([
            _parse_any_date(date_match.group("date"), default_year),
            description,
            amount,
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
            amount_matches = _money_values_with_spans(rest)
            amount_match = amount_matches[-1] if amount_matches else None
            if amount_match and amount_match[0][1] == len(rest.rstrip()):
                if current:
                    rows.append(current)
                (start, end), amount_text, _ = amount_match
                description = rest[:start].strip()
                current = [date_match.group("date"), description, amount_text]
                continue

        if current:
            current[1] = _append_detail(current[1], line)

    if current:
        rows.append(current)
    return rows


def _bank_of_cyprus_transaction_amount(description, fallback_amount):
    amounts = _money_values(description)
    if not amounts:
        return fallback_amount

    amount = abs(amounts[0][1])
    upper = re.sub(r"\s+", " ", str(description or "").upper())
    incoming_tokens = [
        "INWARD",
        "TRANSFER-INTERNET-CREDIT",
        "CREDIT TRANSFER",
        "CREDIT",
        "CREDIT ADVICE",
        "DEPOSIT",
        "REFUND",
        "REVERSAL",
        "TIPS IN",
    ]
    outgoing_tokens = [
        "OUTWARD",
        "TRANSFER-INTERNET-DEBIT",
        "TIPS OUT",
        "CASH WITHDRAWAL",
        "ATM",
        "CARD",
        "FEES",
        "FEE",
        "MAINTENANCE",
        "PURCHASE",
        "DEBIT",
    ]

    if any(token in upper for token in incoming_tokens) and not any(token in upper for token in outgoing_tokens):
        return amount
    return -amount


def _parse_bank_of_cyprus_pdf_text(text):
    rows = _parse_generic_pdf_text(text)
    corrected = []
    for date_value, description, amount in rows:
        corrected.append([
            date_value,
            description,
            _bank_of_cyprus_transaction_amount(description, amount),
        ])
    return corrected


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


def _extract_currency(text):
    raw_head = text[:2000]
    head = raw_head.upper()
    if (
        "EUR STATEMENT" in head
        or "EUR" in head
        or "\u20ac" in raw_head
        or "\u00e2\u201a\u00ac" in raw_head
        or "\u03b2\u201a\u00ac" in raw_head
    ):
        return "EUR"
    if "$" in raw_head or "USD" in head:
        return "USD"
    if "\u00a3" in raw_head or "\u00c2\u00a3" in raw_head or "\u0392\u00a3" in raw_head or "GBP" in head:
        return "GBP"
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
            _find_first_amount(flat, [r"Total\s*Electronic\s*Deposits\s*:?\s*(-?\$?\d[\d,]*\.\d{2})"]),
            _find_first_amount(flat, [r"TotalElectronicDeposits\s*:?\s*(-?\$?\d[\d,]*\.\d{2})"]),
            _find_first_amount(flat, [r"Paper deposits\s+(-?\$?\d[\d,]*\.\d{2})"]),
            _find_first_amount(flat, [r"Paper deposits\s+\d+\s+(-?\$?\d[\d,]*\.\d{2})"]),
            _find_first_amount(flat, [r"Paper\s*deposits\s+(-?\$?\d[\d,]*\.\d{2})"]),
            _find_first_amount(flat, [r"Total\s*Paper\s*Deposits\s*:?\s*(-?\$?\d[\d,]*\.\d{2})"]),
            _find_first_amount(flat, [r"TotalPaperDeposits\s*:?\s*(-?\$?\d[\d,]*\.\d{2})"]),
            _find_first_amount(flat, [r"Total\s*Other\s*Deposits\s*:?\s*(-?\$?\d[\d,]*\.\d{2})"]),
            _find_first_amount(flat, [r"TotalOtherDeposits\s*:?\s*(-?\$?\d[\d,]*\.\d{2})"]),
            _find_first_amount(flat, [r"\bInterest\s+(-?\$?\d[\d,]*\.\d{2})"]),
        ]:
            if value is not None and all(abs(value - existing) > 0.005 for existing in deposits):
                deposits.append(value)
        if deposits:
            money_in = round(sum(deposits), 2)

        withdrawals = []
        for value in [
            _find_first_amount(flat, [r"ATM/Debit\s*Card\s*withdrawals\s*:?\s*(-?\$?\d[\d,]*\.\d{2})"]),
            _find_first_amount(flat, [r"Total\s*ATM/Debit\s*Card\s*Withdrawals\s*:?\s*(-?\$?\d[\d,]*\.\d{2})"]),
            _find_first_amount(flat, [r"ATM/DebitCardWithdrawals\s*:?\s*(-?\$?\d[\d,]*\.\d{2})"]),
            _find_first_amount(flat, [r"TotalATM/DebitCardWithdrawals\s*:?\s*(-?\$?\d[\d,]*\.\d{2})"]),
            _find_first_amount(flat, [r"Electronic \(EFT\) withdrawals\s+(-?\$?\d[\d,]*\.\d{2})"]),
            _find_first_amount(flat, [r"Electronic \(EFT\) withdrawals\s+\d+\s+(-?\$?\d[\d,]*\.\d{2})"]),
            _find_first_amount(flat, [r"Electronic\s*\(EFT\)\s*withdrawals\s+(-?\$?\d[\d,]*\.\d{2})"]),
            _find_first_amount(flat, [r"Total\s*Electronic\s*Withdrawals\s*:?\s*(-?\$?\d[\d,]*\.\d{2})"]),
            _find_first_amount(flat, [r"TotalElectronicWithdrawals\s*:?\s*(-?\$?\d[\d,]*\.\d{2})"]),
        ]:
            if value is None:
                continue
            value = -abs(value)
            if all(abs(value - existing) > 0.005 for existing in withdrawals):
                withdrawals.append(value)
        if withdrawals:
            money_out = round(sum(withdrawals), 2)

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
    columns = {_norm_col(c): c for c in df.columns}
    card_member_col = columns.get("card member")
    source_account_col = columns.get("account #") or columns.get("account number")
    currency_col = next((columns.get(name) for name in CURRENCY_NAMES if columns.get(name)), None)
    generated_amount_from_debit_credit = False

    if amount_col is None and debit_col and credit_col:
        debit = df[debit_col].apply(_parse_amount).abs()
        credit = df[credit_col].apply(_parse_amount).abs()
        df["Amount"] = credit - debit
        amount_col = "Amount"
        generated_amount_from_debit_credit = True

    if not date_col or not desc_cols or not amount_col:
        raise ValueError(
            "Could not detect Date, Description, and Amount columns. "
            "Please check the statement format before import."
        )

    out = pd.DataFrame()
    date_parser = _parse_us_tabular_date if card_member_col and source_account_col else _parse_tabular_date
    out["Date"] = df[date_col].apply(date_parser)
    out["Description"] = df.apply(lambda row: _combine_description(row, desc_cols), axis=1)
    out["Amount"] = df[amount_col].apply(_parse_amount)
    amount_currency_cols = [debit_col, credit_col] if generated_amount_from_debit_credit else [amount_col]
    explicit_currency = (
        df[currency_col].apply(_normalize_currency_value)
        if currency_col
        else pd.Series([""] * len(df), index=df.index)
    )
    symbol_currency = df.apply(lambda row: _currency_from_row_values(row, amount_currency_cols), axis=1)
    statement_currency = symbol_currency.where(symbol_currency.astype(str).str.strip() != "", explicit_currency)
    if statement_currency.fillna("").astype(str).str.strip().ne("").any():
        out["statement_currency"] = statement_currency.fillna("").astype(str).str.upper()
        source_values = [
            "statement row symbol" if str(symbol).strip() else "explicit currency column"
            for symbol in symbol_currency.fillna("").astype(str)
        ]
        out["statement_currency_source"] = source_values
    if card_member_col and source_account_col:
        out["Amount"] = -out["Amount"]
    if card_member_col:
        out["card_member"] = df[card_member_col].fillna("").astype(str).str.strip()
    if source_account_col:
        out["source_account_number"] = df[source_account_col].fillna("").astype(str).str.strip()
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
    if rows and len(rows[0]) >= 5:
        df = pd.DataFrame(rows, columns=["Date", "Description", "Amount", "statement_currency", "statement_currency_source"])
    elif rows and len(rows[0]) >= 4:
        df = pd.DataFrame(rows, columns=["Date", "Description", "Amount", "statement_currency"])
    else:
        df = pd.DataFrame(rows, columns=["Date", "Description", "Amount"])
    amount_currency = df["Amount"].apply(_currency_from_money_text)
    if "statement_currency" in df.columns:
        existing_currency = df["statement_currency"].fillna("").astype(str).str.upper()
        df["statement_currency"] = existing_currency.where(existing_currency.str.strip() != "", amount_currency)
    elif amount_currency.fillna("").astype(str).str.strip().ne("").any():
        df["statement_currency"] = amount_currency.fillna("").astype(str).str.upper()
    if "statement_currency" in df.columns and "statement_currency_source" not in df.columns:
        amount_source = df["Amount"].apply(_currency_source)
        df["statement_currency_source"] = amount_source.where(
            amount_source.astype(str).str.strip() != "",
            "statement/header fallback",
        )
    df["Date"] = df["Date"].apply(_parse_pdf_date)
    df["Amount"] = df["Amount"].apply(_parse_amount)
    df["Description"] = df["Description"].fillna("").astype(str)
    if "statement_currency" in df.columns:
        df["statement_currency"] = df["statement_currency"].fillna("").astype(str).str.upper()
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
                text_upper = text.upper()
                text_compact = re.sub(r"[^A-Z0-9]", "", text_upper)
                if "Revolut Bank" in text or "Account transactions from" in text:
                    rows = _parse_revolut_pdf_text(text)
                elif "Bank of Cyprus" in text or "BankOfCyprus" in text or "BCYPCY2N" in text:
                    rows = _parse_bank_of_cyprus_pdf_text(text)
                elif (
                    "COMERICA" in text_upper
                    or "COMMERCIALCHECKING" in text_compact
                    or "BUSINESSMONEYMARKETACCOUNT" in text_compact
                ):
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
