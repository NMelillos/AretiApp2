"""Fail-closed Bank of Cyprus column extraction; confidential sources stay outside Git."""
import re
from datetime import datetime
from decimal import Decimal


class BOCParseError(ValueError):
    pass


def _fail(message):
    raise BOCParseError('Bank of Cyprus statement validation failed: ' + message)


def _date(value):
    try:
        return datetime.strptime(value, '%d/%m/%Y').date().isoformat()
    except ValueError:
        _fail('invalid statement date')


def _money(value):
    if not re.fullmatch(r'[+-]?\d[\d,]*\.\d{2}', value):
        _fail('invalid or ambiguous monetary column')
    return Decimal(value.replace(',', ''))


def metadata(text):
    flat = re.sub(r'\s+', ' ', text)
    def required(pattern):
        matches = re.findall(pattern, flat)
        if len(set(matches)) != 1:
            _fail('missing or conflicting account/period header')
        return matches[0]
    account = required(r'Account Number\s+(\d+)')
    iban = required(r'IBAN\s+(CY\d{26})')
    numeric = iban[4:] + '1234' + iban[2:4]  # C=12, Y=34
    if int(numeric) % 97 != 1:
        _fail('IBAN checksum failed')
    currency = required(r'Currency\s+([A-Z]{3})')
    start, end = required(r'Statement Period:\s*(\d{2}/\d{2}/\d{4})\s*-\s*(\d{2}/\d{2}/\d{4})')
    start, end = _date(start), _date(end)
    if start > end:
        _fail('invalid statement period')
    return dict(bank='Bank of Cyprus', account_number=account, iban=iban,
                currency=currency, period_start=start, period_end=end, source='BOC bank columns')


def parse_pages(pages, texts):
    meta = metadata('\n'.join(texts))
    rows, previous, opening, totals = [], None, None, None
    debit_total = credit_total = Decimal(0)
    for number, (page, text) in enumerate(zip(pages, texts), 1):
        account = re.search(r'Account Number\s+(\d+)', re.sub(r'\s+', ' ', text))
        if not account or account[1] != meta['account_number']:
            _fail('continuation page account mismatch')
        footer = re.search(r'^Page[ \t]+(\d+)[ \t]*/[ \t]*(\d+)[ \t]*$', text, re.MULTILINE)
        if not footer or int(footer[1]) != number or int(footer[2]) != len(pages):
            _fail('missing, truncated or reordered statement pages')
        lines = []
        for word in sorted(page.extract_words(), key=lambda w: (w['top'], w['x0'])):
            if not word.get('upright', True):
                continue
            if not lines or abs(lines[-1][0] - word['top']) > 2:
                lines.append((word['top'], []))
            lines[-1][1].append(word)
        headers = [(y, ws) for y, ws in lines if {'Debit', 'Credit', 'Balance'}.issubset({w['text'] for w in ws})
                   and any('Transaction' in w['text'] for w in ws)]
        if len(headers) != 1:
            _fail('missing or ambiguous transaction column header')
        top, words = headers[0]
        positions = {w['text']: w for w in words}
        debit, credit, balance = [positions[k] for k in ('Debit', 'Credit', 'Balance')]
        financial_left = debit['x0'] - 55
        boundaries = ((debit['x1'] + credit['x1']) / 2, (credit['x1'] + balance['x1']) / 2)
        description_left = next(w['x0'] for w in words if 'Details' in w['text'])
        active, ended = False, False
        for y, ws in lines:
            if y <= top:
                continue
            ws = sorted(ws, key=lambda w: w['x0'])
            line = ' '.join(w['text'] for w in ws)
            compact = re.sub(r'\s+', '', line).lower()
            if compact.startswith('date'):
                continue
            if line.startswith('Continue on next Page'):
                ended = True
                break
            if compact.startswith('frompreviouspage'):
                if previous is None:
                    _fail('continuation without opening page')
                active = True
                continue
            cols = [[], [], []]
            for w in ws:
                if w['x0'] >= financial_left:
                    center = (w['x0'] + w['x1']) / 2
                    cols[0 if center < boundaries[0] else 1 if center < boundaries[1] else 2].append(w['text'])
            def value(i, required=False):
                if not cols[i]:
                    if required:
                        _fail('missing monetary column')
                    return Decimal(0)
                return _money(''.join(cols[i]))
            if compact.startswith('balancebroughtforward'):
                if opening is not None or rows:
                    _fail('duplicate opening balance')
                if cols[0] or cols[1]:
                    _fail('unexpected opening movement')
                opening = previous = value(2, True)
                active = True
                continue
            if compact.startswith('total/balancecarriedforward'):
                if not active or totals is not None:
                    _fail('unexpected closing summary')
                totals = (value(0, True), value(1, True), value(2, True))
                ended = True
                break
            if not active:
                _fail('content before opening balance')
            dates = [w['text'] for w in ws if w['x0'] < description_left - 1]
            if dates:
                if len(dates) != 2 or not all(re.fullmatch(r'\d{2}/\d{2}/\d{4}', d) for d in dates):
                    _fail('unparsed transaction date columns')
                when = _date(dates[0]); _date(dates[1])
                if not meta['period_start'] <= when <= meta['period_end']:
                    _fail('transaction outside statement period')
                if bool(cols[0]) == bool(cols[1]):
                    _fail('missing or ambiguous debit/credit direction')
                deb, cred, running = value(0), value(1), value(2, True)
                if deb < 0 or cred < 0:
                    _fail('negative labelled movement magnitude')
                amount = cred - deb
                if previous + amount != running:
                    _fail('transaction columns disagree with running balance')
                description = ' '.join(w['text'] for w in ws if description_left - 1 <= w['x0'] < financial_left)
                if not description:
                    _fail('missing transaction description')
                rows.append([when, description, amount, meta['currency'], 'BOC Debit/Credit columns'])
                previous = running; debit_total += deb; credit_total += cred
            else:
                if any(cols) or not rows:
                    _fail('unparsed content in transaction table')
                rows[-1][1] += ' | ' + line
        if not ended:
            _fail('missing table ending')
    if totals is None or opening is None or totals != (debit_total, credit_total, previous):
        _fail('statement totals do not reconcile')
    if opening + credit_total - debit_total != previous:
        _fail('opening plus credits minus debits does not equal closing')
    meta.update(opening_balance=opening, closing_balance=previous, money_out=debit_total,
                money_in=credit_total, transaction_count=len(rows),
                structural_validation=True,
                zero_activity_validated=not rows, reconciliation_convention='deposit')
    meta['source_movements'] = [(row[0], row[2]) for row in rows]
    return rows, meta


