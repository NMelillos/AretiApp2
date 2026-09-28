"""Authenticated comparison only. This module has no repair or persistence path."""
from contextlib import closing
from decimal import Decimal, localcontext
from io import BytesIO
import hashlib
import json
import re
import struct

import streamlit as st
from streamlit.runtime.scriptrunner import get_script_run_ctx


class CompareBlocked(ValueError):
    pass


def authorized():
    # These non-widget keys are assigned by require_login on the server. Never
    # consult query parameters, widget usernames, cookies or a supplied identity.
    return (get_script_run_ctx(suppress_warning=True) is not None
            and st.session_state.get('authenticated') is True
            and st.session_state.get('login_user') == 'Areti'
            and not st.session_state.get('third_report_authenticated'))


def exact_decimal(value):
    if isinstance(value, float):
        result = Decimal.from_float(value)
    elif isinstance(value, (Decimal, int, str)) and not isinstance(value, bool):
        result = Decimal(value)
    else:
        raise CompareBlocked('BLOCKED: missing monetary evidence.')
    if not result.is_finite():
        raise CompareBlocked('BLOCKED: non-finite monetary evidence.')
    return result


def difference(expected, stored):
    with localcontext() as ctx:
        ctx.prec = max(len(v.as_tuple().digits) for v in (expected, stored)) + abs(
            expected.as_tuple().exponent - stored.as_tuple().exponent) + 2
        return expected - stored


def _expected(content):
    import pandas as pd
    import parsing
    from safra_history import section_balances
    if not isinstance(content, bytes) or not content.startswith(b'%PDF-'):
        raise CompareBlocked('BLOCKED: a readable original PDF is required.')
    with parsing.pdfplumber.open(BytesIO(content)) as pdf:
        pages = [page.extract_text() or '' for page in pdf.pages]
    validated = parsing._parse_safra_pages(pages)
    sections = []
    for section, text in zip(validated.attrs['safra_sections'], pages):
        section = dict(section)
        # Reuse the same canonical parser's exact string output, not the legacy
        # float presentation frame. No alternate booking/currency parser exists.
        rows = parsing._parse_safra_pdf_text(text)
        frame = pd.DataFrame(rows, columns=['Date', 'Description', 'Amount',
                                           'statement_currency', 'source'])
        frame['Amount'] = frame.Amount.map(Decimal)
        section_balances(section, frame, text)
        section['rows'] = frame.to_dict('records')
        sections.append(section)
    return sections


def _dicts(cur, sql, params=()):
    cur.execute(sql, params)
    names = [column[0] for column in cur.description]
    return [dict(zip(names, row)) for row in cur.fetchall()]


MONEY = {
    'statement_balances': ('opening_balance', 'money_out', 'money_in', 'closing_balance'),
    'classified_transactions': ('amount', 'amount_usd', 'fx_rate', 'split_original_amount'),
}


def _schema(cur, postgres):
    result = {}
    for table, fields in MONEY.items():
        if postgres:
            rows = _dicts(cur, '''SELECT column_name, data_type FROM information_schema.columns
                WHERE table_schema = current_schema() AND table_name = ?''', (table,))
            types = {r['column_name']: r['data_type'] for r in rows}
        else:
            rows = _dicts(cur, 'PRAGMA table_info(' + table + ')')
            types = {r['name']: r['type'] for r in rows}
        if not all(field in types for field in fields):
            raise CompareBlocked('BLOCKED: required monetary columns are missing.')
        result[table] = {field: types[field] for field in fields}
    return result


def _money_select(table, types, postgres):
    if not postgres:
        return '*'
    expressions = ['*', 'xmin::text AS _read_version']
    for field, kind in types[table].items():
        if kind in ('real', 'double precision'):
            send = 'float4send' if kind == 'real' else 'float8send'
            expressions.append("encode(" + send + '(' + field + "), 'hex') AS exact_" + field)
        elif kind == 'numeric':
            expressions.append(field + '::text AS exact_' + field)
        else:
            raise CompareBlocked('BLOCKED: unsupported monetary storage type.')
    return ', '.join(expressions)


