"""Read-only report authority for committed Greek Natalia wallet pairs."""
from datetime import date
from decimal import Decimal, InvalidOperation
import json
import re
from types import SimpleNamespace
from revolut_sections import wallet_key, compact

OWNER = 'Natalia Fishman'
BANK = 'Revolut'
IBAN = 'LT453250028012133298'
PREFIX = 'Greek Revolut document '


def money(value):
    amount = Decimal(str(value))
    if not amount.is_finite() or amount != amount.quantize(Decimal('.01')):
        raise ValueError('Non-cent financial evidence')
    return amount.quantize(Decimal('.01'))


def report_links(connection):
    """Return (physical Setup ID, currency, statement key); no inferred wallets.

    Recompute the writer's deterministic key from committed source movements,
    verify every identity/count and require a complete EUR/USD document pair.
    Ambiguous or damaged evidence never establishes report-wallet authority.
    """
    cur = connection.cursor()
    try:
        cur.execute('SELECT id,account_name,bank,account_number,currency FROM account_list')
        accounts = [r for r in cur.fetchall() if compact(r[3]) == IBAN]
        if len(accounts) != 1 or tuple(accounts[0][1:]) != (OWNER,BANK,IBAN,''):
            return []
        account_id = accounts[0][0]
        cur.execute('''SELECT b.statement_hash,b.account_name,b.bank,b.account_number,b.currency,
            b.period_start,b.period_end,b.opening_balance,b.money_in,b.money_out,b.closing_balance,
            b.source,b.notes,h.transaction_count
            FROM statement_balances b LEFT JOIN statement_imports h ON h.statement_hash=b.statement_hash
            WHERE b.account_number=? AND b.source LIKE ?''', (IBAN,PREFIX+'%'))
        candidates = cur.fetchall()
        groups = {}
        for r in candidates:
            try:
                key,owner,bank,iban,currency,start,end,opening,incoming,outgoing,closing,source,notes,count = r
                audit = json.loads(notes)
                document = audit['document_sha256']
                if (not isinstance(document,str) or not re.fullmatch('[0-9a-f]{64}',document)
                    or source != PREFIX+document or (owner,bank,iban) != (OWNER,BANK,IBAN)
                    or currency not in ('EUR','USD') or audit['wallet_currency'] != currency
                    or audit['iban'] != IBAN or audit['owner'] != OWNER.upper()
                    or any(type(audit[k]) is not int or audit[k] < 0 for k in ('pending_excluded','reversed_excluded'))
                    or not isinstance(count,int) or count <= 0):
                    return []
                start,end = date.fromisoformat(str(start)),date.fromisoformat(str(end))
                opening,incoming,outgoing,closing = map(money,(opening,incoming,outgoing,closing))
                if start>end or min(incoming,outgoing)<0 or opening+incoming-outgoing != closing:
                    return []
                cur.execute('''SELECT txn_date,original_description,amount,currency,account_name,bank,
                    account_number,split_parent_id,split_group_id FROM classified_transactions
                    WHERE statement_hash=? ORDER BY id''',(key,))
                rows = cur.fetchall()
                if len(rows) != count:
                    return []
                transactions=[]
                for d,text,amount,ccy,name,tbank,number,parent,split in rows:
                    d=date.fromisoformat(str(d));amount=money(amount)
                    if ((ccy,name,tbank,number)!=(currency,OWNER,BANK,IBAN) or parent is not None
                        or split not in (None,'') or not start<=d<=end or not isinstance(text,str) or not text):
                        return []
                    transactions.append(SimpleNamespace(date=d,description=text,amount=amount))
                if (sum((t.amount for t in transactions if t.amount>0),Decimal(0)) != incoming
                    or -sum((t.amount for t in transactions if t.amount<0),Decimal(0)) != outgoing):
                    return []
                wallet=SimpleNamespace(iban=IBAN,owner=audit['owner'],currency=currency,start=start,end=end,
                    opening=opening,credits=incoming,debits=outgoing,closing=closing,transactions=transactions)
                if wallet_key(wallet) != key:
                    return []
                group=groups.setdefault((document,start,end),{})
                if currency in group:
                    return []
                group[currency]=key
            except (ValueError,TypeError,KeyError,InvalidOperation,ArithmeticError):
                return []
        if not groups or any(set(group)!= {'EUR','USD'} for group in groups.values()):
            return []
        # Two physical documents claiming the same wallet period are ambiguous.
        if len({(start,end) for _,start,end in groups}) != len(groups):
            return []
        return [(account_id,currency,key) for group in groups.values() for currency,key in group.items()]
    finally:
        cur.close()
