"""Read-only, original-source-bound link for the existing blank-currency row.

This is not a wallet resolver. Any second currency, duplicate Setup identity or
changed source evidence rejects the link. Setup and stored history stay intact.
"""
from decimal import Decimal
from hashlib import sha256
import json
import re

NUMBER = 'LT503250070356815945'
SOURCE_HASH = 'f6725c4a6e94c3e6ad9954e8a5a16ed4dfbd3a4a90b2edb9b2d1b93a46e3bd8a'
FACTS = 'b53f3aed0201dfc080ba6823e0645242a8f80ffb980fd1206920d156569c9ef7'
MOVEMENTS = '5d526c47d6331adb82c7b2f7f420c61b7b52d6069477dcd1e071ab2751bcf148'


def report_links(connection):
    """Return only the one proven EUR report identity, never write or infer FX."""
    compact = lambda value: re.sub(r'[^A-Z0-9]', '', str(value or '').upper())
    money = lambda value: str(Decimal(str(value)).normalize())
    digest = lambda value: sha256(json.dumps(value, ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()
    cur = connection.cursor()
    try:
        cur.execute('SELECT id,account_name,bank,account_number,currency FROM account_list')
        accounts = [r for r in cur.fetchall() if compact(r[2]) in ('REVOLUT','REVOLUTBANK','REVOLUTBANKUAB') and compact(r[3]) == NUMBER]
        if len(accounts) != 1 or accounts[0][:3] != (968,'B-Projects Limited','Revolut') or str(accounts[0][4] or '').strip():
            return []
        cur.execute("SELECT bank,account_name,account_number,currency,statement_hash,period_start,period_end,opening_balance,money_in,money_out,closing_balance,source FROM statement_balances WHERE REPLACE(account_number,' ','')=?", (NUMBER,))
        balances = [r for r in cur.fetchall() if compact(r[0]) in ('REVOLUT','REVOLUTBANK','REVOLUTBANKUAB') and compact(r[2]) == NUMBER]
        cur.execute("SELECT bank,account_name,account_number,currency,statement_hash,txn_date,amount,split_parent_id FROM classified_transactions WHERE REPLACE(account_number,' ','')=?", (NUMBER,))
        transactions = [r for r in cur.fetchall() if compact(r[0]) in ('REVOLUT','REVOLUTBANK','REVOLUTBANKUAB') and compact(r[2]) == NUMBER]
        if not balances or not transactions or any(r[3] != 'EUR' or r[1] != 'B-Projects Limited' for r in balances + transactions):
            return []
        source = [r for r in balances if r[4] == SOURCE_HASH]
        if len(source) != 1 or source[0][11] != 'Russian Revolut Business':
            return []
        b = source[0]
        facts = [compact(b[2]), b[3], str(b[5]), str(b[6])] + [money(v) for v in b[7:11]]
        if digest(facts) != FACTS:
            return []
        source_rows = [r for r in transactions if r[4] == SOURCE_HASH]
        # A changed/SPLIT source must not be reinterpreted by this report link.
        if len(source_rows) != 8 or any(r[7] is not None for r in source_rows):
            return []
        if digest(sorted((str(r[5]), money(r[6])) for r in source_rows)) != MOVEMENTS:
            return []
        cur.execute('SELECT transaction_count FROM statement_imports WHERE statement_hash=?', (SOURCE_HASH,))
        history = cur.fetchall()
        return [(968, 'EUR')] if len(history) == 1 and history[0][0] == 8 else []
    except (ValueError, TypeError, ArithmeticError):
        return []
    finally:
        cur.close()
