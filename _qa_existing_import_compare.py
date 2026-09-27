"""Read-only comparison through the real duplicate UI and isolated databases."""
from contextlib import closing, ExitStack
from decimal import Decimal
import hashlib
import os
from pathlib import Path
import sqlite3
import tempfile
import copy
import json
from unittest.mock import patch

import pandas as pd
import parsing
from _qa_safra_completion import fixture
from _qa_safra_lifecycle import PDF, upload
from _qa_safra_uat import labelled_accounts
from _qa_safra_duplicate_preview import run_ui


def main():
    import db
    import existing_import_compare as compare
    pages = fixture()
    parsed = parsing._parse_safra_pages(pages)
    accounts = labelled_accounts(parsed)
    content = b'%PDF-synthetic comparison only'
    with tempfile.TemporaryDirectory(dir=os.environ['TEMP']) as folder, patch.object(
            db, 'DB_PATH', str(Path(folder) / 'compare.sqlite')), patch.object(
            db, 'get_accounts', return_value=accounts):
        db.init_db()
        db.add_category('Synthetic', 'General', 'Synthetic')
        with closing(db.get_connection()) as conn:
            conn.execute('INSERT INTO rates (rate_month,rate_type,rate_value) VALUES (?,?,?)',
                         ('2026-01-01', 'GBP/USD', 1.25))
            conn.commit()
        imported = upload(db, pages, True, content)
        assert not imported.errors, imported.errors
        path = Path(db.DB_PATH)
        before = hashlib.sha256(path.read_bytes()).hexdigest()
        real_connection = db.get_connection
        statements = []

        def readonly_connection():
            conn = real_connection()
            def guard(action, arg1, *args):
                allowed = {sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ,
                           sqlite3.SQLITE_FUNCTION, sqlite3.SQLITE_TRANSACTION}
                if action == sqlite3.SQLITE_PRAGMA and arg1 in {'query_only', 'table_info'}:
                    return sqlite3.SQLITE_OK
                return sqlite3.SQLITE_OK if action in allowed else sqlite3.SQLITE_DENY
            conn.set_authorizer(guard)
            conn.set_trace_callback(statements.append)
            return conn

        with patch.object(compare, 'get_script_run_ctx', return_value=object()), patch.object(
                compare.st, 'session_state', {'authenticated': True, 'login_user': 'Areti'}), patch.object(
                db, 'get_connection', side_effect=readonly_connection), patch.object(
                parsing.pdfplumber, 'open', return_value=PDF(pages)):
            result = compare.compare_existing(content)
            assert len(result['sections']) == 6 and len(result['transactions']) == 9
            assert result['fingerprint'] == hashlib.sha256(content).hexdigest()
            assert all(r['Status'] in {'MATCH', 'MISMATCH'} for r in result['transactions'])
            assert all(isinstance(r['PDF amount'], str) for r in result['transactions'])
            assert compare.compare_existing(content) == result
            with ExitStack() as stack:
                spies = []
                allowed = {'get_connection', 'get_accounts', 'get_categories', 'get_rates',
                           'statement_already_imported', 'build_statement_hash'}
                for name, obj in list(vars(db).items()):
                    if (not name.startswith('_') and name not in allowed and callable(obj)
                            and not isinstance(obj, type) and getattr(obj, '__module__', None) == 'db'):
                        spies.append(stack.enter_context(patch.object(db, name,
                            side_effect=AssertionError('Forbidden DB API'))))
                for action in ('Compare with Existing Import', '', 'Cancel', 'Repair Existing Import',
                               'Import statement', 'Compare with Existing Import'):
                    ui, calls = run_ui(db, pages, action, content)
                    assert not ui.errors, ui.errors
                    assert 'Compare with Existing Import' in ui.buttons
                    assert 'Repair Existing Import' not in ui.buttons and 'Import statement' not in ui.buttons
                    if action == 'Compare with Existing Import':
                        assert len(ui.tables) == 2 and len(ui.tables[0]) == 6 and len(ui.tables[1]) == 9
                    else:
                        assert not ui.tables
                assert all(not spy.called for spy in spies)
        assert hashlib.sha256(path.read_bytes()).hexdigest() == before
        assert not any(s.lstrip().upper().startswith(('INSERT', 'UPDATE', 'DELETE', 'CREATE', 'ALTER')) for s in statements)
        assert compare.exact_decimal(Decimal('148351.30')) == Decimal('148351.30')
        assert str(compare.exact_decimal(Decimal('148351.30'))) == '148351.30'
        assert compare.exact_decimal(0.1) == Decimal.from_float(0.1)
        assert compare.exact_decimal(0.1) != Decimal('0.1')
        from streamlit.testing.v1 import AppTest
        script = """
import streamlit as st
from safra_duplicate_preview import render_preview
render_preview(st, b'%PDF-synthetic comparison only', 'synthetic.pdf', None, None)
"""
        with patch.object(db, 'get_connection', side_effect=readonly_connection), patch.object(
                parsing.pdfplumber, 'open', return_value=PDF(pages)):
            app = AppTest.from_string(script)
            app.query_params['authenticated'] = 'true'
            app.query_params['login_user'] = 'Areti'
            app.query_params['action'] = 'compare'
            app.run()
            assert not app.exception
            assert 'Compare with Existing Import' not in [b.label for b in app.button]
            app.session_state['login_username'] = 'Areti'
            app.run()
            assert 'Compare with Existing Import' not in [b.label for b in app.button]
            app.session_state['authenticated'] = True
            app.session_state['login_user'] = 'Areti'
            app.run()
            next(b for b in app.button if b.label == 'Compare with Existing Import').click().run()
            assert not app.exception and not app.error and len(app.dataframe) == 2
            assert len(app.dataframe[0].value) == 6 and len(app.dataframe[1].value) == 9
            app.run()
            assert not app.dataframe
            app.session_state['third_report_authenticated'] = True
            app.run()
            assert 'Compare with Existing Import' not in [b.label for b in app.button]
        assert hashlib.sha256(path.read_bytes()).hexdigest() == before
        for state in ({}, {'authenticated': True}, {'authenticated': False, 'login_user': 'Areti'},
                      {'authenticated': True, 'login_user': 'areti'},
                      {'authenticated': True, 'login_user': 'Other'},
                      {'third_report_authenticated': True, 'login_user': 'Areti'},
                      {'authenticated': True, 'login_user': 'Areti', 'third_report_authenticated': True},
                      {'login_username': 'Areti'}, {'authenticated': 'true', 'login_user': 'Areti'}):
            with patch.object(compare, 'get_script_run_ctx', return_value=object()), patch.object(
                    compare.st, 'session_state', state), patch.object(db, 'get_connection') as connection:
                try:
                    compare.compare_existing(content)
                except compare.CompareBlocked:
                    pass
                else:
                    raise AssertionError('Unauthorized comparison accepted')
                connection.assert_not_called()
        with patch.object(compare, 'get_script_run_ctx', return_value=None), patch.object(
                compare.st, 'session_state', {'authenticated': True, 'login_user': 'Areti'}):
            assert not compare.authorized()
        # Test each mapping gate independently without modifying application rules.
        with patch.object(compare, 'get_script_run_ctx', return_value=object()), patch.object(
                compare.st, 'session_state', {'authenticated': True, 'login_user': 'Areti'}), patch.object(
                parsing.pdfplumber, 'open', return_value=PDF(pages)):
            original = compare._reconcile
            for mutation in ('count', 'identity', 'section', 'split', 'duplicate'):
                def mutate(sections, balances, imports, transactions, mutation=mutation):
                    sections, balances, imports, transactions = copy.deepcopy((sections, balances, imports, transactions))
                    if mutation == 'count': transactions.pop()
                    if mutation == 'identity': transactions[0]['original_description'] = 'different synthetic reference'
                    if mutation == 'section': balances[0]['notes'] = '{}'
                    if mutation == 'split': transactions[0]['split_group_id'] = 'synthetic-split'
                    if mutation == 'duplicate': transactions[1].update({k: transactions[0][k] for k in ('txn_date', 'original_description', 'currency')})
                    return original(sections, balances, imports, transactions)
                with patch.object(compare, '_reconcile', side_effect=mutate):
                    try: compare.compare_existing(content)
                    except compare.CompareBlocked: pass
                    else: raise AssertionError('Mapping gate accepted ' + mutation)
            try: compare.compare_existing(content + b'different fingerprint')
            except compare.CompareBlocked: pass
            else: raise AssertionError('Different fingerprint accepted')
        with closing(real_connection()) as conn:
            conn.execute('UPDATE classified_transactions SET original_description = ?', ('changed synthetic reference',))
            conn.commit()
        with patch.object(compare, 'get_script_run_ctx', return_value=object()), patch.object(
                compare.st, 'session_state', {'authenticated': True, 'login_user': 'Areti'}), patch.object(
                parsing.pdfplumber, 'open', return_value=PDF(pages)):
            try:
                compare.compare_existing(content)
            except compare.CompareBlocked:
                pass
            else:
                raise AssertionError('Ambiguous identity accepted')
    print('PASS exact comparison, six sections, repeated currencies, read-only SQL/bytes, authorization and ambiguity')
    postgres_checks(db, compare, pages, accounts, content)


