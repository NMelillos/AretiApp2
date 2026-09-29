"""Synthetic committed-import boundary and zero-write upload regressions."""
import ast
from contextlib import closing
import hashlib
from io import BytesIO
import os
from pathlib import Path
import tempfile
import subprocess
import re
import sqlite3
from time import perf_counter
from unittest.mock import patch

import pandas as pd


def without_import_history_reliability(name, source):
    from _qa_database_identity import without_database_identity
    source = without_database_identity(name, source)
    if name not in ('app.py', 'db.py') or b'from import_history import' not in source:
        return source
    source = source.replace(b'\r\n', b'\n')
    baseline = subprocess.check_output(['git', 'show',
        '58d3c878d2983a7408e70d576df2bad60d153f9f:' + name]).replace(b'\r\n', b'\n')
    hashes = {
        'save_statement_balance': 'd18ae2ddd86a1629f37a317a7a367df9b2d65852d5feeb1062d98d0083d8cff7',
        'get_import_history': '1d4a2623effc353eb2d4d3229acdbafb7a5ae823a80f22f2f20788d7725597ca',
        'save_pending_transactions': '3fe72401a8532216db19005e97b2d2bafa912ab5a447205de8422ee6434eb613',
        "page == 'Import'": '96653a87157b210b69fc22a913a7a330177da28cd085b1c647c450ec1de6d819',
        "page == 'Import History'": 'c010004ee9a0cd379d6638cf4cb1512ce3b2df0c75b140e797e1619b4884aa32',
    }
    def regions(raw):
        result = {}
        for node in ast.walk(ast.parse(raw.decode())):
            if name == 'db.py' and isinstance(node, ast.FunctionDef) and node.name in hashes:
                result[node.name] = (node.lineno-1, node.end_lineno, node)
            elif name == 'app.py' and isinstance(node, ast.If) and ast.unparse(node.test) in hashes:
                result[ast.unparse(node.test)] = (node.body[0].lineno-1, node.body[-1].end_lineno,
                    ast.Module(body=node.body, type_ignores=[]))
        return result
    old, new = regions(baseline), regions(source)
    lines, old_lines = source.splitlines(keepends=True), baseline.splitlines(keepends=True)
    for key, (start, end, node) in sorted(new.items(), key=lambda item: item[1][0], reverse=True):
        assert hashlib.sha256(ast.dump(node).encode()).hexdigest() == hashes[key], 'Unreviewed import/history block'
        a, b, _ = old[key]
        lines[start:end] = old_lines[a:b]
    assert b''.join(lines) == baseline, 'Change outside authorized import/history blocks'
    return baseline


def fixture():
    return pd.DataFrame([dict(Date='2026-01-15', Description='Synthetic history entry',
        Amount='-12.34', currency='USD', account_name='Synthetic account',
        account_number='TEST-ACCOUNT-0001', bank='Synthetic bank',
        amount_usd='-12.34', fx_rate='1', suggested_category='Synthetic',
        suggested_subcategory='General', confidence=0, dup_flag=False)])


