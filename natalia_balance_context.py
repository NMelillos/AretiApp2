"""Source-bound report linkage for Natalia's blank Setup currency; no Setup remap."""
import json
import re
from decimal import Decimal
from hashlib import sha256

NUMBER = 'LT453250028012133298'
ACTION = 'verified Natalia statement currency report link'
FIELDS = ('period_start','period_end','opening_balance','money_in','money_out','closing_balance')


def eligible(account):
    return (account.get('bank') == 'Revolut' and account.get('account_name') == 'Natalia Fishman'
            and account.get('account_number') == NUMBER
            and account.get('currency') == 'EUR'
            and tuple(account.get('_setup_identity', ())) == ('Natalia Fishman','Revolut',NUMBER,''))


def fingerprint(balance):
    values = [str(balance[k]) if k.startswith('period_') else str(Decimal(str(balance[k])).normalize()) for k in FIELDS]
    return sha256(json.dumps(values,separators=(',',':')).encode()).hexdigest()


def has_link(notes, account, digest, balance):
    for line in str(notes or '').splitlines():
        try:
            event = json.loads(line)
            if (event.get('action') == ACTION and event.get('actor')
                    and event.get('setup_id') == account['id'] and event.get('source_hash') == digest
                    and event.get('account_number') == NUMBER and event.get('currency') == 'EUR'
                    and event.get('balance_fingerprint') == fingerprint(balance)
                    and event.get('transaction_changes') == 0):
                return True
        except (ValueError, TypeError, KeyError, ArithmeticError):
            continue
    return False


def event(account, digest, balance, actor, now):
    if not eligible(account):
        return None
    return json.dumps(dict(action=ACTION, actor=str(actor), setup_id=account['id'],
                           source_hash=digest, account_number=NUMBER, currency='EUR',
                           balance_fingerprint=fingerprint(balance), transaction_changes=0,
                           linked_at=now), separators=(',',':'))


def report_links(connection):
    """Read only; a link cannot turn into a global currency fallback or wallet."""
    cur = connection.cursor()
    try:
        cur.execute('SELECT id,bank,account_number,currency FROM account_list')
        compact=lambda value:re.sub(r'[^\w]','',str(value or '')).upper()
        matches=[r for r in cur.fetchall() if compact(r[1]) in ('REVOLUT','REVOLUTBANK','REVOLUTBANKUAB')
                 and compact(r[2])==NUMBER and str(r[3] or '').strip().upper() in ('','EUR')]
        if len(matches)!=1:
            return []
        cur.execute('''SELECT a.id,b.statement_hash,b.notes,b.period_start,b.period_end,
            b.opening_balance,b.money_in,b.money_out,b.closing_balance,h.transaction_count,
            (SELECT COUNT(*) FROM classified_transactions t WHERE t.statement_hash=b.statement_hash
             AND t.split_parent_id IS NULL)
            FROM account_list a JOIN statement_balances b ON b.bank=a.bank
              AND b.account_name=a.account_name AND b.account_number=a.account_number AND b.currency='EUR'
            JOIN statement_imports h ON h.statement_hash=b.statement_hash
            WHERE a.bank='Revolut' AND a.account_name='Natalia Fishman'
              AND a.account_number=? AND COALESCE(a.currency,'')='' ''', (NUMBER,))
        rows = cur.fetchall()
    finally:
        cur.close()
    if len({row[0] for row in rows}) != 1:
        return []
    valid = []
    for row in rows:
        balance = dict(zip(FIELDS,row[3:9]))
        if row[9] is not None and row[9] > 0 and row[9] == row[10] and has_link(row[2],dict(id=row[0]),row[1],balance):
            valid.append((row[0],row[1]))
    if len({row[0] for row in valid}) != 1:
        return []
    return valid
