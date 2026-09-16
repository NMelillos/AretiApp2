"""Synthetic, value-free diagnostics after an Income/Charity zero-row UPDATE."""
import ast
from contextlib import closing
import hashlib
import os
from pathlib import Path
import re
import subprocess
import tempfile
import traceback
from unittest.mock import Mock, patch

import pandas as pd

BASELINE = '3f8c9df876f973643bf9afc6922b42093b136778'
APP_OLD = b'    count = save_reviewed_rows(save_df)\n'
APP_NEW = b'    count = save_reviewed_rows(save_df, diagnose_income_charity_conflicts=True)\n'
DB_OLD = b'def save_reviewed_rows(df):\n'
DB_NEW = b'def save_reviewed_rows(df, *, diagnose_income_charity_conflicts=False):\n'
DB_DIAGNOSTIC = (b'                if cur.rowcount == 0 and diagnose_income_charity_conflicts:\n'
                 b'                    from income_save_diagnostic import raise_conflict\n'
                 b'                    raise_conflict(cur, tx_id, before)\n')


def without_income_save_diagnostic(name, source):
    source = source.replace(b'\r\n', b'\n')
    if name not in ('app.py', 'db.py'):
        return source
    baseline = subprocess.check_output(['git', 'show', BASELINE + ':' + name]).replace(b'\r\n', b'\n')
    if source == baseline:
        return source
    if name == 'app.py':
        assert source.count(APP_NEW) == 1
        source = source.replace(APP_NEW, APP_OLD, 1)
    else:
        assert source.count(DB_NEW) == source.count(DB_DIAGNOSTIC) == 1
        source = source.replace(DB_NEW, DB_OLD, 1).replace(DB_DIAGNOSTIC, b'', 1)
    assert source == baseline, 'Change beyond exact Income/Charity diagnostic opt-in'
    return source


