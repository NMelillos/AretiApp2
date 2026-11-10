"""Citi refund-cheque direction proved by the labelled liability summary."""
from datetime import datetime
from decimal import Decimal
import re


class CitiSourceError(ValueError):
    pass


def verified_refund(text, rows):
    """Return None for unrelated cards/layouts; never use refund words alone."""
    if ('CITI' in text.upper() and 'AADVANTAGE' in text.upper()
            and 'STOP PAY REAPPLY CK#' in text.upper()):
        return _verified_credit_adjustment(text, rows)
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


def _verified_credit_adjustment(text, rows):
    """Source-proven credit reversal, distinct from an issued refund cheque."""
    def fail():
        raise CitiSourceError('Citi credit adjustment does not reconcile; no rows imported.')
    numbers=set(re.findall(r'Account number ending in:\s*(\d{4})',text,re.I))
    periods=set(re.findall(r'Billing Period:\s*(\d{2}/\d{2}/\d{2})-(\d{2}/\d{2}/\d{2})',text))
    if len(numbers)!=1 or len(periods)!=1 or 'Payments, Credits and Adjustments' not in text:
        fail()
    start,end=[datetime.strptime(v,'%m/%d/%y').date().isoformat() for v in next(iter(periods))]
    if start>end:fail()
    first=re.split(r'Page\s+2\s+of\s+\d+',text,maxsplit=1)[0]
    values={}
    for key in ('Previous balance','Payments','Credits','Purchases','Cash advances','Fees','Interest','New balance'):
        found=re.findall(re.escape(key)+r'\s+([+-]?)\$(\d[\d,]*\.\d{2})',first)
        if len(set(found))!=1:fail()
        sign,value=found[0];values[key]=Decimal(value.replace(',',''))*(-1 if sign=='-' else 1)
    if (values['Credits']>=0 or values['Purchases']<=0 or values['Payments']!=0
            or any(values[k]!=0 for k in ('Cash advances','Fees','Interest'))
            or values['Previous balance']+values['Credits']+values['Purchases']!=values['New balance']):
        fail()
    fixed=[list(row) for row in rows]
    credits=[r for r in fixed if str(r[1]).upper().startswith('STOP PAY REAPPLY CK#')]
    if len(credits)!=1 or abs(Decimal(str(credits[0][2])))!=-values['Credits']:fail()
    # Prove the actual labelled source row, not a free-text refund heuristic.
    source=re.findall(r'^\s*(\d{2}/\d{2})\s+STOP PAY REAPPLY CK#\d+\s+-\$(\d[\d,]*\.\d{2})\s*$',text,re.MULTILINE)
    if len(source)!=1 or Decimal(source[0][1].replace(',',''))!=-values['Credits']:fail()
    if str(credits[0][0])[5:]!=source[0][0][0:2]+'-'+source[0][0][3:5]:fail()
    credits[0][2]=-values['Credits']
    purchases=[r for r in fixed if r is not credits[0]]
    if (any(Decimal(str(r[2]))>=0 for r in purchases)
            or -sum((Decimal(str(r[2])) for r in purchases),Decimal(0))!=values['Purchases']
            or any(not start<=str(r[0])<=end for r in fixed)):
        fail()
    opening,closing=-values['Previous balance'],-values['New balance']
    if opening+sum((Decimal(str(r[2])) for r in fixed),Decimal(0))!=closing:fail()
    return fixed,dict(bank='Citi',account_number=next(iter(numbers)),currency='USD',
        period_start=start,period_end=end,opening_balance=opening,
        money_in=-values['Credits'],money_out=-values['Purchases'],closing_balance=closing,
        source='Citi credit adjustment summary',structural_validation=True,
        source_movements=[(str(r[0]),Decimal(str(r[2]))) for r in fixed])


def validate_adjustment_preview(frame,balance,account):
    """Only this new source contract; existing Citi/refund paths are untouched."""
    compact=lambda value:re.sub(r'[^A-Z0-9]','',str(value or '').upper())
    if (not balance.get('structural_validation') or compact(account.get('bank')) not in ('CITI','CITIBANK','CITIBANKCREDITCARD')
            or account.get('currency')!='USD' or not re.sub(r'\D','',str(account.get('account_number',''))).endswith(balance['account_number'])):
        raise CitiSourceError('Citi source/account identity differs; nothing imported.')
    actual=[(str(r['Date']),Decimal(str(r['Amount']))) for r in frame.to_dict('records')]
    if (actual!=balance['source_movements'] or balance['opening_balance']+sum((v for _,v in actual),Decimal(0))!=balance['closing_balance']
            or any(compact(r.get('account_number'))!=compact(account['account_number']) or r.get('currency')!='USD' for r in frame.to_dict('records'))):
        raise CitiSourceError('Citi preview differs from validated movements; nothing imported.')
