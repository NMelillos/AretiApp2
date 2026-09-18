"""Value-free, conflict-only Income diagnostics on isolated SQLite/PostgreSQL."""
import ast
from contextlib import closing, redirect_stdout, redirect_stderr
import hashlib
import io
import os
from pathlib import Path
import subprocess
import tempfile
from unittest.mock import patch

import pandas as pd

BASE = '06ade0bf725464b6228beb7b88372214d7e29408'
APPROVED = {'app.py': '81ea3ffcda16ac1fdf9a9d4bbeb5f53cd9c0c127e72be619ec21efcf53a41e90',
            'db.py': '2b35b5b8c4155fe3cb568dd54c2883bbb6eca363de1d15ff243569761b10ffa6'}


def without_conflict_diagnostic(name, source):
    source = source.replace(b'\r\n', b'\n')
    prior = subprocess.check_output(['git', 'show', BASE + ':' + name]).replace(b'\r\n', b'\n')
    if source == prior:
        return source
    assert hashlib.sha256(source).hexdigest() == APPROVED[name], 'Unreviewed diagnostic change'
    old, new = ast.parse(prior), ast.parse(source)
    class Restore(ast.NodeTransformer):
        def visit_FunctionDef(self, node):
            if node.name in {'_income_conflict_error', '_income_conflict_predicates'}:
                return None
            if node.name == 'save_reviewed_rows':
                node.args.kwonlyargs = []
                node.args.kw_defaults = []
            return self.generic_visit(node)
        def visit_If(self, node):
            if isinstance(node.test, ast.Name) and node.test.id == 'conflict_diagnostics':
                return None
            return self.generic_visit(node)
        def visit_Call(self, node):
            if isinstance(node.func, ast.Name) and node.func.id == 'save_reviewed_rows':
                node.keywords = [k for k in node.keywords if k.arg != 'conflict_diagnostics']
            return self.generic_visit(node)
    restored = Restore().visit(new)
    assert ast.dump(old) == ast.dump(restored), 'Non-diagnostic source changed'
    return prior


