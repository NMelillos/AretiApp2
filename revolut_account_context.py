"""Source-bound account context for completed English Revolut transactions.

An unconfigured currency is resolved only for this validated statement. Setup
and its legitimate other-currency history are never changed.
"""
from datetime import datetime
from decimal import Decimal
import re


def recognized(text):
    return ('Revolut Bank UAB' in text
            and re.search(r'(?m)^(EUR|USD|GBP) Statement\s*$', text) is not None
            and 'Account (Current Account)' in text
            and 'Account transactions from ' in text)


def _fail():
    from parsing import RevolutBusinessParseError
    raise RevolutBusinessParseError('Revolut completed transactions differ from validated source/account.')


def _compact(value):
    return re.sub(r'[^\w]', '', str(value or '')).upper()


def source_context(pages, frame):
    text = '\n'.join(pages)
    if not recognized(text):
        _fail()
    titles = re.findall(r'(?m)^(EUR|USD|GBP) Statement\s*$', text)
    if len(titles) != len(pages) or len(set(titles)) != 1:
        _fail()
    for i, page in enumerate(pages, 1):
        if 'Revolut Bank UAB' not in page or not re.search(rf'Page {i} of {len(pages)}\s*$', page):
            _fail()
    currency = titles[0]
    symbol = {'EUR': '€', 'USD': '$', 'GBP': '£'}[currency]
    header = pages[0].split('Balance summary')[0]
    numbers = re.findall(r'\bIBAN\s+(LT\d{18})\b', header)
    if len(numbers) != 1:
        _fail()
    number = numbers[0]
    if int(number[4:]+'2129'+number[2:4]) % 97 != 1:
        _fail()
    owner = re.search(r'(?m)^Revolut Bank UAB\s*\n([^\n]+)\n', header)
    if owner is None or not owner[1].strip():
        _fail()
    periods = re.findall(r'(?m)^Account transactions from ([A-Za-z]+ \d{1,2}, \d{4}) to ([A-Za-z]+ \d{1,2}, \d{4})\s*$', text)
    if len(periods) != 1:
        _fail()
    try:
        start, end = [datetime.strptime(d, '%B %d, %Y').date().isoformat() for d in periods[0]]
    except ValueError:
        _fail()
    if start > end:
        _fail()
    money = re.compile(re.escape(symbol)+r'(-?(?:\d{1,3}(?:,\d{3})+|\d+)\.\d{2})')
    summaries = re.findall(r'(?m)^Account \(Current Account\) (.+)\s*$', text)
    totals = re.findall(r'(?m)^Total (.+)\s*$', text)
    if len(summaries) != 1 or len(totals) != 1:
        _fail()
    values = [Decimal(m.replace(',', '')) for m in money.findall(summaries[0])]
    if len(values) != 4 or money.sub('', summaries[0]).strip():
        _fail()
    if [Decimal(m.replace(',', '')) for m in money.findall(totals[0])] != values or money.sub('', totals[0]).strip():
        _fail()
    opening, debits, credits, closing = values
    if debits < 0 or credits < 0 or opening+credits-debits != closing:
        _fail()
    movements = []
    active = False
    previous = opening
    for page in pages:
        for line in page.splitlines():
            if line.startswith('Account transactions from '):
                active = True
                continue
            if line.lower().startswith(('pending from ', 'reverted from ')):
                active = False
                continue
            row = re.match(r'^([A-Za-z]{3} \d{1,2}, \d{4})\s+(.+)$', line)
            if row is None or not active:
                continue
            try:
                when = datetime.strptime(row[1], '%b %d, %Y').date().isoformat()
            except ValueError:
                _fail()
            tokens = list(money.finditer(row[2]))
            if len(tokens) != 2 or not start <= when <= end:
                _fail()
            magnitude, running = [Decimal(t[1].replace(',', '')) for t in tokens]
            delta = running-previous
            if magnitude <= 0 or abs(delta) != magnitude:
                _fail()
            movements.append((when, delta))
            previous = running
    if not movements or previous != closing:
        _fail()
    if sum((a for _, a in movements if a > 0), Decimal(0)) != credits or -sum((a for _, a in movements if a < 0), Decimal(0)) != debits:
        _fail()
    actual = [(str(r['Date']), Decimal(str(r['Amount']))) for r in frame.to_dict('records')]
    if actual != movements or not frame.statement_currency.eq(currency).all():
        _fail()
    return dict(account_number=number, account_name=owner[1].strip(), currency=currency,
                period_start=start, period_end=end, opening_balance=opening,
                money_out=debits, money_in=credits, closing_balance=closing,
                movements=[(str(r['Date']), str(r['Description']), Decimal(str(r['Amount']))) for r in frame.to_dict('records')])


def effective_account(frame, balance, account):
    evidence = frame.attrs.get('revolut_source_account')
    if not evidence:
        _fail()
    account = dict(account or {})
    currency = str(account.get('currency') or '').strip().upper()
    if (_compact(account.get('bank')) not in ('REVOLUT', 'REVOLUTBANK', 'REVOLUTBANKUAB')
            or _compact(account.get('account_number')) != evidence['account_number']
            or _compact(account.get('account_name')) != _compact(evidence['account_name'])
            or (currency and currency != evidence['currency'])):
        _fail()
    for key in ('account_number', 'currency', 'period_start', 'period_end'):
        actual = balance.get(key)
        if (_compact(actual) if key == 'account_number' else actual) != evidence[key]:
            _fail()
    for key in ('opening_balance', 'money_in', 'money_out', 'closing_balance'):
        if balance.get(key) is None or Decimal(str(balance[key])) != evidence[key]:
            _fail()
    records = frame.to_dict('records')
    if [(str(r['Date']), str(r['Description']), Decimal(str(r['Amount']))) for r in records] != evidence['movements']:
        _fail()
    for row in records:
        if str(row.get('statement_currency') or '') != evidence['currency']:
            _fail()
        if 'account_number' in row and _compact(row['account_number']) != evidence['account_number']:
            _fail()
        if 'currency' in row and str(row['currency']) != evidence['currency']:
            _fail()
    account['currency'] = evidence['currency']
    return account
