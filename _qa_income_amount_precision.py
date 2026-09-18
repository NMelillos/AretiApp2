"""Permanent synthetic regression for lossless Income classification snapshots."""
import ast
from contextlib import closing
from decimal import Decimal
import hashlib
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import types
from unittest.mock import patch

import pandas as pd

BASE = 'fc19d066b505e4ce79cfb4c8d7f815b1ebc28f0c'
APPROVED = {'app.py': 'db25354642d212a331c60cec3d29df813d763b16b9519f334daed4fd8f155fc5',
            'db.py': '2b711b72bea6d4efc927ee3016bda703a35bba51cc3ab440b5b86a8eba490f21'}


def diagnostic_baseline():
    module = types.ModuleType('diagnostic_baseline')
    module.__file__ = str(Path('db.py').resolve())
    exec(compile(subprocess.check_output(['git', 'show', BASE + ':db.py']), module.__file__, 'exec'), module.__dict__)
    return module


def without_precision_fix(name, source):
    source = source.replace(b'\r\n', b'\n')
    prior = subprocess.check_output(['git', 'show', BASE + ':' + name]).replace(b'\r\n', b'\n')
    if source == prior:
        return source
    assert hashlib.sha256(source).hexdigest() == APPROVED[name], 'Unreviewed precision correction'
    old, new = ast.parse(prior), ast.parse(source)
    changed = {'_save_income_charity_edits', '_render_income_charity_editor'} if name == 'app.py' else {
        'save_reviewed_rows', '_income_conflict_error', '_income_conflict_predicates',
        'get_income_row_versions', '_income_amount_representation', '_income_conflict_details'}
    def protected(tree):
        tree.body = [n for n in tree.body if not isinstance(n, ast.FunctionDef) or n.name not in changed]
        return ast.dump(tree)
    assert protected(old) == protected(new), 'Protected functionality changed'
    return prior


