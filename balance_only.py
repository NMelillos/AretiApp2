"""Source-verified BOC overlap records; never repair an existing import."""
import json
from decimal import Decimal

PREFIX = 'Verified BOC balance-only v1: '


def audit_overlap(conn, frame, balance, account):
    """Every source movement must reference a distinct, unchanged stored row."""
    if balance.get('source') != 'BOC bank columns' or frame.empty:
        raise ValueError('Balance-only activity requires a verified BOC source.')
    cur = conn.cursor()
    cur.execute('''SELECT id, txn_date, amount, original_description FROM classified_transactions
        WHERE bank = ? AND account_number = ? AND currency = ? AND split_parent_id IS NULL''',
        (account['bank'], account['account_number'], account['currency']))
    available = list(cur.fetchall())
    references = []
    for row in frame.to_dict('records'):
        matched = next((item for item in available if str(item[1]) == str(row['Date'])
                       and Decimal(str(item[2])) == Decimal(str(row['Amount']))
                       and str(item[3]) == str(row['Description'])), None)
        if matched is None:
            raise ValueError('Balance-only source movement has no exact stored counterpart; nothing recorded.')
        available.remove(matched)
        references.append([matched[0], str(row['Date']), str(row['Amount'])])
    return PREFIX + json.dumps({'references': references}, separators=(',', ':'))


def verified_record(conn, notes, bank, number, currency, opening, credits, debits, closing):
    """Read-only verification of the auditable overlap record, including its references."""
    try:
        if not isinstance(notes, str) or not notes.startswith(PREFIX):
            return False
        refs = json.loads(notes[len(PREFIX):])['references']
        if not refs or len({r[0] for r in refs}) != len(refs):
            return False
        cur = conn.cursor()
        actual = []
        for ident, when, amount in refs:
            cur.execute('''SELECT txn_date, amount FROM classified_transactions WHERE id = ?
                AND bank = ? AND account_number = ? AND currency = ? AND split_parent_id IS NULL''',
                (ident, bank, number, currency))
            row = cur.fetchone()
            if row is None or str(row[0]) != when or Decimal(str(row[1])) != Decimal(amount):
                return False
            actual.append(Decimal(amount))
        values = list(map(lambda value: Decimal(str(value)), (opening, credits, debits, closing)))
        if not all(value.is_finite() for value in values):
            return False
        op, incoming, outgoing, end = values
        return (sum((v for v in actual if v > 0), Decimal(0)) == incoming
                and -sum((v for v in actual if v < 0), Decimal(0)) == outgoing
                and op + incoming - outgoing == end)
    except (ValueError, TypeError, KeyError, ArithmeticError):
        return False
