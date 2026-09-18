"""Synthetic conflict-only version/representation diagnostics; no customer data."""
import ast
from contextlib import closing, redirect_stdout, redirect_stderr
from decimal import Decimal
import hashlib
import io
import os
from pathlib import Path
import re
import subprocess
import tempfile
from unittest.mock import patch, Mock

import pandas as pd

BASE = 'd1a6886045e8a0b0ca903b09b03e8817ed00dd41'
APPROVED = {'app.py': '741ca4cc4232effda5019db3001599140a62899df6aac9cf6eda6516666bd5d8',
            'db.py': '34519f9bacd94eddfb5b8fca16a1f955b37b29ae21b6d3a3ae19fbda3429b001'}


def without_amount_representation(name, source):
    from _qa_income_amount_precision import without_precision_fix
    source = without_precision_fix(name, source)
    source = source.replace(b'\r\n', b'\n')
    prior = subprocess.check_output(['git', 'show', BASE + ':' + name]).replace(b'\r\n', b'\n')
    if source == prior:
        return source
    assert hashlib.sha256(source).hexdigest() == APPROVED[name], 'Unreviewed representation diagnostic'
    old, new = ast.parse(prior), ast.parse(source)
    replaced = {'_save_income_charity_edits', '_render_income_charity_editor'} if name == 'app.py' else {'save_reviewed_rows'}
    added = {'get_income_row_versions', '_income_amount_representation', '_income_conflict_details'} if name == 'db.py' else set()
    originals = {node.name: node for node in old.body if isinstance(node, ast.FunctionDef)}
    new.body = [originals[n.name] if isinstance(n, ast.FunctionDef) and n.name in replaced else n
                for n in new.body if not isinstance(n, ast.FunctionDef) or n.name not in added]
    assert ast.dump(old) == ast.dump(new), 'Protected code changed'
    return prior


def exercise(db, kind, precision):
    db.init_db()
    connect = db.get_connection
    if kind != 'SQLite':
        with closing(connect()) as conn:
            conn.cursor().execute(f'ALTER TABLE classified_transactions ALTER COLUMN amount TYPE {kind}')
            conn.commit()
    db.add_category('Income', 'First', 'Income')
    db.add_category('Income', 'Next', 'Income')
    def seed(value='48123.46'):
        with closing(connect()) as conn:
            cur = conn.cursor()
            cur.execute('DELETE FROM classified_transactions')
            for identity, amount in [(801, '125.5'), (802, value)]:
                cur.execute('''INSERT INTO classified_transactions
                    (id,row_hash,txn_date,category,subcategory,reviewed,status,amount,amount_usd,fx_rate,currency)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?)''',
                    (identity, 'synthetic-'+str(identity), '2026-01-01', 'Income', 'First', 1, 'reviewed',
                     float(amount) if kind == 'SQLite' else Decimal(amount), 100, 1, 'USD'))
            conn.commit()
    def raw():
        with closing(connect()) as conn:
            cur = conn.cursor()
            cur.execute('SELECT * FROM classified_transactions ORDER BY id')
            return cur.fetchall()
    def payload():
        return pd.DataFrame([dict(id=i, category='Income', subcategory='Next', reviewed=True,
                                  _expected_category='Income', _expected_subcategory='First', _expected_reviewed=True)
                             for i in (801, 802)])
    def amount():
        with closing(connect()) as conn:
            cur = conn.cursor()
            cur.execute('SELECT amount FROM classified_transactions WHERE id=?', (802,))
            return cur.fetchone()[0]
    def detail(version):
        with closing(connect()) as conn:
            return db._income_amount_representation(conn.cursor(), 802, amount(), version)
    seed()
    versions = db.get_income_row_versions([801, 802])
    before = raw()
    if kind == 'SQLite':
        assert versions == {}
        with patch.object(db, '_income_amount_representation', side_effect=AssertionError('PostgreSQL metadata on SQLite')):
            assert db.save_reviewed_rows(payload(), conflict_diagnostics=True, row_versions={}) == 2
        print('PASS SQLite support without PostgreSQL metadata')
        return

    assert set(versions) == {801, 802}
    result = detail(versions[802])
    assert 'ROW_VERSION_CHANGED=NO' in result
    assert 'AMOUNT_DB_TYPE=' + kind.replace(' ', '_') in result
    assert 'DRIVER_TYPE=' + ('DECIMAL' if kind == 'NUMERIC' else 'FLOAT') in result
    assert 'EXTRA_FLOAT_DIGITS=' + str(precision) in result
    lossy = precision == 0 and kind == 'REAL'
    assert 'SERVER_TEXT_ROUNDTRIP=' + ('FAIL' if lossy else 'PASS') in result
    assert 'DRIVER_ROUNDTRIP=' + ('FAIL' if lossy else 'PASS') in result
    if not lossy:
        assert 'AMOUNT_CAUSE=UNRESOLVED' in result
        with patch.object(db, '_income_amount_representation', side_effect=AssertionError('Diagnostic on successful Save')):
            assert db.save_reviewed_rows(payload(), conflict_diagnostics=True, row_versions=versions) == 2
    else:
        stream = io.StringIO()
        with redirect_stdout(stream), redirect_stderr(stream):
            try:
                db.save_reviewed_rows(payload(), conflict_diagnostics=True, row_versions=versions)
            except db.ConcurrentTransactionEditError as error:
                assert str(error) == str(db._income_conflict_error(802, ['AMOUNT'])) + ' ' + result
            else:
                raise AssertionError('Forced lossy conflict bypassed')
        assert stream.getvalue() == ''
        assert raw() == before, 'Atomic rollback failed'
    assert not any(token in result for token in (str(versions[802]), '48123', 'Income', 'First', 'synthetic', 'SELECT'))
    allowed = r'ROW_VERSION_CHANGED=(YES|NO|UNKNOWN); AMOUNT_DB_TYPE=(REAL|DOUBLE_PRECISION|NUMERIC|OTHER); DRIVER_TYPE=(FLOAT|DECIMAL|OTHER); EXTRA_FLOAT_DIGITS=-?\d+; SERVER_TEXT_ROUNDTRIP=(PASS|FAIL); DRIVER_ROUNDTRIP=(PASS|FAIL)(; AMOUNT_CAUSE=UNRESOLVED)?'
    assert re.fullmatch(allowed, result)

    # A committed update after editor opening must change the version even if
    # the displayed amount is unchanged or the later representation also loses precision.
    seed()
    versions = db.get_income_row_versions([801, 802])
    with closing(connect()) as conn:
        conn.cursor().execute('UPDATE classified_transactions SET amount=? WHERE id=?', (Decimal('37123.89'), 802))
        conn.commit()
    assert 'ROW_VERSION_CHANGED=YES' in detail(versions[802])
    assert 'ROW_VERSION_CHANGED=UNKNOWN' in detail(None)

    # A genuine update between Save's raw read and guarded UPDATE is rejected.
    seed('125.5')
    versions = db.get_income_row_versions([801, 802])
    before = raw()
    raced = [False]
    class Cursor:
        def __init__(self, cur): self.cur = cur
        def __getattr__(self, name): return getattr(self.cur, name)
        def execute(self, sql, params=None):
            if 'UPDATE classified_transactions' in sql and 'IS NOT DISTINCT FROM' in sql and params[8] == 802 and not raced[0]:
                raced[0] = True
                with closing(connect()) as other:
                    other.cursor().execute('UPDATE classified_transactions SET amount=? WHERE id=?', (126.5, 802))
                    other.commit()
            return self.cur.execute(sql, params) if params is not None else self.cur.execute(sql)
    class Connection:
        def __init__(self): self.conn = connect()
        def __getattr__(self, name): return getattr(self.conn, name)
        def cursor(self): return Cursor(self.conn.cursor())
    with patch.object(db, 'get_connection', Connection):
        try:
            db.save_reviewed_rows(payload(), conflict_diagnostics=True, row_versions=versions)
        except db.ConcurrentTransactionEditError as error:
            message = str(error)
            assert 'ROW_VERSION_CHANGED=YES' in message
            assert 'SERVER_TEXT_ROUNDTRIP=PASS' in message
            assert 'DRIVER_ROUNDTRIP=FAIL' in message
            assert '126.5' not in message and '125.5' not in message
        else:
            raise AssertionError('Concurrent amount update accepted')
    assert raw()[0] == before[0], 'First batch edit escaped rollback'

    class BrokenCursor:
        def execute(self, *args): raise RuntimeError('SYNTHETIC_PRIVATE_VALUE')
    stream = io.StringIO()
    with redirect_stdout(stream), redirect_stderr(stream):
        assert db._income_amount_representation(BrokenCursor(), 802, 125.5, versions[802]) == 'AMOUNT_CAUSE=UNRESOLVED'
    assert stream.getvalue() == ''
    print('PASS', kind, precision, 'version changes, round trips, whitelisted privacy, success/no diagnostic, genuine conflict and rollback')


