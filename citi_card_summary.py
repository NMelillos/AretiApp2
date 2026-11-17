"""Normal Citi card summaries in the existing cardholder convention."""
from datetime import datetime, timedelta
from decimal import Decimal as D
import re
from citi_refund import CitiSourceError

SOURCE = 'Citi validated cardholder summary'


def require(ok, reason):
    if not ok:
        raise CitiSourceError('Citi card validation failed: '+reason+'; no rows imported.')


def verified(text, rows):
    if not ('CITI' in text.upper() and 'AADVANTAGE' in text.upper() and 'Account Summary' in text):
        return None
    # Existing special contracts remain authoritative and are dispatched first.
    if any(marker in text.upper() for marker in ('STOP PAY REAPPLY CK#','CREDIT REFUND AS REQUESTED')):
        return None
    numbers = set(re.findall(r'Account number ending in:\s*(\d{4})', text, re.I))
    periods = set(re.findall(r'Billing Period:\s*(\d{2}/\d{2}/\d{2})\s*-\s*(\d{2}/\d{2}/\d{2})', text))
    require(len(numbers) == len(periods) == 1, 'account/period')
    start, end = [datetime.strptime(v,'%m/%d/%y').date().isoformat() for v in next(iter(periods))]
    require(start <= end, 'period order')
    first = re.split(r'Page\s+2\s+of\s+\d+', text, maxsplit=1)[0]
    values = {}
    for key in ('Previous balance','Payments','Credits','Purchases','Cash advances','Fees','Interest','New balance'):
        found = re.findall(re.escape(key)+r'\s+([+-]?)\$(\d[\d,]*\.\d{2})', first)
        require(len(found) == 1, 'summary '+key)
        sign, amount = found[0]
        values[key] = D(amount.replace(',','')) * (-1 if sign == '-' else 1)
    charges = sum((values[k] for k in ('Purchases','Cash advances','Fees','Interest')), D(0))
    credits = -(values['Payments']+values['Credits'])
    require(all(values[k]>=0 for k in ('Purchases','Cash advances','Fees','Interest'))
            and values['Payments']<=0 and values['Credits']<=0, 'issuer activity signs')
    require(values['Previous balance']+charges-credits == values['New balance'], 'issuer reconciliation')
    movements = [(str(r[0]), D(str(r[2]))) for r in rows]
    require(all(start <= d <= end and a.is_finite() for d,a in movements), 'movement dates/amounts')
    require(sum((a for _,a in movements if a>0),D(0))==credits
            and -sum((a for _,a in movements if a<0),D(0))==charges, 'completed movement totals')
    opening, closing = -values['Previous balance'], -values['New balance']
    require(opening+credits-charges == closing, 'cardholder reconciliation')
    return rows, dict(bank='Citi',account_number=next(iter(numbers)),currency='USD',
        period_start=start,period_end=end,opening_balance=opening,money_in=abs(credits),
        money_out=charges,closing_balance=closing,source=SOURCE,
        structural_validation=True,source_movements=movements)


def validate(frame, balance, account, conn):
    from citi_refund import validate_adjustment_preview
    validate_adjustment_preview(frame, balance, account)
    previous = (datetime.fromisoformat(balance['period_start']).date()-timedelta(days=1)).isoformat()
    cur = conn.cursor()
    try:
        cur.execute('SELECT account_name,bank,account_number,currency FROM account_list WHERE id=?',(account.get('id'),))
        current = cur.fetchone()
        require(current is not None and tuple(current) == tuple(account.get(k) for k in
                ('account_name','bank','account_number','currency')), 'Setup identity changed since preview')
        cur.execute('SELECT closing_balance FROM statement_balances WHERE bank=? AND account_name=? '
                    'AND account_number=? AND currency=? AND period_end=?',
                    (account['bank'],account['account_name'],account['account_number'],'USD',previous))
        prior = cur.fetchall()
        require(all(row[0] is not None and D(str(row[0]))==balance['opening_balance'] for row in prior),
                'adjacent historical closing conflicts with source opening')
    finally:
        cur.close()
