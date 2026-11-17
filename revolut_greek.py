"""Strict Greek Revolut wallet parsing. No persistence or currency conversion.

Wallet results deliberately are not the legacy single-account import DataFrame.
"""
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
import re


class GreekRevolutError(ValueError):
    pass


@dataclass(frozen=True)
class Transaction:
    date: date
    description: str
    amount: Decimal
    balance: Decimal
    currency: str


@dataclass(frozen=True)
class Wallet:
    currency: str
    iban: str
    owner: str
    start: date
    end: date
    opening: Decimal
    credits: Decimal
    debits: Decimal
    closing: Decimal
    transactions: tuple
    pending_count: int
    reverted_count: int


MONTHS = dict(zip(('Ιαν', 'Φεβ', 'Μαρ', 'Απρ', 'Μαΐ', 'Ιουν', 'Ιουλ', 'Αυγ', 'Σεπ', 'Οκτ', 'Νοε', 'Δεκ'), range(1, 13)))
MONTHS.update(dict(zip(('Ιανουαρίου', 'Φεβρουαρίου', 'Μαρτίου', 'Απριλίου', 'Μαΐου', 'Ιουνίου', 'Ιουλίου', 'Αυγούστου', 'Σεπτεμβρίου', 'Οκτωβρίου', 'Νοεμβρίου', 'Δεκεμβρίου'), range(1, 13))))
NUMBER = r'-?(?:\d{1,3}(?:\.\d{3})+|\d+),\d{2}'
MONEY = re.compile(r'(' + NUMBER + r')([€$])')
DATE = r'(\d{1,2}) ([^\s]+) (\d{4})'
HEADER = 'Ημερομηνία Περιγραφή Εκροές Εισροές Υπόλοιπο'


def fail(reason):
    raise GreekRevolutError('Greek Revolut validation failed: ' + reason)


def recognized(text):
    return 'Κίνηση λογαριασμού' in text and 'Revolut' in text


def day(d, m, y):
    try:
        return date(int(y), MONTHS[m.rstrip('.')], int(d))
    except (KeyError, ValueError):
        fail('invalid source date')


def amounts(line, symbol, count):
    matches = list(MONEY.finditer(line))
    if len(matches) != count or any(m[2] != symbol for m in matches):
        fail('ambiguous wallet amount/currency')
    return tuple(Decimal(m[1].replace('.', '').replace(',', '.')) for m in matches)


