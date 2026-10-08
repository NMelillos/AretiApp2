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
    # Zero-debit statements omit both debit breakdown and transaction sections.
    zero_debits = re.findall(rf"^Debits - \$({money})$", text, re.MULTILINE)
    if zero_debits:
        if len(zero_debits) != 1 or amount(zero_debits[0]) != 0:
            fail()
        debits = Decimal(0)
    else:
        debits = amount(unique(rf"^Total debits - \$({money})$", r"^Total debits\b"))
    labels = {
        "DEPOSITS": (r"Credits Deposits", r"\+", 1),
        "ELECTRONIC CREDITS": (r"Electronic cr", r"\+", 1),
        "CHECKS PAID": (r"Debits Checks paid", "-", -1),
        "ELECTRONIC DEBITS": (r"Electronic db", "-", -1),
    }
    expected = {}
    for section, (label, sign, direction) in labels.items():
        found = re.findall(rf"{label}\s*\((\d+)\) {sign} ({money})$", text, re.MULTILINE)
        if not found and zero_debits and direction == -1:
            expected[section] = (0, Decimal(0))
        elif len(found) != 1:
            fail()
        else:
            count, total = found[0]
            expected[section] = (int(count), amount(total))
            if (int(count) == 0) != (amount(total) == 0):
                fail()
    for label, sign in ((r"Other credits", r"\+"),(r"Other debits", "-")):
        found = re.findall(rf"{label}\s*\((\d+)\) {sign} ({money})$", text, re.MULTILINE)
        if not found and zero_debits and label == 'Other debits':
            continue
        if len(found) != 1 or int(found[0][0]) != 0 or amount(found[0][1]) != 0:
            fail()
    headers = {'DEPOSITS':'Date Description Reference Credits',
               'ELECTRONIC CREDITS':'Date Description Credits',
               'ELECTRONIC DEBITS':'Date Description Debits'}
    rows, state, seen, header = [], None, [], False
    counts = {key:0 for key in labels}
    sums = {key:Decimal(0) for key in labels}
    for line in lines:
        if line in labels or line == 'DAILY BALANCES':
            if state and not header:
                fail()
            if line in seen:
                fail()
            seen.append(line)
            state = None if line == 'DAILY BALANCES' else line
            header = False
            continue
        if state is None:
            if re.match(r"\d{1,2}-\d{1,2}\b", line):
                daily_money = rf"(?:{money}|\.\d{{1,2}})"
                if not seen or seen[-1] != 'DAILY BALANCES' or not re.fullmatch(rf"(?:\d{{1,2}}-\d{{1,2}} {daily_money})(?: \d{{1,2}}-\d{{1,2}} {daily_money})*",line):
                    fail()
            continue
        if (line == headers.get(state) or (state == 'CHECKS PAID' and re.fullmatch(r"Number Date Amount(?: Number Date Amount)*",line))):
            if header:
                fail()
            header = True
            continue
        if not header:
            fail()
        if state == 'CHECKS PAID':
            # Number/date/amount triples are explicit check activity, not daily balances.
            matches = re.findall(rf"(\d+) (\d{{1,2}})-(\d{{1,2}}) ({money})", line)
            if not matches or ' '.join(f'{n} {m}-{d} {v}' for n,m,d,v in matches) != line:
                fail()
            movements = [(m,d,'Check '+n,v) for n,m,d,v in matches]
        else:
            match = re.fullmatch(rf"(\d{{1,2}})-(\d{{1,2}}) (.+) ({money})",line)
            if not match:
                fail()
            movements = [match.groups()]
        for month, day, description, value in movements:
            when = datetime(end.year,int(month),int(day)).date()
            if not start <= when <= end or re.search(r"[+-]\s*$",description):
                fail()
            if state == 'DEPOSITS' and not re.search(r"\s\d{8}$",description):
                fail()
            magnitude = amount(value)
            if magnitude <= 0:
                fail()
            direction = labels[state][2]
            rows.append([when.isoformat(),description,str(magnitude*direction),'USD','CNB statement'])
            counts[state] += 1
            sums[state] += magnitude
    present = [key for key in labels if expected[key][0] > 0]
    if seen != present + ['DAILY BALANCES']:
        fail()
    if any((counts[key],sums[key]) != expected[key] for key in labels):
        fail()
    if (sum((sums[key] for key in labels if labels[key][2] == 1),Decimal(0)) != credits
            or sum((sums[key] for key in labels if labels[key][2] == -1),Decimal(0)) != debits
            or amount(opening) + credits - debits != amount(closing)):
        fail()
    result = _frame_from_pdf_rows(rows)
    result["source_account_number"] = number
    result.attrs["cnb_account"] = number
    result.attrs["cnb_alias"] = alias
    result.attrs['statement_balance'] = dict(
        bank='CNB', account_number=number, currency='USD',
        period_start=start.isoformat(), period_end=end.isoformat(),
        opening_balance=amount(opening), money_in=credits, money_out=debits,
        closing_balance=amount(closing), source='CNB Account Summary',
        transaction_count=len(rows),
        source_movements=[(row[0], Decimal(row[2])) for row in rows],
    )
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


def validate_preview(frame, balance, account):
    """Preserve the Account Summary proof through normal atomic import."""
    number = str(frame.attrs.get('cnb_account', ''))
    allowed = {number, str(frame.attrs.get('cnb_alias', ''))} - {''}
    if (balance.get('source') != 'CNB Account Summary' or balance.get('account_number') != number
            or str(account.get('account_number', '')) not in allowed
            or str(account.get('bank', '')).strip().casefold() not in ('cnb', 'city national bank')
            or account.get('currency') != 'USD' or balance.get('currency') != 'USD'):
        raise CNBParseError('CNB preview account identity is not verified.')
    movements = [(str(row['Date']), Decimal(str(row['Amount']))) for row in frame.to_dict('records')]
    if (movements != balance.get('source_movements') or len(frame) != balance.get('transaction_count')
            or not frame.account_number.eq(account['account_number']).all()
            or not frame.currency.eq('USD').all()):
        raise CNBParseError('CNB preview no longer matches the verified source.')
    incoming = sum((value for _, value in movements if value > 0), Decimal(0))
    outgoing = -sum((value for _, value in movements if value < 0), Decimal(0))
    if (incoming != balance.get('money_in') or outgoing != balance.get('money_out')
            or balance['opening_balance'] + incoming - outgoing != balance['closing_balance']):
        raise CNBParseError('CNB Account Summary does not reconcile; no rows imported.')
