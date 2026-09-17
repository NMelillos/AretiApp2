"""Exact USD snapshot comparisons across isolated numeric-storage backends."""
import ast
from contextlib import closing
from decimal import Decimal
import hashlib
import math
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from unittest.mock import Mock, patch

import numpy as np
import pandas as pd

BASELINE = 'fe83af56a318ba984f2c7e4eb6a9842d0bde66f6'
NORMAL_BASELINE = '3f8c9df876f973643bf9afc6922b42093b136778'
USD_EXPRESSION = b'''    # Convert the snapshot to the actual column type, without a tolerance.
    usd_snapshot = (
        "(json_populate_record(NULL::classified_transactions, "
        "json_build_object('amount_usd', CAST(? AS TEXT)))).amount_usd"
        if USING_POSTGRES else "?"
    )
'''


def without_income_save_usd(name, source):
    from _qa_income_save_types import without_remaining_save
    source = without_remaining_save(name, source)
    source = source.replace(b'\r\n', b'\n')
    if name not in ('app.py', 'db.py'):
        return source
    baseline = subprocess.check_output(['git', 'show', NORMAL_BASELINE + ':' + name]).replace(b'\r\n', b'\n')
    if source == baseline:
        return source
    assert name == 'db.py', 'Income editor must match its normal pre-diagnostic implementation'
    replacements = ((USD_EXPRESSION, b''),
                    (b'cur.execute(f"""\n                UPDATE classified_transactions\n', b'cur.execute("""\n                UPDATE classified_transactions\n'),
                    (b'AND amount_usd IS NOT DISTINCT FROM {usd_snapshot}', b'AND amount_usd IS NOT DISTINCT FROM CAST(? AS REAL)'),
                    (b'                before[9] if not amount_changed else amount_usd,\n', b'                amount_usd,\n'))
    for new, old in replacements:
        assert source.count(new) == 1
        source = source.replace(new, old, 1)
    assert source == baseline, 'Change beyond the exact USD comparison and lossless unchanged binding'
    return source