def _decode(rows, table, types, postgres):
    for row in rows:
        for field, kind in types[table].items():
            value = row.pop('exact_' + field, row.get(field))
            if value is None:
                row[field] = None
                continue
            if postgres and kind in ('real', 'double precision'):
                value = struct.unpack('!f' if kind == 'real' else '!d', bytes.fromhex(value))[0]
            row[field] = exact_decimal(value)
    return rows


def _iban(value):
    compact = re.sub(r'\s+', '', str(value or '')).upper()
    matches = re.findall(r'CH\d{19}(?!\d)', compact)
    if len(matches) != 1:
        raise CompareBlocked('BLOCKED: account identity is missing or ambiguous.')
    return matches[0]


def _identity(row):
    return (str(row['txn_date']), str(row['original_description']).replace('\r\n', '\n'), row['currency'])


def _reconcile(sections, balances, imports, transactions):
    if len(sections) != len(balances) or len(imports) != len(balances):
        raise CompareBlocked('BLOCKED: account-section or import count differs.')
    by_hash = {r['statement_hash']: r for r in imports}
    if len(by_hash) != len(imports) or {b['statement_hash'] for b in balances} != set(by_hash):
        raise CompareBlocked('BLOCKED: import linkage is ambiguous.')
    output = {'sections': [], 'transactions': []}
    used = set()
    for section in sections:
        candidates = [b for b in balances if _iban(b['account_number']) == section['source_iban']
                      and b['currency'] == section['statement_currency']
                      and b['period_start'] == section['period_start'] and b['period_end'] == section['period_end']]
        if len(candidates) != 1 or candidates[0]['id'] in used:
            raise CompareBlocked('BLOCKED: account-section mapping is not one-to-one.')
        balance = candidates[0]
        used.add(balance['id'])
        notes = json.loads(balance['notes'] or '{}')
        if (notes.get('source_page') != section['source_page']
                or notes.get('account_number') != section['source_account_number']):
            raise CompareBlocked('BLOCKED: source page/account metadata differs.')
        key = balance['statement_hash']
        rows = [r for r in transactions if r['statement_hash'] == key]
        if any(r['split_parent_id'] is not None or r['split_group_id'] for r in rows):
            raise CompareBlocked('BLOCKED: SPLIT dependencies require a separate reconciliation.')
        if len(rows) != len(section['rows']) or by_hash[key]['transaction_count'] != len(rows):
            raise CompareBlocked('BLOCKED: transaction count differs.')
        index = {}
        for row in rows:
            identity = _identity(row)
            if identity in index:
                raise CompareBlocked('BLOCKED: repeated transaction identity is ambiguous.')
            if (_iban(row['account_number']) != section['source_iban']
                    or row['account_name'] != balance['account_name'] or row['bank'] != balance['bank']):
                raise CompareBlocked('BLOCKED: transaction account association differs.')
            index[identity] = row
        info = {'Page': section['source_page'], 'Import ID': by_hash[key]['id'],
                'Balance ID': balance['id'], 'Account': balance['account_name'],
                'IBAN': section['source_iban'], 'Currency': section['statement_currency'],
                'Period start': section['period_start'], 'Period end': section['period_end'],
                'Transactions': len(rows), 'Reviewed': sum(r['reviewed'] == 1 for r in rows),
                'SPLIT dependencies': 0, 'Status': 'MATCH'}
        for field in MONEY['statement_balances']:
            expected, stored = exact_decimal(section[field]), exact_decimal(balance[field])
            info['PDF ' + field], info['Stored ' + field] = str(expected), str(stored)
            info['Difference ' + field] = str(difference(expected, stored))
            if expected != stored:
                info['Status'] = 'MISMATCH'
        output['sections'].append(info)
        for expected in section['rows']:
            identity = (str(expected['Date']), expected['Description'].replace('\r\n', '\n'), expected['statement_currency'])
            if identity not in index:
                raise CompareBlocked('BLOCKED: transaction identity cannot be matched exactly.')
            row = index.pop(identity)
            value, stored = exact_decimal(expected['Amount']), exact_decimal(row['amount'])
            output['transactions'].append({'Page': section['source_page'], 'Import ID': by_hash[key]['id'],
                'Record ID': row['id'], 'IBAN': section['source_iban'], 'Currency': row['currency'],
                'Date': row['txn_date'], 'Description / value date': row['original_description'],
                'Stored amount': str(stored), 'PDF amount': str(value), 'Difference': str(difference(value, stored)),
                'Reviewed': row['reviewed'], 'Status': 'MATCH' if value == stored else 'MISMATCH'})
        if index:
            raise CompareBlocked('BLOCKED: unmatched stored transactions remain.')
    return output


