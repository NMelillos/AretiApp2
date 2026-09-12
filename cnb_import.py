"""Strict City National Bank statement adapter; no database writes."""
from datetime import datetime, timedelta
from decimal import Decimal
import re


class CNBParseError(ValueError):
    pass


def is_cnb(text):
    return bool(re.search(r"(?m)^City National Bank\s*$", text)
                and "Account Summary Account Activity" in text)


def parse_cnb(pages, metadata):
    from parsing import _frame_from_pdf_rows
    text = "\n".join(pages)
    lines = [re.sub(r"\s+", " ", line).strip() for line in text.splitlines()]
    text = "\n".join(lines)

    def fail():
        raise CNBParseError("CNB statement validation failed; no rows imported.")

    def unique(pattern, label=None):
        values = re.findall(pattern, text, re.MULTILINE)
        if not values or len(set(values)) != 1 or (label and len(re.findall(label, text, re.MULTILINE)) != len(values)):
            fail()
        return values[0]

    number = unique(r"^Account #:\s*(\d+)\s*$", r"^Account #:")
    if unique(r"^Account number (\d+) Beginning balance", r"^Account number\b") != number:
        fail()
    alias = str(metadata.get("Keywords", "")).strip()
    if alias and alias != "0" + number:
        fail()
    end = datetime.strptime(unique(r"^This statement: ([A-Za-z]+ \d{1,2}, \d{4})\b"), "%B %d, %Y").date()
    prior = datetime.strptime(unique(r"^Last statement: ([A-Za-z]+ \d{1,2}, \d{4})\b"), "%B %d, %Y").date()
    start = prior + timedelta(days=1)
    if start > end or (start.year, start.month) != (end.year, end.month):
        fail()
    money = r"(?:\d{1,3}(?:,\d{3})+|\d+)\.\d{2}"

    def amount(value):
        return Decimal(value.replace(",", ""))

    opening_date, opening = unique(rf"Beginning balance \((\d+/\d+/\d+)\) \$({money})$")
    closing_date, closing = unique(rf"^Ending balance \((\d+/\d+/\d+)\) \$({money})$")
    if datetime.strptime(opening_date, "%m/%d/%Y").date() not in (prior, start) or datetime.strptime(closing_date, "%m/%d/%Y").date() != end:
        fail()
    credits = amount(unique(rf"^Total credits \+\$({money})$", r"^Total credits\b"))
    debits = amount(unique(rf"^Total debits - \$({money})$", r"^Total debits\b"))
    deposit_count, deposit_total = unique(rf"Credits Deposits \((\d+)\) \+ ({money})$")
    debit_count, debit_total = unique(rf"^Electronic db \((\d+)\) - ({money})$")
    for label, sign in ((r"Electronic cr", r"\+"), (r"Other credits", r"\+"),
                        (r"Debits Checks paid", "-"), (r"Other debits", "-")):
        count, total = unique(rf"{label}\s*\((\d+)\) {sign} ({money})$")
        if int(count) != 0 or amount(total) != 0:
            fail()
    rows = []
    state = None
    seen = []
    header = False
    counts = {"DEPOSITS": 0, "ELECTRONIC DEBITS": 0}
    sums = {key: Decimal(0) for key in counts}
    for line in lines:
        if line in ("DEPOSITS", "ELECTRONIC DEBITS", "DAILY BALANCES"):
            if state and not header:
                fail()
            seen.append(line)
            state = None if line == "DAILY BALANCES" else line
            header = False
            continue
        if state is None:
            if re.match(r"\d{1,2}-\d{2}\b", line):
                daily_money = rf"(?:{money}|\.\d{{2}})"
                if not seen or seen[-1] != "DAILY BALANCES" or not re.fullmatch(rf"(?:\d{{1,2}}-\d{{2}} {daily_money})(?: \d{{1,2}}-\d{{2}} {daily_money})*", line):
                    fail()
            continue
        expected_header = "Date Description Reference Credits" if state == "DEPOSITS" else "Date Description Debits"
        if line == expected_header:
            if header:
                fail()
            header = True
            continue
        match = re.fullmatch(rf"(\d{{1,2}})-(\d{{2}}) (.+) ({money})", line)
        if not match or not header:
            fail()
        month, day, description, value = match.groups()
        when = datetime(end.year, int(month), int(day)).date()
        if not start <= when <= end or re.search(r"[+-]\s*$", description):
            fail()
        if state == "DEPOSITS" and not re.search(r"\s\d{8}$", description):
            fail()
        magnitude = amount(value)
        if magnitude <= 0:
            fail()
        rows.append([when.isoformat(), description, str(magnitude if state == "DEPOSITS" else -magnitude), "USD", "CNB statement"])
        counts[state] += 1
        sums[state] += magnitude
    if seen != ["DEPOSITS", "ELECTRONIC DEBITS", "DAILY BALANCES"]:
        fail()
    if (counts["DEPOSITS"] != int(deposit_count) or counts["ELECTRONIC DEBITS"] != int(debit_count)
            or sums["DEPOSITS"] != credits or credits != amount(deposit_total)
            or sums["ELECTRONIC DEBITS"] != debits or debits != amount(debit_total)
            or amount(opening) + credits - debits != amount(closing)):
        fail()
    result = _frame_from_pdf_rows(rows)
    result["source_account_number"] = number
    result.attrs["cnb_account"] = number
    result.attrs["cnb_alias"] = alias
    return result


def cnb_account(df, accounts):
    number = df.attrs.get("cnb_account")
    if not number or not df.source_account_number.eq(number).all() or not df.statement_currency.eq("USD").all():
        raise CNBParseError("CNB source account identity is missing or inconsistent.")
    alias = df.attrs.get("cnb_alias", "")
    if alias and alias != "0" + number:
        raise CNBParseError("CNB account alias is invalid.")
    numbers = {number, alias} - {""}
    candidates = accounts[
        accounts.bank.fillna("").str.strip().str.casefold().isin(["city national bank", "cnb"])
        & accounts.currency.fillna("").str.strip().str.upper().eq("USD")
        & accounts.account_number.astype(str).str.strip().isin(numbers)
    ]
    if len(candidates) != 1:
        raise CNBParseError("CNB requires exactly one verified existing USD account.")
    return candidates.iloc[0].to_dict()