def main():
    root = Path(os.environ['TEMP']).resolve()
    assert root.drive.upper() == 'E:'
    for key in list(os.environ):
        if any(p in key.upper() for p in ('DATABASE', 'POSTGRES', 'SUPABASE')) or key.upper().startswith('PG'):
            del os.environ[key]
    # Keep the complete retired diagnostic contract executable on its pinned
    # historical source; the permanent precision test exercises the live fix.
    from _qa_income_amount_precision import diagnostic_baseline
    db = diagnostic_baseline()
    with tempfile.TemporaryDirectory(dir=root) as folder, patch.object(db, 'DB_PATH', str(Path(folder)/'test.sqlite')):
        exercise(db, 'SQLite', 0)
    import psycopg2
    kwargs = dict(host='127.0.0.1', port=int(os.environ['ARETI_QA_PG_PORT']), user='qa_local', sslmode='disable')
    admin = psycopg2.connect(dbname='postgres', **kwargs)
    admin.autocommit = True
    try:
        with admin.cursor() as cur:
            cur.execute('SHOW data_directory')
            assert Path(cur.fetchone()[0]).resolve() == Path(os.environ['ARETI_QA_PG_DATA']).resolve()
        for kind in ('REAL', 'DOUBLE PRECISION', 'NUMERIC'):
            for precision in (0, 1):
                name = 'qa_representation_' + os.urandom(6).hex()
                with admin.cursor() as cur: cur.execute('CREATE DATABASE ' + name)
                try:
                    def connect(): return db.PostgresConnection(psycopg2.connect(dbname=name, options=f'-c extra_float_digits={precision}', **kwargs))
                    with patch.object(db, 'get_connection', connect), patch.object(db, 'USING_POSTGRES', True):
                        exercise(db, kind, precision)
                finally:
                    with admin.cursor() as cur: cur.execute('DROP DATABASE ' + name)
    finally:
        admin.close()
    for name in ('app.py', 'db.py'):
        without_amount_representation(name, Path(name).read_bytes())


if __name__ == '__main__':
    main()