def exercise(db, connect, *, baseline=False, postgres=True):
    db.init_db()
    for sub in ('Original', 'Target', 'Concurrent'):
        db.add_category('Income', sub, 'Income')
    values = ['12345.6', '-12345.6', '48231.47', '-48231.47', '9876543.21',
              '-9876543.21', '0.123456789', '-0.123456789', '125.5']
    def seed(value):
        with closing(connect()) as conn:
            cur = conn.cursor()
            cur.execute('DELETE FROM classified_transactions')
            for identity, val in ((701, '125.5'), (702, value)):
                cur.execute('''INSERT INTO classified_transactions
                    (id,row_hash,txn_date,category,subcategory,reviewed,status,amount,amount_usd,
                     fx_rate,currency,original_description,account_name)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                    (identity, 'synthetic-' + str(identity), '2026-01-02', 'Income', 'Original',
                     1, 'reviewed', Decimal(val) if postgres else float(val), 120, 1, 'USD',
                     'SYNTHETIC', 'SYNTHETIC'))
            conn.commit()
    def payload(ids=(701, 702)):
        return pd.DataFrame([dict(id=i, category='Income', subcategory='Target', reviewed=True,
                                 _expected_category='Income', _expected_subcategory='Original',
                                 _expected_reviewed=True) for i in ids])
    def stored():
        with closing(connect()) as conn:
            cur = conn.cursor()
            extra = ", encode(float4send(amount), 'hex')" if postgres else ''
            cur.execute('SELECT *' + extra + ' FROM classified_transactions ORDER BY id')
            return cur.fetchall(), [c[0] for c in cur.description]
    def save(frame):
        return db.save_reviewed_rows(frame, **({'conflict_diagnostics': True} if baseline else {'income_edit': True}))
    failures = 0
    for value in values:
        for ids in ((702,), (701, 702)):
            seed(value)
            before, columns = stored()
            lossy = False
            if postgres:
                with closing(connect()) as conn:
                    cur = conn.cursor()
                    cur.execute('SELECT amount IS DISTINCT FROM amount::text::real FROM classified_transactions WHERE id=?', (702,))
                    lossy = cur.fetchone()[0]
            try:
                assert save(payload(ids)) == len(ids)
            except db.ConcurrentTransactionEditError as exc:
                assert baseline and lossy, 'Corrected unchanged snapshot rejected'
                assert str(exc).endswith('Conflict predicates: AMOUNT.')
                assert stored()[0] == before, 'Baseline failure escaped atomic rollback'
                failures += 1
                continue
            assert not (baseline and lossy), 'Baseline reproduction did not fail'
            after, _ = stored()
            permitted = {'subcategory', 'reviewed_at'}
            for old, new in zip(before, after):
                for index, column in enumerate(columns):
                    if column not in permitted:
                        assert old[index] == new[index], 'Protected stored value changed: ' + column
                assert new[columns.index('subcategory')] == ('Target' if new[0] in ids else 'Original')
            assert len(after) == 2
    if baseline:
        assert failures >= 12, 'Expected signed, large and fractional baseline failures'
        print('PASS untouched baseline: only AMOUNT conflicts, unchanged rows, exact controls, single/batch rollback:', failures)
        return

    # Inject changes after the raw snapshot but before its guarded UPDATE.
    for field, value in [('amount', 125.50000762939453), ('category', 'Concurrent'),
                         ('subcategory', 'Concurrent')]:
        seed('125.5')
        before, _ = stored()
        raced = [False]
        queries = []
        class Cursor:
            def __init__(self, cur): self.cur = cur
            def __getattr__(self, name): return getattr(self.cur, name)
            def execute(self, sql, params=None):
                if 'UPDATE classified_transactions' in sql and 'IS NOT DISTINCT FROM' in sql:
                    queries.append(sql)
                    assert 'amount =' not in sql.split('WHERE')[0]
                    assert 'amount_usd =' not in sql.split('WHERE')[0]
                    if params[6] == 702 and not raced[0]:
                        raced[0] = True
                        if postgres:
                            with closing(connect()) as other:
                                other.cursor().execute(f'UPDATE classified_transactions SET {field}=? WHERE id=?', (value, 702))
                                other.commit()
                        else:
                            self.cur.execute(f'UPDATE classified_transactions SET {field}=? WHERE id=?', (value, 702))
                return self.cur.execute(sql, params) if params is not None else self.cur.execute(sql)
        class Connection:
            def __init__(self): self.conn = connect()
            def __getattr__(self, name): return getattr(self.conn, name)
            def cursor(self): return Cursor(self.conn.cursor())
        with patch.object(db, 'get_connection', Connection):
            try:
                save(payload())
            except db.ConcurrentTransactionEditError as exc:
                assert str(exc) == str(db.ConcurrentTransactionEditError(702)), 'Technical diagnostic leaked'
            else:
                raise AssertionError('Real concurrent change accepted')
        assert raced[0] and len(queries) == 2
        assert stored()[0][0] == before[0], 'First row did not roll back'

    # The same pooled connection must return to its original precision after both outcomes.
    if postgres:
        for fail in (False, True):
            seed('48231.47')
            connection = connect()
            class Reused:
                def __getattr__(self, name): return getattr(connection, name)
                def close(self): pass
            frame = payload()
            if fail:
                frame.loc[1, '_expected_subcategory'] = 'Concurrent'
            try:
                with patch.object(db, 'get_connection', lambda: Reused()):
                    try:
                        save(frame)
                    except db.ConcurrentTransactionEditError:
                        assert fail
                    else:
                        assert not fail
                cur = connection.cursor()
                cur.execute('SHOW extra_float_digits')
                assert cur.fetchone()[0] == '0', 'Transaction-local setting leaked'
                connection.rollback()
            finally:
                connection.close()
    print('PASS', 'PostgreSQL' if postgres else 'SQLite', 'exact financial storage, smallest REAL change, classification conflicts, rollback, precision reset')


def main():
    root = Path(os.environ['TEMP']).resolve()
    assert root.drive.upper() == 'E:'
    for key in list(os.environ):
        if any(p in key.upper() for p in ('DATABASE', 'POSTGRES', 'SUPABASE')) or key.upper().startswith('PG'):
            del os.environ[key]
    import db
    import psycopg2
    kwargs = dict(host='127.0.0.1', port=int(os.environ['ARETI_QA_PG_PORT']), user='qa_local', sslmode='disable')
    admin = psycopg2.connect(dbname='postgres', **kwargs)
    admin.autocommit = True
    try:
        with admin.cursor() as cur:
            cur.execute('SHOW data_directory')
            assert Path(cur.fetchone()[0]).resolve() == Path(os.environ['ARETI_QA_PG_DATA']).resolve()
        for baseline in (True,) if '--baseline' in sys.argv else (True, False):
            module = diagnostic_baseline() if baseline else db
            name = 'qa_final_precision_' + os.urandom(6).hex()
            with admin.cursor() as cur: cur.execute('CREATE DATABASE ' + name)
            try:
                def connect():
                    return module.PostgresConnection(psycopg2.connect(dbname=name, options='-c extra_float_digits=0', **kwargs))
                with patch.object(module, 'get_connection', connect), patch.object(module, 'USING_POSTGRES', True):
                    exercise(module, connect, baseline=baseline)
            finally:
                with admin.cursor() as cur: cur.execute('DROP DATABASE ' + name)
    finally:
        admin.close()
    if '--baseline' not in sys.argv:
        with tempfile.TemporaryDirectory(dir=root) as folder, patch.object(db, 'DB_PATH', str(Path(folder)/'test.sqlite')):
            exercise(db, db.get_connection, postgres=False)
        for name in ('app.py', 'db.py'):
            without_precision_fix(name, Path(name).read_bytes())
        source = Path('db.py').read_text(encoding='utf-8') + Path('app.py').read_text(encoding='utf-8')
        for label in ('Conflict predicates:', 'ROW_VERSION_CHANGED=', 'AMOUNT_DB_TYPE=', 'DRIVER_TYPE=',
                      'SERVER_TEXT_ROUNDTRIP=', 'DRIVER_ROUNDTRIP=', 'AMOUNT_CAUSE='):
            assert label not in source, 'Temporary diagnostic remains'


if __name__ == '__main__':
    main()