def exercise(db, backend):
    db.init_db()
    for sub in ('First', 'Second'):
        db.add_category('Income', sub, 'Income')
    connect = db.get_connection

    def seed():
        with closing(connect()) as conn:
            cur = conn.cursor()
            cur.execute('DELETE FROM classified_transactions')
            for identity in (701, 702):
                cur.execute('''INSERT INTO classified_transactions
                    (id,row_hash,txn_date,amount,amount_usd,fx_rate,currency,category,subcategory,
                     original_description,account_name,reviewed,status)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                    (identity, 'synthetic-' + str(identity), '2026-01-10', 125.75, 125.75, 1.25,
                     'USD', 'Income', 'First', 'PRIVATE_SYNTHETIC_DESCRIPTION',
                     'PRIVATE_SYNTHETIC_ACCOUNT', 1, 'reviewed'))
            conn.commit()

    def rows():
        with closing(connect()) as conn:
            cur = conn.cursor()
            cur.execute('SELECT * FROM classified_transactions ORDER BY id')
            return cur.fetchall()

    payload = pd.DataFrame([{'id': identity, 'category': 'Income', 'subcategory': 'Second',
                            'reviewed': True, '_expected_category': 'Income',
                            '_expected_subcategory': 'First', '_expected_reviewed': True}
                           for identity in (701, 702)])
    cases = [('category', 'PRIVATE_SYNTHETIC_CATEGORY', 'CATEGORY'),
             ('subcategory', None, 'SUBCATEGORY'), ('reviewed', None, 'REVIEWED'),
             ('status', 'PRIVATE_SYNTHETIC_STATUS', 'STATUS'),
             ('amount', 999.125, 'AMOUNT'), ('amount_usd', None, 'AMOUNT_USD'),
             ('currency', 'PRIVATE_SYNTHETIC_CURRENCY', 'CURRENCY'),
             ('fx_rate', 9.875, 'FX_RATE'), ('delete', None, 'ROW_MISSING')]

    checks = [(field, value, label, True) for field, value, label in cases]
    checks.append(('amount', 999.125, 'AMOUNT', False))
    for field, value, label, enabled in checks:
        seed()
        initial = rows()
        diagnostic_calls = []
        guarded_calls = []
        raced = [False]
        class Cursor:
            def __init__(self, cursor):
                self.cursor = cursor
            def __getattr__(self, name):
                return getattr(self.cursor, name)
            def execute(self, sql, params=None):
                if 'UPDATE classified_transactions' in sql and 'IS NOT DISTINCT FROM' in sql:
                    guarded_calls.append((self, sql, params))
                if 'UPDATE classified_transactions' in sql and 'IS NOT DISTINCT FROM' in sql and params[8] == 702 and not raced[0]:
                    raced[0] = True
                    mutation = 'DELETE FROM classified_transactions WHERE id=?' if field == 'delete' else f'UPDATE classified_transactions SET {field}=? WHERE id=?'
                    values = (702,) if field == 'delete' else (value, 702)
                    if backend == 'PostgreSQL':
                        with closing(connect()) as other:
                            other.cursor().execute(mutation, values)
                            other.commit()
                    else:
                        # SQLite serializes writers after the first batch update.
                        self.cursor.execute(mutation, values)
                if sql.lstrip().startswith('SELECT') and 'IS NOT DISTINCT FROM' in sql:
                    diagnostic_calls.append((self, sql, params))
                return self.cursor.execute(sql, params) if params is not None else self.cursor.execute(sql)
        class Connection:
            def __init__(self):
                self.connection = connect()
            def __getattr__(self, name):
                return getattr(self.connection, name)
            def cursor(self):
                return Cursor(self.connection.cursor())
        with patch.object(db, 'get_connection', Connection):
            try:
                if enabled:
                    db.save_reviewed_rows(payload.copy(), diagnose_income_charity_conflicts=True)
                else:
                    db.save_reviewed_rows(payload.copy())
            except db.ConcurrentTransactionEditError as error:
                if enabled:
                    assert str(error) == 'Income/Charity Save conflict predicates: ' + label
                    assert error.__suppress_context__
                    assert not hasattr(error, 'transaction_id')
                else:
                    assert type(error) is db.ConcurrentTransactionEditError
                    assert error.transaction_id == 702
            else:
                raise AssertionError('A failed protected UPDATE must still reject the batch')
        assert raced[0] and len(diagnostic_calls) == int(enabled)
        if enabled:
            update_cursor, update_sql, update_params = guarded_calls[-1]
            diagnostic_cursor, diagnostic_sql, diagnostic_params = diagnostic_calls[0]
            assert diagnostic_cursor is update_cursor
            assert diagnostic_params == tuple(update_params[9:]) + (update_params[8],)
            predicate = r'\b\w+ IS NOT DISTINCT FROM (?:CAST\(\? AS REAL\)|\?)'
            assert re.findall(predicate, diagnostic_sql) == re.findall(predicate, update_sql)
        assert rows()[0] == initial[0], 'First row must roll back atomically'
        if backend == 'SQLite':
            assert rows() == initial
        print(f'PASS {backend}: {label}, diagnostic={enabled}, exact predicates/parameters, atomic rollback')

    seed()
    from income_save_diagnostic import raise_conflict
    with patch('income_save_diagnostic.raise_conflict', side_effect=AssertionError('Unexpected diagnostic')):
        assert db.save_reviewed_rows(payload.copy(), diagnose_income_charity_conflicts=True) == 2
        stale = payload.copy()
        try:
            db.save_reviewed_rows(stale, diagnose_income_charity_conflicts=True)
        except db.ConcurrentTransactionEditError:
            pass
        else:
            raise AssertionError('Snapshot conflict must remain protected')
    seed()
    with closing(connect()) as conn:
        cur = conn.cursor()
        cur.execute('SELECT category,subcategory,reviewed,status,original_description,normalized_description,beneficiary,transaction_type,amount,amount_usd,currency,fx_rate FROM classified_transactions WHERE id=?', (701,))
        snapshot = cur.fetchone()
        try:
            raise_conflict(cur, 701, snapshot)
        except db.ConcurrentTransactionEditError as error:
            assert str(error) == 'Income/Charity Save conflict predicates: NONE_OBSERVED'
    class BrokenCursor:
        def execute(self, *args):
            raise RuntimeError('PRIVATE_SYNTHETIC_SQL_PARAMETERS')
    try:
        raise_conflict(BrokenCursor(), 701, snapshot)
    except db.ConcurrentTransactionEditError as error:
        assert str(error) == 'Income/Charity Save conflict predicates: DIAGNOSTIC_UNAVAILABLE'
        assert error.__suppress_context__
        assert 'PRIVATE_SYNTHETIC_SQL_PARAMETERS' not in ''.join(traceback.format_exception(error))
    print(f'PASS {backend}: successful Save, earlier stale guard, safe diagnostic failure/no-current-mismatch')

    seed()
    initial = rows()
    from _qa_income_charity_edit import UI, load_functions
    from reporting import _prepare_report_data
    ui = UI()
    environment = {'pd': pd, 'hashlib': hashlib, 'st': ui,
                   '_CATEGORY_PAIR_COLUMN': 'category_subcategory', '_NO_SUBCATEGORY_LABEL': 'No subcategory',
                   'save_reviewed_rows': db.save_reviewed_rows, 'get_categories': db.get_categories,
                   '_executive_signed_amount_series': lambda frame: frame.amount_usd,
                   '_clear_transaction_read_caches': Mock()}
    functions = {'_category_pair_label', '_parse_category_pair_label', '_category_pair_options',
                 '_with_category_pair_column', '_editor_row_signature', '_scoped_editor_key',
                 '_save_income_charity_edits', '_render_income_charity_editor', '_render_income_charity_transactions'}
    load_functions(Path('app.py').read_text(encoding='utf-8'), functions, environment)
    frame = _prepare_report_data(db.get_all_transactions(), db.get_categories(include_subcategories=True), include_all_valid=True)[1].sort_values('id')
    ui.action = 'Save'
    def edit(display):
        display['category_subcategory'] = 'Income / Second'
        return display
    ui.transform = edit
    raced[0] = False
    with patch.object(db, 'get_connection', Connection):
        environment['_render_income_charity_transactions'](frame, editable=True)
    assert ui.errors == ['Could not save transaction edits: Income/Charity Save conflict predicates: AMOUNT']
    assert rows()[0] == initial[0]
    print(f'PASS {backend}: actual Income/Charity editor displays names only after rollback')

    if backend == 'PostgreSQL':
        seed()
        with closing(connect()) as conn:
            cur = conn.cursor()
            for column in ('amount', 'amount_usd', 'fx_rate'):
                cur.execute(f'ALTER TABLE classified_transactions ALTER COLUMN {column} TYPE DOUBLE PRECISION')
            cur.execute('UPDATE classified_transactions SET amount=?,amount_usd=?,fx_rate=?', (125.73, 125.73, 1.2345))
            conn.commit()
        initial = rows()
        try:
            db.save_reviewed_rows(payload.copy(), diagnose_income_charity_conflicts=True)
        except db.ConcurrentTransactionEditError as error:
            assert str(error) == 'Income/Charity Save conflict predicates: AMOUNT, AMOUNT_USD, FX_RATE'
        else:
            raise AssertionError('All failed numeric predicates must be reported without changing the guard')
        assert rows() == initial
        print('PASS PostgreSQL: all three numeric predicate failures reported; no financial changes')


def main():
    root = Path(os.environ['TEMP']).resolve()
    assert root.drive.upper() == 'E:'
    for key in list(os.environ):
        if any(part in key.upper() for part in ('DATABASE', 'POSTGRES', 'SUPABASE')) or key.upper().startswith('PG'):
            del os.environ[key]
    import db
    assert not db.USING_POSTGRES
    with tempfile.TemporaryDirectory(prefix='diagnostic-', dir=root) as folder:
        db.DB_PATH = str(Path(folder) / 'synthetic.sqlite')
        exercise(db, 'SQLite')
    import psycopg2
    expected = Path(os.environ['ARETI_QA_PG_DATA']).resolve()
    assert expected.is_relative_to(root)
    port = int(os.environ['ARETI_QA_PG_PORT'])
    admin = psycopg2.connect(host='127.0.0.1', port=port, user='qa_local', dbname='postgres', sslmode='disable')
    admin.autocommit = True
    database = 'qa_diagnostic_' + os.urandom(6).hex()
    try:
        with admin.cursor() as cur:
            cur.execute('SHOW data_directory')
            assert Path(cur.fetchone()[0]).resolve() == expected
            cur.execute('CREATE DATABASE ' + database)
        def connection():
            return db.PostgresConnection(psycopg2.connect(host='127.0.0.1', port=port, user='qa_local', dbname=database, sslmode='disable'))
        db.USING_POSTGRES = True
        db.get_connection = connection
        exercise(db, 'PostgreSQL')
    finally:
        with admin.cursor() as cur:
            cur.execute('DROP DATABASE ' + database)
        admin.close()
    for name in ('app.py', 'db.py'):
        without_income_save_diagnostic(name, Path(name).read_bytes())
    tree = ast.parse(Path('app.py').read_text(encoding='utf-8'))
    opted_in = [node.name for node in tree.body if isinstance(node, ast.FunctionDef)
                and any(isinstance(call, ast.Call) and any(keyword.arg == 'diagnose_income_charity_conflicts'
                    for keyword in call.keywords) for call in ast.walk(node))]
    assert opted_in == ['_save_income_charity_edits']
    print('PASS: exact application scope; only Income/Charity opts in')


if __name__ == '__main__':
    main()
