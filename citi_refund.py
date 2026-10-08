"""Citi refund-cheque direction proved by the labelled liability summary."""
from datetime import datetime
from decimal import Decimal
import re


class CitiSourceError(ValueError):
    pass


def verified_refund(text, rows):
    """Return None for unrelated cards/layouts; never use refund words alone."""
    if not ('CITI' in text.upper() and 'AADVANTAGE' in text.upper()
            and 'CREDIT REFUND AS REQUESTED' in text.upper()):
        return None
    def fail():
        raise CitiSourceError('Citi refund statement does not reconcile; no rows imported.')
    number = set(re.findall(r'Account number ending in:\s*(\d{4})', text, re.I))
    period = re.findall(r'Billing Period:\s*(\d{2}/\d{2}/\d{2})-(\d{2}/\d{2}/\d{2})', text)
    if len(number) != 1 or len(set(period)) != 1:
        fail()
    start, end = [datetime.strptime(v, '%m/%d/%y').date().isoformat() for v in period[0]]
    if start > end or 'Payments, Credits and Adjustments' not in text:
        fail()
    values = {}
    money = r'([+-]?)\$(\d[\d,]*\.\d{2})'
    # Page-one labels are authoritative; account messages/rewards cannot become rows.
    first = text.split('Page 2 of 4')[0]
    for key in ('Previous balance','Payments','Credits','Purchases','Cash advances','Fees','Interest','New balance'):
        found = re.findall(re.escape(key)+r'\s+'+money, first)
        if len(set(found)) != 1:
            fail()
        sign, amount = found[0]
        values[key] = Decimal(amount.replace(',', '')) * (-1 if sign == '-' else 1)
    opening, closing = values['Previous balance'], values['New balance']
    increase = sum((values[k] for k in ('Purchases','Cash advances','Fees','Interest')), Decimal(0))
    decrease = -(values['Payments'] + values['Credits'])
    if (decrease != 0 or opening + increase - decrease != closing or not rows
            or values['Purchases'] <= 0 or any(values[k] != 0 for k in ('Cash advances','Fees','Interest'))):
        fail()
    fixed = [list(row) for row in rows]
    refunds = [row for row in fixed if str(row[1]).strip().upper() == 'CREDIT REFUND AS REQUESTED']
    if len(refunds) != 1:
        fail()
    refunds[0][2] = -abs(Decimal(str(refunds[0][2])))
    # Existing application convention: card outflows are negative. Their inverse
    # must equal the source liability increase, including the issued refund cheque.
    if (any(not start <= str(row[0]) <= end for row in fixed)
            or any(Decimal(str(row[2])) >= 0 for row in fixed)
            or -sum((Decimal(str(row[2])) for row in fixed), Decimal(0)) != increase):
        fail()
    return fixed, dict(bank='Citi', account_number=next(iter(number)), currency='USD',
        period_start=start, period_end=end, opening_balance=opening, money_in=increase,
        money_out=decrease, closing_balance=closing, source='Citi Account Summary',
        notes='Liability summary increases are distinct from negative application card outflows.')
