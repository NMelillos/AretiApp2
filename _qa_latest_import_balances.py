"""Snapshot selection, exact arithmetic, print safety; isolated SQLite only."""
from contextlib import closing
import ast
from decimal import Decimal
from datetime import datetime
import os
from pathlib import Path
import sqlite3
import tempfile
import uuid
from unittest.mock import patch, Mock

import db
from latest_import_balances import snapshot, print_document, COLUMNS


def main():
    ui()
    assert not db.USING_POSTGRES
    with tempfile.TemporaryDirectory(dir=os.environ['TEMP']) as root:
        with patch.object(db, 'DB_PATH', str(Path(root)/'snapshot.sqlite')):
            db.init_db()
            with closing(db.get_connection()) as c:
                for i, currency in enumerate(('USD', 'EUR', 'GBP', 'CHF', 'USD', 'USD', 'EUR'), 1):
                    c.execute('INSERT INTO account_list (account_name, bank, account_number, currency, rate_type) '
                              'VALUES (?, ?, ?, ?, ?)', (f'Account {i}', 'Test bank', f'A{i}', currency, f'{currency}/USD'))
                def statement(account, import_id, timestamp, end, opening, closing, currency, count=0):
                    key = f'qa-{import_id}'
                    c.execute('INSERT INTO statement_imports (id,statement_hash,imported_at,transaction_count) '
                              'VALUES (?,?,?,?)', (import_id,key,timestamp,count))
                    c.execute('INSERT INTO statement_balances (statement_hash,account_name,bank,account_number,'
                              'currency,period_start,period_end,opening_balance,closing_balance,notes,source) '
                              'VALUES (?,?,?,?,?,?,?,?,?,?,?)', (key,f'Account {account}','Test bank',f'A{account}',
                              currency,end[:7]+'-01',end,opening,closing,'SECRET NOTE','SECRET PATH'))
                statement(1,1,'2026-09-01T09:00:00+00:00','2026-08-31','10.01','10.01','USD')
                statement(1,2,'2026-10-01T09:00:00+00:00','2026-07-31','20.02','20.02','USD')
                statement(1,3,'2026-10-01T09:00:00+00:00','2026-08-31','30.03','30.03','USD')
                statement(2,4,'2026-10-02T09:00:00+00:00','2026-09-30','100','100','EUR')
                statement(3,5,'2026-08-01T09:00:00+00:00','2026-07-31','200','200','GBP')
                statement(3,10,'2026-07-01T09:00:00+00:00','2026-09-30','999','999','GBP')
                statement(4,6,'2026-10-02T09:00:00+00:00','2026-09-30','50','50','CHF')
                # Failed/incomplete newer imports cannot displace successful statements.
                statement(1,7,'2026-10-03T09:00:00+00:00','2026-09-30','999','999','USD',count=1)
                statement(6,8,'2026-10-03T09:00:00+00:00','2026-09-30','5','6','USD')
                statement(7,9,'2026-08-01T09:00:00+00:00','2026-07-31','-0.10','-0.10','EUR')
                for currency, rate in (('EUR','1.2'),('GBP','1.3')):
                    c.execute('INSERT INTO rates (rate_month,rate_type,rate_value) VALUES (?,?,?)',
                              ('2026-07-01',currency+'/USD',rate))
                c.commit()
                before = '\n'.join(c.iterdump())
            with patch.object(db, 'get_connection', wraps=db.get_connection) as connect, \
                    patch.object(db, '_resolve_rate_for_values', wraps=db._resolve_rate_for_values) as fx, \
                    patch.object(db, '_usd_from_amount', wraps=db._usd_from_amount) as conversion:
                rows, total, warnings = snapshot(db)
                assert fx.call_count == 4 and conversion.call_count == 3
                assert connect.call_count == 2, 'Snapshot must use a bounded number of queries'
            assert len(rows) == 7 and len({r['Account number'] for r in rows}) == 7
            by_account = {r['Account number']: r for r in rows}
            a = by_account['A1']
            assert a['Statement end date'] == '2026-08-31'
            assert a['Opening balance'] == a['Closing balance'] == a['Closing balance converted to USD'] == Decimal('30.03')
            assert by_account['A2']['Statement end date'] == '2026-09-30'
            assert by_account['A3']['Statement end date'] == '2026-07-31'
            assert by_account['A2']['Closing balance converted to USD'] == Decimal('120.00')
            assert by_account['A3']['Closing balance converted to USD'] == Decimal('260.00')
            assert by_account['A4']['Closing balance converted to USD'] == 'NOT AVAILABLE'
            assert total == Decimal('409.91') and len(warnings) == 1
            for number in ('A5','A6'):
                assert by_account[number]['Status'] == 'NO IMPORT'
                assert all(by_account[number][k] in ('',None) for k in COLUMNS[3:8])
            # HTML is escaped, has exactly the selected rows, no extra metadata.
            rows[0]['Account name'] = '<script>secret</script>'
            html = print_document(rows, total, warnings, datetime.fromisoformat('2026-10-03T12:00:00+03:00'))
            assert '<script>secret</script>' not in html and '&lt;script&gt;' in html
            assert html.count('<tr>') == 8 and '@page { size: A4 landscape;' in html
            assert 'window.print()' in html and '409.91' in html
            assert all(secret not in html for secret in ('SECRET NOTE','SECRET PATH','statement_hash','txn_date'))
            with closing(db.get_connection()) as c:
                assert '\n'.join(c.iterdump()) == before
            # Approved Safra Setup labels match canonical persisted IBANs.
            with closing(db.get_connection()) as c:
                c.execute("UPDATE account_list SET bank='Safra', account_number='Current account USD / IBAN CH12 12345' WHERE id=1")
                c.execute("UPDATE statement_balances SET bank='Safra', account_number='CH1212345' WHERE account_number='A1'")
                c.commit()
            rows, _, _ = snapshot(db)
            assert rows[0]['Status'] == 'IMPORTED'
            with closing(db.get_connection()) as c:
                c.execute("UPDATE statement_balances SET period_end='invalid date' WHERE account_number='A2'")
                c.commit()
            with patch.object(db, '_resolve_rate_for_values', wraps=db._resolve_rate_for_values) as fx:
                rows, _, warnings = snapshot(db)
            assert next(r for r in rows if r['Account number']=='A2')['Closing balance converted to USD'] == 'NOT AVAILABLE'
            assert len(warnings) == 2 and fx.call_count == 3, 'Missing/invalid dates cannot guess an FX month'
    print('PASS: per-account latest, timestamp/ID tie, incomplete imports, missing FX, exact USD total, print escaping, zero writes')
    if os.environ.get('ARETI_QA_PG_PORT'):
        postgres()


