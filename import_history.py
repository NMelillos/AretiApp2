"""Committed import boundary and timezone-safe history presentation."""
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from zoneinfo import ZoneInfo

import pandas as pd


def commit_statement(db, frame, name, fingerprint, balance, account):
    """Persist a validated statement, history and balance in one transaction."""
    if not str(fingerprint).strip() or not str(name).strip():
        raise ValueError('Statement identity is required.')
    if db.statement_already_imported(fingerprint):
        return 0, True, 0
    if frame.attrs.get('safra_sections'):
        return db.save_pending_transactions(frame, name, fingerprint)
    account, balance = dict(account or {}), dict(balance or {})
    if balance.get('source') == 'BOC bank columns':
        from boc_import import validate_preview
        validate_preview(frame, balance, account)
    if not account.get('account_number') or not account.get('currency'):
        raise ValueError('A complete statement account is required.')
    if frame.empty:
        required = ('period_start', 'period_end', 'opening_balance', 'closing_balance')
        if any(balance.get(k) in (None, '') for k in required):
            raise ValueError('An empty statement requires its period and opening/closing balances.')
        try:
            opening = Decimal(str(balance['opening_balance']))
            closing = Decimal(str(balance['closing_balance']))
            if not opening.is_finite() or not closing.is_finite() or opening != closing:
                raise ValueError('An empty statement must have equal finite opening/closing balances.')
            for key in ('money_in', 'money_out'):
                if balance.get(key) not in (None, ''):
                    flow = Decimal(str(balance[key]))
                    if not flow.is_finite() or flow != 0:
                        raise ValueError('Statement activity requires transaction rows.')
        except InvalidOperation:
            raise ValueError('Invalid statement balance.') from None
    elif not {'Date', 'Description', 'Amount', 'currency', 'account_number'}.issubset(frame.columns):
        raise ValueError('The statement preview is incomplete.')
    else:
        for row in frame.to_dict('records'):
            if any(pd.isna(row[key]) or not str(row[key]).strip()
                   for key in ('Date', 'currency', 'account_number')):
                raise ValueError('Transaction date and account identity are required.')
            try:
                amount = Decimal(str(row['Amount']))
            except InvalidOperation:
                raise ValueError('Invalid transaction amount.') from None
            if not amount.is_finite():
                raise ValueError('Invalid transaction amount.')
    conn = db.get_connection()
    try:
        result = db.save_pending_transactions(frame, name, fingerprint, _connection=conn)
        inserted, duplicate, skipped = result
        if duplicate:
            conn.rollback()
            return result
        if not frame.empty and not inserted:
            conn.rollback()
            return 0, True, skipped
        if not db.save_statement_balance(fingerprint, name, balance, account, _connection=conn):
            raise ValueError('The statement balance section could not be recorded.')
        timestamp = datetime.now(timezone.utc).isoformat(timespec='seconds')
        cur = conn.cursor()
        cur.execute('UPDATE statement_imports SET imported_at = ? WHERE statement_hash = ?',
                    (timestamp, fingerprint))
        conn.commit()
        return result
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def completed_history(db, history):
    """Do not infer success from a filename/hash or an empty import header alone."""
    if history.empty:
        return history
    conn = db.get_connection()
    try:
        sections = pd.read_sql_query('''
            SELECT b.statement_hash, COUNT(t.id) AS source_rows
            FROM statement_balances b
            LEFT JOIN classified_transactions t ON t.statement_hash = b.statement_hash
                AND t.split_parent_id IS NULL
            GROUP BY b.statement_hash
        ''', conn)
    finally:
        conn.close()
    result = history[history.statement_hash.isin(sections.statement_hash)].copy()
    counts = sections.set_index('statement_hash').source_rows
    result = result[result.transaction_count.eq(result.statement_hash.map(counts))].copy()
    zero = result.transaction_count.eq(0)
    valid_zero = (result.period_start.fillna('').ne('') & result.period_end.fillna('').ne('')
                  & result.account_number.fillna('').ne('') & result.currency.fillna('').ne('')
                  & result.opening_balance.notna() & result.closing_balance.notna()
                  & result.opening_balance.eq(result.closing_balance))
    result = result[~zero | valid_zero].copy()
    result['duplicate_status'] = 'Imported'
    return result


def cyprus_time(value):
    """Legacy timezone-less timestamps cannot be safely assumed to be UTC."""
    if value is None or pd.isna(value) or not str(value).strip():
        return ''
    try:
        parsed = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    except ValueError:
        return str(value) + ' (timezone unverified)'
    if parsed.tzinfo is None:
        return str(value) + ' (timezone unverified)'
    return parsed.astimezone(ZoneInfo('Europe/Nicosia')).strftime('%Y-%m-%d %H:%M:%S %Z')