def parse(pdf_pages):
    """Return independently reconciled immutable wallets, never a mixed frame."""
    groups = []
    for page in pdf_pages:
        text = page.extract_text() or ''
        head = re.match(r'^(EUR|USD) Κίνηση λογαριασμού\n', text)
        if not head or not re.search(r'(?m)^Revolut Bank UAB$', text):
            fail('bank/wallet page identity')
        if not groups or groups[-1][0] != head[1]:
            if any(g[0] == head[1] for g in groups):
                fail('non-contiguous wallet pages')
            groups.append((head[1], []))
        groups[-1][1].append((page, text))
    if not groups:
        fail('empty document')
    result = []
    for currency, pages in groups:
        symbol = {'EUR': '€', 'USD': '$'}[currency]
        first = pages[0][1]
        identities = re.findall(r'\bIBAN (LT\d{18})\b', first)
        if len(identities) != 1:
            fail('missing/ambiguous IBAN')
        iban = identities[0]
        if int(iban[4:] + '2129' + iban[2:4]) % 97 != 1:
            fail('invalid IBAN checksum')
        owner = re.search(r'(?m)^Revolut Bank UAB\n([^\n]+)\n', first)
        if not owner or 'IBAN' in owner[1]:
            fail('missing owner')
        owner = owner[1]
        for label in ('Σύνοψη ισολογισμού', 'Αρχικό υπόλοιπο', 'Εκροές', 'Εισροές', 'κλεισίματος'):
            if label not in first:
                fail('missing wallet summary label')
        summaries = re.findall(r'(?m)^Λογαριασμός \(τρεχούμενος λογαριασμός\) (.+)$', first)
        totals = re.findall(r'(?m)^Σύνολο (.+)$', first)
        if len(summaries) != 1 or len(totals) != 1:
            fail('wallet summary count')
        opening, debits, credits, closing = amounts(summaries[0], symbol, 4)
        if amounts(totals[0], symbol, 4) != (opening, debits, credits, closing):
            fail('summary total mismatch')
        periods = re.findall(r'Συναλλαγές λογαριασμού από ' + DATE + ' έως ' + DATE, first)
        if len(periods) != 1:
            fail('completed statement period')
        start, end = day(*periods[0][:3]), day(*periods[0][3:])
        if start > end or min(debits, credits) < 0 or opening + credits - debits != closing:
            fail('summary reconciliation')
        mode = None
        rows = []
        counts = {'pending': 0, 'reverted': 0}
        previous = opening
        for page_index, (page, text) in enumerate(pages, 1):
            numbers = re.findall(r'Σελίδα (\d+) από (\d+)', text)
            if numbers != [(str(page_index), str(len(pages)))]:
                fail('wallet page sequence')
            if page_index > 1 and ('Σύνοψη ισολογισμού' in text or 'IBAN' in text):
                fail('unexpected account/summary on continuation page')
            words = page.extract_words()
            columns = None
            in_table = False
            # Only a complete transaction-table header establishes column geometry.
            for w in words:
                if w['text'] == 'Ημερομηνία':
                    header_words = [v for v in words if abs(v['top'] - w['top']) < 2]
                    if ' '.join(v['text'] for v in header_words) == HEADER:
                        columns = {v['text']: v['x0'] for v in header_words}
            for line in text.splitlines():
                if line.startswith('Δήλωση απώλειας'):
                    break
                section = next((m for prefix, m in (('Σε εκκρεμότητα από ', 'pending'), ('Αντιστράφηκε από ', 'reverted'), ('Συναλλαγές λογαριασμού από ', 'completed')) if line.startswith(prefix)), None)
                if section:
                    period = re.search(DATE + r' (?:έως|προς) ' + DATE, line)
                    if not period or (day(*period.groups()[:3]), day(*period.groups()[3:])) != (start, end):
                        fail('section period mismatch')
                    mode = section
                    in_table = False
                    continue
                if line == HEADER:
                    if mode != 'completed' or columns is None:
                        fail('unexpected completed table')
                    in_table = True
                    continue
                match = re.match('^' + DATE + r' (.+)$', line)
                if match:
                    when = day(*match.groups()[:3])
                    if not start <= when <= end or mode is None:
                        fail('row date/section')
                    if mode != 'completed':
                        counts[mode] += 1
                        continue
                    if columns is None:
                        fail('missing completed columns')
                    movement, balance = amounts(match[4], symbol, 2)
                    delta = balance - previous
                    if movement <= 0 or abs(delta) != movement:
                        fail('running balance reconciliation')
                    # Match source row geometry, independently checking debit/credit sign.
                    candidates = []
                    for w in words:
                        if abs(w['x0'] - columns['Ημερομηνία']) < 2 and w['text'] == match[1]:
                            same = [v for v in words if abs(v['top'] - w['top']) < 2]
                            if ' '.join(v['text'] for v in same) == line:
                                candidates.append(same)
                    if len(candidates) != 1:
                        fail('ambiguous transaction geometry')
                    cells = [v for v in candidates[0] if MONEY.fullmatch(v['text'])]
                    expected_x = columns['Εισροές' if delta > 0 else 'Εκροές']
                    # Amount columns start at their heading; balance cells are
                    # right-aligned and widen leftward as the balance grows.
                    balance_boundary = (columns['Εισροές'] + columns['Υπόλοιπο']) / 2
                    if len(cells) != 2 or abs(cells[0]['x0'] - expected_x) > 2 or cells[1]['x0'] < balance_boundary or cells[1]['x1'] > page.width:
                        fail('activity column/sign mismatch')
                    description = match[4][:MONEY.search(match[4]).start()].strip()
                    if not description or (rows and when < rows[-1].date):
                        fail('description/date order')
                    rows.append(Transaction(when, description, delta, balance, currency))
                    previous = balance
                elif mode == 'completed' and re.match(r'^\d{1,2}\s', line):
                    fail('unparsed transaction date')
                elif mode == 'completed' and in_table:
                    if not rows:
                        fail('orphan transaction detail')
                    # Preserve references, wrapped descriptions and printed FX
                    # detail verbatim as text; never use them for native amounts.
                    r = rows[-1]
                    rows[-1] = Transaction(r.date, r.description + '\n' + line, r.amount, r.balance, r.currency)
        if not rows or previous != closing or sum((r.amount for r in rows if r.amount > 0), Decimal(0)) != credits or -sum((r.amount for r in rows if r.amount < 0), Decimal(0)) != debits:
            fail('completed wallet totals')
        result.append(Wallet(currency, iban, owner, start, end, opening, credits, debits, closing, tuple(rows), counts['pending'], counts['reverted']))
    if len({(w.iban, w.owner, w.start, w.end) for w in result}) != 1:
        fail('wallet account/period inconsistency')
    return tuple(result)