def exercise(db, kind, baseline_only=False):
    db.init_db()
    connect = db.get_connection
    if kind not in ('SQLite', 'REAL'):
        assert kind in ('DOUBLE PRECISION', 'NUMERIC', 'NUMERIC(30,18)')
        with closing(connect()) as conn:
            conn.cursor().execute(f'ALTER TABLE classified_transactions ALTER COLUMN amount_usd TYPE {kind}')
            conn.commit()
    for category, sub, group in [('Income', 'First', 'Income'), ('Income', 'Second', 'Income'),
                                 ('Other Income', 'Alternative', 'Income'), ('Charity', 'General', 'Family expenses')]:
        db.add_category(category, sub, group)

    def seed(usd=None):
        with closing(connect()) as conn:
            cur = conn.cursor()
            cur.execute('DELETE FROM classified_transactions')
            for identity, category, sub, amount, value in [(801,'Income','First',100,125.73),
                    (802,'Income','First',200,234.91), (803,'Charity','General',-30,-36.064)]:
                cur.execute('''INSERT INTO classified_transactions
                    (id,row_hash,txn_date,amount,amount_usd,fx_rate,currency,category,subcategory,
                     original_description,account_name,reviewed,status)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                    (identity, f'synthetic-{identity}', '2026-01-10', amount,
                     usd if identity == 801 and usd is not None else value, 1,
                     'USD', category, sub, 'Synthetic fixture', 'Synthetic holder', 1, 'reviewed'))
            conn.commit()

    def raw():
        with closing(connect()) as conn:
            cur = conn.cursor()
            cur.execute('SELECT id,category,subcategory,amount,amount_usd,fx_rate,currency,txn_date,original_description,account_name,reviewed,status FROM classified_transactions ORDER BY id')
            return cur.fetchall()

    def payload(ids=(801,)):
        return pd.DataFrame([{'id': identity, 'category': 'Other Income', 'subcategory': 'Alternative',
                              'reviewed': True, '_expected_category': 'Income',
                              '_expected_subcategory': 'First', '_expected_reviewed': True} for identity in ids])

    seed()
    initial = raw()
    baseline_tree = ast.parse(subprocess.check_output(['git', 'show', BASELINE + ':db.py']).decode())
    node = next(node for node in baseline_tree.body if isinstance(node, ast.FunctionDef) and node.name == 'save_reviewed_rows')
    environment = db.__dict__.copy()
    exec(compile(ast.Module(body=[node], type_ignores=[]), '<verified diagnostic baseline>', 'exec'), environment)
    if kind not in ('SQLite', 'REAL'):
        with closing(connect()) as conn:
            cur = conn.cursor()
            fields = ('category','subcategory','reviewed','status','amount','amount_usd','currency','fx_rate')
            cur.execute('SELECT ' + ','.join(fields) + ' FROM classified_transactions WHERE id=?', (801,))
            values = cur.fetchone()
            predicates = ','.join(field + ' IS NOT DISTINCT FROM ' + ('CAST(? AS REAL)' if field in ('amount','amount_usd','fx_rate') else '?') for field in fields)
            cur.execute('SELECT ' + predicates + ' FROM classified_transactions WHERE id=?', (*values, 801))
            assert [field for field, matched in zip(fields, cur.fetchone()) if not matched] == ['amount_usd']
        try:
            environment['save_reviewed_rows'](payload())
        except db.ConcurrentTransactionEditError:
            pass
        else:
            raise AssertionError('Untouched diagnostic baseline must reproduce the AMOUNT_USD-only conflict')
        assert raw() == initial
        print(f'PASS {kind}: untouched baseline fails only AMOUNT_USD; zero writes')
    if baseline_only:
        return

    assert db.save_reviewed_rows(payload()) == 1
    after = raw()
    assert after[0][1:3] == ('Other Income', 'Alternative')
    assert [row[3:] for row in after] == [row[3:] for row in initial]
    assert after[1:] == initial[1:]
    from reporting import _prepare_report_data, income_charity_month_values, income_charity_percentage
    month = pd.Period('2026-01', freq='M')
    def report():
        return _prepare_report_data(db.get_all_transactions(), db.get_categories(include_subcategories=True), include_all_valid=True)[1]
    scoped, monthly, _ = income_charity_month_values(report(), [month])
    assert scoped.id.nunique() == len(scoped) == 3
    assert monthly['Income'][month] == Decimal('360.64')
    assert monthly['Charity'][month] == Decimal('-36.064')
    assert income_charity_percentage(monthly['Income'][month], monthly['Charity'][month]) == Decimal('10')
    print(f'PASS {kind}: unchanged USD saves; fresh connection, stable IDs, exact totals/ratio and inclusion')

    # Independently committed modifications between reread and UPDATE remain exact conflicts.
    tiny_original = 0.015625 if kind == 'REAL' else Decimal('125.73') if kind.startswith('NUMERIC') else 125.73
    tiny_new = float(np.nextafter(np.float32(tiny_original), np.float32(math.inf))) if kind == 'REAL' else tiny_original + Decimal('0.000000000000000001') if kind.startswith('NUMERIC') else math.nextafter(tiny_original, math.inf)
    cases = [(field, value, (801,)) for field, value in [
        ('category','Concurrent Income'), ('subcategory','Concurrent child'),
        ('reviewed', None), ('status', 'pending'), ('amount', 150),
        ('amount_usd', tiny_new), ('currency', 'EUR'), ('fx_rate', 2), ('delete', None)]]
    cases.append(('amount_usd', tiny_new, (801,802)))
    for field, value, identities in cases:
        seed(tiny_original)
        initial = raw()
        raced = [False]
        class Cursor:
            def __init__(self, cursor):
                self.cursor = cursor
            def __getattr__(self, name):
                return getattr(self.cursor, name)
            def execute(self, sql, params=None):
                if 'UPDATE classified_transactions' in sql and 'IS NOT DISTINCT FROM' in sql and params[8] == identities[-1] and not raced[0]:
                    raced[0] = True
                    mutation = 'DELETE FROM classified_transactions WHERE id=?' if field == 'delete' else f'UPDATE classified_transactions SET {field}=? WHERE id=?'
                    values = (identities[-1],) if field == 'delete' else (value, identities[-1])
                    if kind == 'SQLite' and len(identities) > 1:
                        # SQLite serializes writers once an earlier batch row is updated.
                        self.cursor.execute(mutation, values)
                    else:
                        with closing(connect()) as other:
                            other.cursor().execute(mutation, values)
                            other.commit()
                return self.cursor.execute(sql, params) if params is not None else self.cursor.execute(sql)
        class Connection:
            def __init__(self):
                self.conn = connect()
            def __getattr__(self, name):
                return getattr(self.conn, name)
            def cursor(self):
                return Cursor(self.conn.cursor())
        with patch.object(db, 'get_connection', Connection):
            try:
                db.save_reviewed_rows(payload(identities))
            except db.ConcurrentTransactionEditError as error:
                assert str(error) == f'Transaction ID {identities[-1]} changed in another tab or session. Reload the report and apply the edit again.'
            else:
                raise AssertionError('A genuine intervening edit must reject Save without tolerance')
        latest = raw()
        if kind == 'SQLite' and len(identities) > 1:
            assert latest == initial
        elif field == 'delete':
            assert latest == [row for row in initial if row[0] != identities[-1]]
        else:
            column = {'category':1,'subcategory':2,'amount':3,'amount_usd':4,'fx_rate':5,
                      'currency':6,'reviewed':10,'status':11}[field]
            changed = next(row for row in latest if row[0] == identities[-1])
            previous = next(row for row in initial if row[0] == identities[-1])
            if kind == 'REAL' and field == 'amount_usd':
                assert np.float32(changed[column]).tobytes() == np.float32(value).tobytes()
            else:
                assert changed[column] == value
            assert [row for row in latest if row[0] != identities[-1]] == [row for row in initial if row[0] != identities[-1]]
            for index in range(len(previous)):
                if index != column:
                    assert changed[index] == previous[index]
        print(f'PASS {kind}: concurrent {field} change retained; normal conflict; no overwrite')

    seed()
    initial = raw()
    stale = payload((801,802))
    stale.loc[1, '_expected_subcategory'] = 'Old selection'
    try:
        db.save_reviewed_rows(stale)
    except db.ConcurrentTransactionEditError:
        pass
    else:
        raise AssertionError('A stale second row must roll back the entire batch')
    assert raw() == initial
    from _qa_income_charity_edit import UI, Rerun, load_functions
    ui = UI()
    env = {'pd': pd, 'hashlib': hashlib, 'st': ui, '_CATEGORY_PAIR_COLUMN':'category_subcategory',
           '_NO_SUBCATEGORY_LABEL':'No subcategory', 'save_reviewed_rows':db.save_reviewed_rows,
           'get_categories':db.get_categories, '_executive_signed_amount_series':lambda frame: frame.amount_usd,
           '_clear_transaction_read_caches':Mock()}
    functions = {'_category_pair_label','_parse_category_pair_label','_category_pair_options','_with_category_pair_column',
                 '_editor_row_signature','_scoped_editor_key','_save_income_charity_edits','_render_income_charity_editor','_render_income_charity_transactions'}
    load_functions(Path('app.py').read_text(encoding='utf-8'), functions, env)
    frame = report().query('id == 801')
    ui.transform = lambda display: display.assign(category_subcategory='Other Income / Alternative')
    for action in ('Cancel','Save'):
        ui.action = action
        try:
            env['_render_income_charity_transactions'](frame, editable=True)
        except Rerun:
            pass
        assert not ui.errors
        if action == 'Cancel':
            assert raw() == initial
    assert raw()[0][1:3] == ('Other Income','Alternative')
    assert [row[3:] for row in raw()] == [row[3:] for row in initial]
    print(f'PASS {kind}: batch rollback, actual editor Cancel/Save, rerun and persistence')

    for value in (0, 2, None):
        seed()
        with closing(connect()) as conn:
            conn.cursor().execute('UPDATE classified_transactions SET amount_usd=? WHERE id=?', (value,801))
            conn.commit()
        before = raw()
        assert db.save_reviewed_rows(payload()) == 1
        assert raw()[0][4] == before[0][4]
    if kind.startswith('NUMERIC'):
        precise = Decimal('125.730000000000000001')
        seed(precise)
        assert db.save_reviewed_rows(payload()) == 1
        assert raw()[0][4] == precise, 'Classification-only Save must not coerce unchanged NUMERIC through float'
    print(f'PASS {kind}: NULL/integer values and lossless numeric storage')


def main():
    root = Path(os.environ['TEMP']).resolve()
    assert root.drive.upper() == 'E:'
    for key in list(os.environ):
        if any(part in key.upper() for part in ('DATABASE','POSTGRES','SUPABASE')) or key.upper().startswith('PG'):
            del os.environ[key]
    import db
    assert not db.USING_POSTGRES
    baseline_only = '--baseline' in sys.argv
    with tempfile.TemporaryDirectory(prefix='usd-save-', dir=root) as folder:
        db.DB_PATH = str(Path(folder) / 'synthetic.sqlite')
        exercise(db, 'SQLite', baseline_only)
    import psycopg2
    expected = Path(os.environ['ARETI_QA_PG_DATA']).resolve()
    assert expected.is_relative_to(root)
    port = int(os.environ['ARETI_QA_PG_PORT'])
    admin = psycopg2.connect(host='127.0.0.1', port=port, user='qa_local', dbname='postgres', sslmode='disable')
    admin.autocommit = True
    try:
        with admin.cursor() as cur:
            cur.execute('SHOW data_directory')
            assert Path(cur.fetchone()[0]).resolve() == expected
        for kind in ('REAL','DOUBLE PRECISION','NUMERIC','NUMERIC(30,18)'):
            name = 'qa_usd_' + os.urandom(6).hex()
            with admin.cursor() as cur:
                cur.execute('CREATE DATABASE ' + name)
            try:
                def connect():
                    return db.PostgresConnection(psycopg2.connect(host='127.0.0.1', port=port, user='qa_local', dbname=name, sslmode='disable'))
                db.USING_POSTGRES = True
                db.get_connection = connect
                exercise(db, kind, baseline_only)
            finally:
                with admin.cursor() as cur:
                    cur.execute('DROP DATABASE ' + name)
    finally:
        admin.close()
    if not baseline_only:
        for name in ('app.py','db.py'):
            source = Path(name).read_bytes()
            without_income_save_usd(name, source)
            assert b'diagnose_income_charity_conflicts' not in source
        assert not Path('income_save_diagnostic.py').exists()
        print('PASS exact USD-only correction, normal editor, temporary diagnostic removed')


if __name__ == '__main__':
    main()