def exercise(db, backend):
    db.init_db()
    connect = db.get_connection
    for sub in ('Original', 'Target', 'Concurrent'):
        db.add_category('Income', sub, 'Income')
    def seed():
        with closing(connect()) as conn:
            cur = conn.cursor()
            cur.execute('DELETE FROM classified_transactions')
            for identity in (901, 902):
                cur.execute('''INSERT INTO classified_transactions
                    (id,row_hash,txn_date,category,subcategory,reviewed,status,amount,amount_usd,currency,fx_rate)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?)''',
                    (identity, 'synthetic-'+str(identity), '2026-01-01', 'Income', 'Original', 1,
                     'reviewed', 81.125, 82.25, 'USD', 1.25))
            conn.commit()
    def raw():
        with closing(connect()) as conn:
            cur = conn.cursor()
            cur.execute('SELECT * FROM classified_transactions ORDER BY id')
            return cur.fetchall()
    def payload():
        return pd.DataFrame([dict(id=i, category='Income', subcategory='Target', reviewed=True,
                                  _expected_category='Income', _expected_subcategory='Original',
                                  _expected_reviewed=True) for i in (901, 902)])

    class Cursor:
        def __init__(self, cur): self.cur = cur
        def __getattr__(self, name): return getattr(self.cur, name)
        def execute(self, sql, params=None):
            diagnostic = sql.startswith('SELECT category IS NOT DISTINCT FROM')
            if diagnostic:
                seen.append('diagnostic')
                if diagnostic_error:
                    raise RuntimeError('SYNTHETIC_PRIVATE_DRIVER_VALUE')
            if 'UPDATE classified_transactions' in sql and 'IS NOT DISTINCT FROM' in sql and params[8] == 902 and not raced[0]:
                raced[0] = True
                def change(cur):
                    if remove:
                        cur.execute('DELETE FROM classified_transactions WHERE id=?', (902,))
                    for field, value in changes.items():
                        cur.execute(f'UPDATE classified_transactions SET {field}=? WHERE id=?', (value, 902))
                if backend == 'SQLite':
                    change(self.cur)
                else:
                    with closing(connect()) as other:
                        change(other.cursor())
                        other.commit()
            return self.cur.execute(sql, params) if params is not None else self.cur.execute(sql)
    class Connection:
        def __init__(self): self.conn = connect()
        def __getattr__(self, name): return getattr(self.conn, name)
        def cursor(self): return Cursor(self.conn.cursor())

    values = dict(category='SYNTHETIC_PRIVATE_CATEGORY', subcategory='SYNTHETIC_PRIVATE_SUBCATEGORY',
                  reviewed=0, status='SYNTHETIC_PRIVATE_STATUS', amount=83.125,
                  amount_usd=84.25, currency='SYNTHETIC_PRIVATE_CURRENCY', fx_rate=1.5)
    cases = [({field: value}, False, False, [field.upper()]) for field, value in values.items()]
    cases += [(dict(amount=None, amount_usd=None, fx_rate=None), False, False, ['AMOUNT', 'AMOUNT_USD', 'FX_RATE']),
              (dict(category=values['category'], subcategory=values['subcategory']), False, False, ['CATEGORY', 'SUBCATEGORY']),
              ({}, True, False, ['ROW_NOT_FOUND']),
              ({'amount': 83.125}, False, True, ['UNKNOWN'])]
    for changes, remove, diagnostic_error, expected in cases:
        seed()
        before = raw()
        raced, seen = [False], []
        output = io.StringIO()
        with patch.object(db, 'get_connection', Connection), redirect_stdout(output), redirect_stderr(output):
            try:
                db.save_reviewed_rows(payload(), conflict_diagnostics=True)
            except db.ConcurrentTransactionEditError as error:
                message = str(error)
            else:
                raise AssertionError('Conflict was not rejected')
        expected_message = str(db.ConcurrentTransactionEditError(902)) + ' Conflict predicates: ' + ', '.join(expected) + '.'
        assert message == expected_message, 'Diagnostic exposed non-whitelisted output'
        assert output.getvalue() == '', 'Diagnostic logged output'
        assert seen == ['diagnostic'], 'Diagnostic must run exactly once after the failed UPDATE'
        after = raw()
        assert after[0] == before[0], 'Earlier batch row did not roll back'
        if backend == 'SQLite':
            assert after == before, 'Failure did not roll back whole transaction'
        else:
            assert len(after) == (1 if remove else 2)
            if not remove:
                columns = list(db.get_all_transactions().columns)
                assert after[1][columns.index('subcategory')] != 'Target', 'Classification escaped rollback'
    print('PASS', backend, 'all eight SQL predicates, multiple failures, NULL, missing row, private driver error, atomic rollback')

    for enabled in (False, True):
        seed()
        changes, remove, diagnostic_error = {}, False, False
        raced, seen = [False], []
        with patch.object(db, 'get_connection', Connection):
            assert db.save_reviewed_rows(payload(), conflict_diagnostics=enabled) == 2
        assert seen == [], 'Normal Save issued a diagnostic query'
    for field, value, expected in [('_expected_category', 'SYNTHETIC_PRIVATE_CATEGORY', 'CATEGORY'),
                                   ('_expected_subcategory', 'SYNTHETIC_PRIVATE_SUBCATEGORY', 'SUBCATEGORY'),
                                   ('_expected_reviewed', False, 'REVIEWED')]:
        seed()
        before = raw()
        frame = payload()
        frame.loc[1, field] = value
        try:
            db.save_reviewed_rows(frame, conflict_diagnostics=True)
        except db.ConcurrentTransactionEditError as error:
            assert str(error) == str(db.ConcurrentTransactionEditError(902)) + ' Conflict predicates: ' + expected + '.'
        else:
            raise AssertionError('Stale UI snapshot accepted')
        assert raw() == before
    # Non-Income callers keep their existing conflict messages and query count.
    seed()
    changes, remove, diagnostic_error = {'amount': 83.125}, False, False
    raced, seen = [False], []
    with patch.object(db, 'get_connection', Connection):
        try:
            db.save_reviewed_rows(payload())
        except db.ConcurrentTransactionEditError as error:
            assert str(error) == str(db.ConcurrentTransactionEditError(902))
        else:
            raise AssertionError('Genuine conflict accepted')
    assert seen == []
    for null in (None, float('nan'), pd.NA):
        assert str(db._income_conflict_error(901, [null])) == str(db.ConcurrentTransactionEditError(901)) + ' Conflict predicates: UNKNOWN.'
    print('PASS', backend, 'success has zero diagnostics; UI stale checks; non-Income behavior; privacy')


def main():
    root = Path(os.environ['TEMP']).resolve()
    assert root.drive.upper() == 'E:'
    for key in list(os.environ):
        if any(p in key.upper() for p in ('DATABASE', 'POSTGRES', 'SUPABASE')) or key.upper().startswith('PG'):
            del os.environ[key]
    import db
    with tempfile.TemporaryDirectory(dir=root) as folder, patch.object(db, 'DB_PATH', str(Path(folder)/'test.sqlite')):
        exercise(db, 'SQLite')
    import psycopg2
    kwargs = dict(host='127.0.0.1', port=int(os.environ['ARETI_QA_PG_PORT']), user='qa_local', sslmode='disable')
    admin = psycopg2.connect(dbname='postgres', **kwargs)
    admin.autocommit = True
    name = 'qa_diagnostic_' + os.urandom(6).hex()
    try:
        with admin.cursor() as cur:
            cur.execute('SHOW data_directory')
            assert Path(cur.fetchone()[0]).resolve() == Path(os.environ['ARETI_QA_PG_DATA']).resolve()
            cur.execute('CREATE DATABASE ' + name)
        try:
            def connect(): return db.PostgresConnection(psycopg2.connect(dbname=name, **kwargs))
            with patch.object(db, 'get_connection', connect), patch.object(db, 'USING_POSTGRES', True):
                exercise(db, 'PostgreSQL')
        finally:
            with admin.cursor() as cur: cur.execute('DROP DATABASE ' + name)
    finally:
        admin.close()
    for name in ('app.py', 'db.py'):
        without_conflict_diagnostic(name, Path(name).read_bytes())


if __name__ == '__main__':
    main()
