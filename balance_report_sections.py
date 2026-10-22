"""Approved presentation buckets; never resolve FX, select balances, or mutate values."""
import re
from financial_decimal import optional_decimal,exact_sum

def section(row):
    bank=re.sub(r'[^A-Z0-9]','',str(row.get('Bank') or '').upper())
    number=re.sub(r'[^A-Z0-9]','',str(row.get('Account number') or '').upper())
    if bank in ('AMEX','AMERICANEXPRESS','CITIBANKCREDITCARD','SAPPHIRECHASE'):return 'Debts'
    if bank in ('COMERICA','COMERICACREDITLINE','FIFTHTHIRDEXCOMERICA') and number in ('05590000991','05590000991CREDITLINE'):return 'Debts'
    return 'Deposits'

def eligible(row,name):
    if section(row)!=name or row.get('Status')!='IMPORTED':return False
    allowed=('SOURCE RECONCILIATION NOT VERIFIED','LIABILITY CONVENTION UNVERIFIED') if name=='Debts' else ('SOURCE RECONCILIATION NOT VERIFIED',)
    return row.get('Verification') in allowed and optional_decimal(row.get('Closing balance converted to USD')) is not None

def partition(rows):
    result={'Deposits':[],'Debts':[]}
    for row in rows:result[section(row)].append(row)
    return result

def totals(rows):
    return {name:exact_sum(optional_decimal(r['Closing balance converted to USD']) for r in group if eligible(r,name)) for name,group in partition(rows).items()}