def postgres_checks(db, compare, pages, accounts, content):
    import psycopg2
    options = dict(host='127.0.0.1', port=int(os.environ['ARETI_QA_PG_PORT']), user='qa_local', sslmode='disable')
    admin = psycopg2.connect(dbname='postgres', **options)
    admin.autocommit = True
    name = 'qa_compare_' + os.urandom(6).hex()
    created = False
    try:
        with admin.cursor() as cur:
            cur.execute('SHOW data_directory')
            assert Path(cur.fetchone()[0]).resolve() == Path(os.environ['ARETI_QA_PG_DATA']).resolve()
            cur.execute('CREATE DATABASE ' + name)
            created = True
        def connect():
            raw = psycopg2.connect(dbname=name, **options)
            with raw.cursor() as cur: cur.execute('SET extra_float_digits = 0')
            raw.commit()
            return db.PostgresConnection(raw)
        with patch.object(db, 'USING_POSTGRES', True), patch.object(db, 'get_connection', connect), patch.object(
                db, 'get_accounts', return_value=accounts):
            db.init_db()
            db.add_category('Synthetic', 'General', 'Synthetic')
            with closing(connect()) as conn:
                cur = conn.cursor()
                cur.execute('INSERT INTO rates (rate_month,rate_type,rate_value) VALUES (?,?,?)', ('2026-01-01', 'GBP/USD', 1.25))
                conn.commit()
            assert not upload(db, pages, True, content).errors
            def snapshot():
                with closing(connect()) as conn:
                    cur = conn.cursor()
                    result = []
                    cur.execute("SELECT table_name FROM information_schema.tables WHERE table_schema = 'public' ORDER BY table_name")
                    tables = [row[0] for row in cur.fetchall()]
                    for table in tables:
                        assert table.replace('_', '').isalnum()
                        cur.execute('SELECT * FROM ' + table)
                        result.append((table, sorted(repr(row) for row in cur.fetchall())))
                    return result
            for kind in ('REAL', 'DOUBLE PRECISION', 'NUMERIC'):
                with closing(connect()) as conn:
                    cur = conn.cursor()
                    for table, fields in compare.MONEY.items():
                        for field in fields:
                            cur.execute('ALTER TABLE ' + table + ' ALTER COLUMN ' + field + ' TYPE ' + kind)
                    cur.execute('UPDATE classified_transactions SET amount = ? WHERE id = (SELECT MIN(id) FROM classified_transactions)', (Decimal('148351.30'),))
                    conn.commit()
                before = snapshot()
                with patch.object(compare, 'get_script_run_ctx', return_value=object()), patch.object(
                        compare.st, 'session_state', {'authenticated': True, 'login_user': 'Areti'}), patch.object(
                        parsing.pdfplumber, 'open', return_value=PDF(pages)):
                    result = compare.compare_existing(content)
                    assert len(result['transactions']) == 9 and len(result['sections']) == 6
                    first = min(result['transactions'], key=lambda row: row['Record ID'])
                    stored = Decimal(first['Stored amount'])
                    if kind == 'NUMERIC': assert stored == Decimal('148351.30')
                    elif kind == 'REAL': assert stored == Decimal('148351.296875')
                    else: assert stored == Decimal.from_float(148351.30)
                    assert first['Status'] == 'MISMATCH'
                assert snapshot() == before
            print('PASS PostgreSQL REAL/DOUBLE/NUMERIC exact binary evidence at reduced output precision; all-table snapshots unchanged')
    finally:
        if created:
            with admin.cursor() as cur: cur.execute('DROP DATABASE ' + name)
        admin.close()


if __name__ == '__main__':
    main()