def postgres():
    """Production SQL/NUMERIC semantics, in a disposable loopback database."""
    import psycopg2
    options = dict(host='127.0.0.1', port=int(os.environ['ARETI_QA_PG_PORT']),
                   user='qa_local', sslmode='disable')
    name = 'qa_latest_balances_' + uuid.uuid4().hex
    admin = psycopg2.connect(dbname='postgres', **options)
    admin.autocommit = True
    try:
        with admin.cursor() as cur:
            cur.execute('CREATE DATABASE ' + name)
        raw = psycopg2.connect(dbname=name, **options)
        with raw:
            with raw.cursor() as c:
                c.execute('CREATE TABLE account_list (id integer, account_name text, bank text, account_number text, currency text, rate_type text)')
                c.execute('CREATE TABLE statement_imports (id integer, statement_hash text, imported_at text, transaction_count integer)')
                c.execute('CREATE TABLE statement_balances (statement_hash text, account_name text, bank text, account_number text, currency text, period_start text, period_end text, opening_balance numeric, closing_balance numeric)')
                c.execute('CREATE TABLE classified_transactions (id integer, statement_hash text, split_parent_id integer)')
                c.execute('CREATE TABLE rates (rate_month text, rate_type text, rate_value numeric)')
                c.execute("INSERT INTO account_list VALUES (1,'QA','QA bank','QA-1','USD','USD/USD')")
                c.execute("INSERT INTO statement_imports VALUES (1,'old','2026-10-03T14:00:00+03:00',0),(2,'new','2026-10-03T12:00:00+00:00',0),(3,'tie','2026-10-03T15:00:00+03:00',0)")
                for key, balance in (('old','1.01'),('new','2.02'),('tie','12345678901234567890.12')):
                    c.execute("INSERT INTO statement_balances VALUES (%s,'QA','QA bank','QA-1','USD','2026-08-01','2026-08-31',%s,%s)", (key,balance,balance))
        raw.close()
        def connect():
            connection = psycopg2.connect(dbname=name, **options)
            connection.set_session(readonly=True)
            return db.PostgresConnection(connection)
        with patch.object(db, 'get_connection', side_effect=connect), patch.object(db, 'USING_POSTGRES', True):
            rows, total, warnings = snapshot(db)
        expected = Decimal('12345678901234567890.12')
        assert len(rows) == 1 and total == expected and not warnings
        assert rows[0]['Opening balance'] == rows[0]['Closing balance'] == expected
        assert '12,345,678,901,234,567,890.12' in print_document(rows,total,warnings)
        print('PASS: PostgreSQL timestamp offsets/ID tie and exact NUMERIC beyond float precision')
    finally:
        with admin.cursor() as cur:
            cur.execute('DROP DATABASE IF EXISTS ' + name)
        admin.close()


def ui():
    """Execute the actual main-app dispatch and renderer with a fixed snapshot."""
    import latest_import_balances as report
    import streamlit.components.v1 as components
    tree = ast.parse(Path('app.py').read_text(encoding='utf-8'))
    branch = next(node for node in ast.walk(tree) if isinstance(node, ast.If)
                  and ast.unparse(node.test) == "page == 'Latest Import Balances'")
    st = Mock()
    with patch.object(report, 'snapshot', return_value=([],Decimal(0),[])), \
            patch.object(components, 'html') as preview, \
            patch.object(db, 'get_connection', side_effect=AssertionError('Renderer wrote/read DB outside snapshot')):
        exec(compile(ast.Module(body=branch.body,type_ignores=[]),'actual-report-dispatch','exec'),dict(st=st))
    st.subheader.assert_called_once_with('Latest Import Balances')
    st.download_button.assert_called_once()
    assert b'One row per account.' in st.download_button.call_args.args[1]
    preview.assert_called_once()
    assert 'TOTAL USD VALUE OF ALL ACCOUNTS' in preview.call_args.args[0]
    st = Mock()
    with patch.object(report, 'snapshot', side_effect=RuntimeError('SECRET DSN FILE PATH')), \
            patch.object(components, 'html') as preview:
        report.render(st)
    st.error.assert_called_once_with('Latest Import Balances is temporarily unavailable. Please try again.')
    st.download_button.assert_not_called()
    preview.assert_not_called()
    assert 'SECRET' not in str(st.mock_calls)
    print('PASS: actual authenticated main-app report dispatch/renderer; printable HTML, zero mutation')


if __name__ == '__main__':
    main()