def ui_checks(db):
    from _qa_safra_lifecycle import UI, Stop
    tree = ast.parse(Path('app.py').read_text(encoding='utf-8'))
    branch = next(n for n in tree.body if isinstance(n, ast.If)
                  and ast.unparse(n.test) == "page == 'Import'")
    class ImportUI(UI):
        def selectbox(self, label, options, index=0, **kwargs): return options[index]
        def caption(self, *args, **kwargs): pass
        def text_input(self, *args, **kwargs): return ''
        def download_button(self, *args, **kwargs): pass
    for bank in ('AMEX', 'CNB', 'Bank of Cyprus', 'CITI', 'Revolut'):
        with tempfile.TemporaryDirectory(dir=os.environ['TEMP']) as folder, patch.object(
                db, 'DB_PATH', str(Path(folder) / 'ui.sqlite')):
            db.init_db()
            frame = fixture()
            frame['bank'] = bank
            frame['match_type'] = 'new'
            account = frame.iloc[0].to_dict()
            balance = dict(period_start='2026-01-01', period_end='2026-01-31',
                           opening_balance='100', closing_balance='87.66', currency='USD')
            def snapshot():
                with closing(db.get_connection()) as conn:
                    return '\n'.join(conn.iterdump())
            invalidated = []
            def run(action=False, cancel=False, fail_parse=False):
                ui = ImportUI(action, b'synthetic common history contract')
                if cancel: ui.file = None
                env = dict(db.__dict__, st=ui, pd=pd, BytesIO=BytesIO, re=re)
                env.update(missing_setup_items=lambda *a: [],
                    get_accounts=lambda: pd.DataFrame([account]),
                    account_options=lambda *a: (['Synthetic'], {'Synthetic': account}),
                    guess_account_index=lambda *a: 0,
                    parse_statement=lambda *a: frame.copy(),
                    parse_statement_balance=lambda *a: balance.copy(),
                    is_amex_cardholder_statement=lambda *a: False,
                    flag_duplicates=lambda df: df,
                    classify_statement_rows=lambda df, memory: df,
                    balance_has_values=lambda b: True,
                    render_summary_strip=lambda *a: None,
                    display_money=lambda value, *a: str(value))
                if fail_parse:
                    def reject(*args): raise ValueError('Synthetic parse failure')
                    env['parse_statement'] = reject
                readers = ('get_import_history', 'get_import_transaction_audit',
                    'get_statement_balances', 'get_all_transactions', 'get_pending_transactions',
                    'get_saved_transactions', 'get_dashboard_counts',
                    'get_cross_statement_duplicate_audit', 'get_exact_duplicate_audit')
                for name in readers:
                    def reader(*a, _name=name, **kw): return getattr(db, _name)(*a, **kw)
                    reader.clear = lambda name=name: invalidated.append(name)
                    env[name] = reader
                try:
                    exec(compile(ast.Module(body=branch.body, type_ignores=[]), 'app.py', 'exec'), env)
                except Stop:
                    pass
                return ui
            before = snapshot()
            real_connection = db.get_connection
            writes = []
            def readonly_connection():
                conn = real_connection()
                def authorize(action, *args):
                    if action not in (sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ, sqlite3.SQLITE_FUNCTION):
                        writes.append(action)
                        return sqlite3.SQLITE_DENY
                    return sqlite3.SQLITE_OK
                conn.set_authorizer(authorize)
                return conn
            with patch.object(db, 'get_connection', readonly_connection):
                for action in (dict(), dict(cancel=True), dict(fail_parse=True)):
                    ui = run(**action)
                    if not action.get('fail_parse'): assert not ui.errors, ui.errors
            assert not writes and snapshot() == before, 'Upload/cancel/failure attempted a write'
            with patch.object(db, 'save_statement_balance', side_effect=ValueError('Synthetic DB failure')):
                ui = run(True)
            assert ui.errors and snapshot() == before
            start = perf_counter()
            ui = run(True)
            assert not ui.errors, ui.errors
            history = db.get_import_history()
            assert len(history) == 1 and history.iloc[0].bank == bank
            assert history.iloc[0].account_number == account['account_number']
            assert history.iloc[0].currency == 'USD' and history.iloc[0].transaction_count == 1
            assert 'get_import_history' in invalidated and 'get_statement_balances' in invalidated
            history_branch = next(n for n in ast.walk(tree) if isinstance(n, ast.If)
                and ast.unparse(n.test) == "page == 'Import History'")
            history_ui = ImportUI()
            history_env = dict(db.__dict__, st=history_ui, pd=pd,
                render_summary_strip=lambda *a: None, dataframe_to_excel_bytes=lambda *a: b'')
            with patch.object(db, 'get_connection', readonly_connection):
                exec(compile(ast.Module(body=history_branch.body, type_ignores=[]), 'app.py', 'exec'), history_env)
            assert not writes and not history_ui.errors
            displayed = history_ui.tables[0]
            assert displayed.iloc[0].account_number == account['account_number']
            assert displayed.iloc[0]['Import time (Cyprus)'].endswith(('EET', 'EEST'))
            print('TIMING', bank, 'commit_and_visible_ms', round((perf_counter()-start)*1000, 3))
            before = snapshot()
            with patch.object(db, 'record_duplicate_statement_attempt', side_effect=AssertionError('Duplicate write')):
                for _ in range(2):
                    assert not run(True).errors
            assert snapshot() == before
    print('PASS actual Import branch: five bank contracts, preview/cancel/failure, atomic import, cache clear and duplicate')