def validate_preview(frame, balance, account):
    """Recheck this BOC layout's source proof at the atomic persistence boundary."""
    compact = lambda value: re.sub(r'[\s./-]', '', str(value)).upper()
    if not balance.get('structural_validation'):
        _fail('missing source-column validation')
    if compact(account.get('account_number')) not in {
            compact(balance['account_number']), compact(balance['iban'])}:
        _fail('selected Setup account differs from source')
    if str(account.get('currency', '')).upper() != balance['currency']:
        _fail('selected Setup currency differs from source')
    actual = [(str(row['Date']), Decimal(str(row['Amount']))) for row in frame.to_dict('records')]
    if actual != balance['source_movements'] or len(actual) != balance['transaction_count']:
        _fail('preview movements differ from validated source columns')
    if any(compact(row.get('account_number')) != compact(account['account_number'])
           or str(row.get('currency', '')).upper() != balance['currency'] for row in frame.to_dict('records')):
        _fail('preview account/currency differs from source')
    credits = sum((amount for _, amount in actual if amount > 0), Decimal(0))
    debits = -sum((amount for _, amount in actual if amount < 0), Decimal(0))
    if (credits != balance['money_in'] or debits != balance['money_out']
            or balance['opening_balance'] + credits - debits != balance['closing_balance']):
        _fail('preview totals do not reconcile with source')


def parse_document(pages, texts):
    """Validate every original account section before exposing any parsed unit.

    BOC original page labels restart at 1 for each statement. A new account
    header alone cannot turn a missing continuation page into a valid section.
    """
    if not pages or len(pages) != len(texts):
        _fail('missing document pages')
    groups = []
    for index, text in enumerate(texts):
        footer = re.search(r'^Page[ \t]+(\d+)[ \t]*/[ \t]*(\d+)[ \t]*$', text, re.MULTILINE)
        if not footer:
            _fail('missing section page numbering')
        page_number, count = map(int, footer.groups())
        flat = re.sub(r'\s+', ' ', text)
        account = re.search(r'Account Number\s+(\d+)', flat)
        if not account:
            _fail('missing account on section page')
        if page_number == 1:
            if groups and len(groups[-1]['indices']) != groups[-1]['count']:
                _fail('truncated account section')
            groups.append(dict(indices=[], account=account[1], count=count))
        if not groups:
            _fail('document starts with a continuation page')
        group = groups[-1]
        if (group['account'] != account[1] or count != group['count']
                or page_number != len(group['indices']) + 1):
            _fail('account boundary or continuation numbering mismatch')
        group['indices'].append(index)
    if len(groups[-1]['indices']) != groups[-1]['count']:
        _fail('truncated final account section')
    sections, identities = [], set()
    for number, group in enumerate(groups, 1):
        indices = group['indices']
        rows, meta = parse_pages([pages[i] for i in indices], [texts[i] for i in indices])
        identity = (meta['iban'], meta['currency'], meta['period_start'], meta['period_end'])
        if identity in identities:
            _fail('duplicate account statement section')
        identities.add(identity)
        if len(groups) > 1:
            meta.update(source_section=number, source_page_start=indices[0] + 1,
                        source_page_end=indices[-1] + 1)
        sections.append((rows, meta))
    return sections