def compare_existing(content):
    if not authorized():
        raise CompareBlocked('BLOCKED: authorized primary application session required.')
    import db
    sections = _expected(content)
    fingerprint = hashlib.sha256(content).hexdigest()
    with closing(db.get_connection()) as conn:
        cur = conn.cursor()
        try:
            if db.USING_POSTGRES:
                cur.execute('BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY')
                cur.execute('SHOW transaction_read_only')
                if cur.fetchone()[0] != 'on':
                    raise CompareBlocked('BLOCKED: read-only transaction could not be verified.')
            else:
                cur.execute('PRAGMA query_only = ON')
                cur.execute('PRAGMA query_only')
                if cur.fetchone()[0] != 1:
                    raise CompareBlocked('BLOCKED: read-only connection could not be verified.')
                cur.execute('BEGIN')
            types = _schema(cur, db.USING_POSTGRES)
            balances = _decode(_dicts(cur, 'SELECT ' + _money_select('statement_balances', types, db.USING_POSTGRES)
                + ' FROM statement_balances WHERE source = ? ORDER BY id', ('Safra document ' + fingerprint,)),
                'statement_balances', types, db.USING_POSTGRES)
            if not balances:
                raise CompareBlocked('BLOCKED: no existing import matches the PDF fingerprint.')
            imports, transactions = [], []
            for balance in balances:
                key = balance['statement_hash']
                import_select = '*, xmin::text AS _read_version' if db.USING_POSTGRES else '*'
                imports.extend(_dicts(cur, 'SELECT ' + import_select + ' FROM statement_imports WHERE statement_hash = ?', (key,)))
                transactions.extend(_decode(_dicts(cur, 'SELECT ' + _money_select('classified_transactions', types, db.USING_POSTGRES)
                    + ' FROM classified_transactions WHERE statement_hash = ? ORDER BY id', (key,)),
                    'classified_transactions', types, db.USING_POSTGRES))
            result = _reconcile(sections, balances, imports, transactions)
            result['fingerprint'] = fingerprint
            result['schema'] = types
            from repair_readiness import collect
            result['readiness'] = collect(cur, db.USING_POSTGRES, fingerprint, result, balances, imports, transactions)
            return result
        finally:
            conn.rollback()
            cur.close()


def render_compare(ui, content):
    if not authorized():
        return
    if not ui.button('Compare with Existing Import'):
        return
    try:
        result = compare_existing(content)
        ui.info('Read-only comparison. No records were changed. Repair is not available.')
        mismatches = sum(row['Status'] == 'MISMATCH' for row in result['transactions'])
        section_mismatches = sum(row['Status'] == 'MISMATCH' for row in result['sections'])
        ui.info(f"PDF fingerprint: {result['fingerprint']}. "
                f"Transaction differences: {mismatches}; account sections with balance differences: {section_mismatches}.")
        ui.dataframe(result['sections'], use_container_width=True, hide_index=True)
        ui.dataframe(result['transactions'], use_container_width=True, hide_index=True)
        from repair_readiness import render
        render(ui, result['readiness'])
    except CompareBlocked as exc:
        ui.error(str(exc))
    except Exception:
        ui.error('BLOCKED: comparison evidence could not be validated. No changes were made.')