def main():
    import db
    assert not db.USING_POSTGRES
    assert Path(os.environ['TEMP']).drive.upper() == 'E:'
    failures = []

    def check(name, operation):
        try:
            operation()
        except AssertionError as exc:
            failures.append(name)
            print('FAIL', name, str(exc))
        else:
            print('PASS', name)

    with tempfile.TemporaryDirectory(dir=os.environ['TEMP']) as folder, patch.object(
            db, 'DB_PATH', str(Path(folder) / 'history.sqlite')):
        db.init_db()
        frame = fixture()
        digest = db.build_statement_hash(b'synthetic history source')
        db.save_pending_transactions(frame, 'synthetic.csv', digest)

        def incomplete_hidden():
            assert db.get_import_history().empty, 'Transactions without committed balance/section are shown as successful'
        check('incomplete import excluded', incomplete_hidden)

        def duplicate_zero_write():
            with closing(db.get_connection()) as conn:
                before = '\n'.join(conn.iterdump())
            assert db.save_pending_transactions(frame, 'synthetic.csv', digest)[1]
            with closing(db.get_connection()) as conn:
                after = '\n'.join(conn.iterdump())
            assert before == after, 'Duplicate attempt changes persistent history'
        check('duplicate backend zero-write', duplicate_zero_write)

        def empty_hidden():
            empty_hash = db.build_statement_hash(b'synthetic empty incomplete')
            db.save_pending_transactions(frame.iloc[:0], 'synthetic-empty.csv', empty_hash)
            history = db.get_import_history()
            assert not history.statement_hash.eq(empty_hash).any(), 'Empty import without balances is shown as successful'
        check('incomplete zero-row import excluded', empty_hidden)

    tree = ast.parse(Path('app.py').read_text(encoding='utf-8'))
    branch = next(n for n in tree.body if isinstance(n, ast.If)
                  and ast.unparse(n.test) == "page == 'Import'")
    duplicate = next(n for n in ast.walk(branch) if isinstance(n, ast.If)
                     and ast.unparse(n.test) == 'statement_already_imported(statement_hash)')

    def upload_zero_write():
        calls = {n.func.id for n in ast.walk(duplicate) if isinstance(n, ast.Call)
                 and isinstance(n.func, ast.Name)}
        assert not calls & {'record_duplicate_statement_attempt', 'save_statement_balance'}, 'Duplicate upload has persistent mutators'
    check('duplicate upload zero-write', upload_zero_write)
    from import_history import commit_statement, cyprus_time
    with tempfile.TemporaryDirectory(dir=os.environ['TEMP']) as folder, patch.object(
            db, 'DB_PATH', str(Path(folder) / 'atomic.sqlite')):
        db.init_db()
        frame = fixture()
        account = frame.iloc[0].to_dict()
        balance = dict(period_start='2026-01-01', period_end='2026-01-31',
                       opening_balance='100', closing_balance='87.66', currency='USD')
        def snapshot():
            with closing(db.get_connection()) as conn:
                return '\n'.join(conn.iterdump())
        before = snapshot()
        with patch.object(db, 'save_statement_balance', side_effect=ValueError('Synthetic failure')):
            try:
                commit_statement(db, frame, 'synthetic.csv', 'atomic-test', balance, account)
            except ValueError:
                pass
            else:
                raise AssertionError('Injected failure accepted')
        assert snapshot() == before, 'Partial import survived balance failure'
        assert commit_statement(db, frame, 'synthetic.csv', 'atomic-test', balance, account) == (1, False, 0)
        assert len(db.get_import_history()) == 1
        before = snapshot()
        assert commit_statement(db, frame, 'synthetic.csv', 'atomic-test', balance, account) == (0, True, 0)
        assert snapshot() == before
        empty_balance = dict(balance, opening_balance='100', closing_balance='100')
        assert commit_statement(db, frame.iloc[:0], 'empty.csv', 'empty-valid', empty_balance, account) == (0, False, 0)
        assert len(db.get_import_history()) == 2
        before = snapshot()
        try:
            commit_statement(db, frame.iloc[:0], 'empty.csv', 'empty-invalid', {}, account)
        except ValueError:
            pass
        else:
            raise AssertionError('Incomplete empty statement accepted')
        assert snapshot() == before
        invalid = frame.copy()
        invalid['Amount'] = 'NaN'
        for data, summary in ((invalid, balance), (frame.iloc[:0],
                dict(empty_balance, money_out='10', money_in='10'))):
            try:
                commit_statement(db, data, 'invalid.csv', 'crafted-invalid', summary, account)
            except ValueError:
                pass
            else:
                raise AssertionError('Crafted non-finite amount or missing transaction rows accepted')
            assert snapshot() == before
        print('PASS atomic rollback, successful/zero-row import, immediate read, duplicate zero-write')
        with closing(db.get_connection()) as conn:
            conn.execute("UPDATE statement_imports SET transaction_count=2 WHERE statement_hash='atomic-test'")
            conn.commit()
        assert not db.get_import_history().statement_hash.eq('atomic-test').any(), 'Incomplete row count shown as successful'
        with closing(db.get_connection()) as conn:
            conn.execute("UPDATE statement_imports SET transaction_count=1 WHERE statement_hash='atomic-test'")
            conn.commit()
        legacy = frame.copy()
        legacy['Description'] = 'Synthetic legacy metadata'
        for key in ('account_name', 'bank', 'account_number', 'currency'):
            legacy[key] = ''
        db.save_pending_transactions(legacy, 'legacy.csv', 'legacy-metadata')
        db.save_statement_balance('legacy-metadata', 'legacy.csv', balance, account)
        record = db.get_import_history().set_index('statement_hash').loc['legacy-metadata']
        for key in ('account_name', 'bank', 'account_number', 'currency'):
            assert record[key] == account[key], 'Blank transaction metadata hides stored section metadata: ' + key
    assert cyprus_time('2026-01-01T10:00:00+00:00') == '2026-01-01 12:00:00 EET'
    assert cyprus_time('2026-07-01T10:00:00+00:00') == '2026-07-01 13:00:00 EEST'
    assert cyprus_time('2026-07-01 10:00:00') == '2026-07-01 10:00:00 (timezone unverified)'
    print('PASS Cyprus winter/summer and unchanged legacy timestamp')
    ui_checks(db)
    postgres_checks(db)
    assert not failures, 'Import History requirements failed: ' + ', '.join(failures)


def postgres_checks(db):
    import psycopg2
    from import_history import commit_statement
    options = dict(host='127.0.0.1', port=int(os.environ['ARETI_QA_PG_PORT']),
                   user='qa_local', sslmode='disable')
    admin = psycopg2.connect(dbname='postgres', **options)
    admin.autocommit = True
    name = 'qa_history_' + os.urandom(6).hex()
    created = False
    try:
        with admin.cursor() as cur:
            cur.execute('SHOW data_directory')
            assert Path(cur.fetchone()[0]).resolve() == Path(os.environ['ARETI_QA_PG_DATA']).resolve()
            cur.execute('CREATE DATABASE ' + name)
            created = True
        def connect():
            return db.PostgresConnection(psycopg2.connect(dbname=name, **options))
        with patch.object(db, 'USING_POSTGRES', True), patch.object(db, 'get_connection', connect):
            db.init_db()
            frame = fixture()
            account = frame.iloc[0].to_dict()
            balance = dict(period_start='2026-01-01', period_end='2026-01-31',
                           opening_balance='100', closing_balance='87.66', currency='USD')
            with patch.object(db, 'save_statement_balance', side_effect=ValueError('Synthetic failure')):
                try:
                    commit_statement(db, frame, 'synthetic.csv', 'pg-history', balance, account)
                except ValueError:
                    pass
                else:
                    raise AssertionError('Injected failure accepted')
            with closing(connect()) as conn:
                for table in ('statement_imports', 'statement_balances', 'classified_transactions'):
                    cur = conn.cursor()
                    cur.execute('SELECT COUNT(*) FROM ' + table)
                    assert cur.fetchone()[0] == 0
            assert commit_statement(db, frame, 'synthetic.csv', 'pg-history', balance, account) == (1, False, 0)
            assert len(db.get_import_history()) == 1
            assert commit_statement(db, frame, 'synthetic.csv', 'pg-history', balance, account) == (0, True, 0)
            assert commit_statement(db, frame.iloc[:0], 'empty.csv', 'pg-empty',
                dict(balance, opening_balance='100', closing_balance='100'), account) == (0, False, 0)
            assert len(db.get_import_history()) == 2
            print('PASS PostgreSQL isolated atomic import, rollback, duplicate and zero-row visibility')
    finally:
        if created:
            with admin.cursor() as cur: cur.execute('DROP DATABASE ' + name)
        admin.close()


if __name__ == '__main__':
    main()
