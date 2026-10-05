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
from latest_import_balances import snapshot, print_document, workbook_bytes, COLUMNS, NOTE


def main():
    ui()
    assert not db.USING_POSTGRES
    with tempfile.TemporaryDirectory(dir=os.environ['TEMP']) as root:
        with patch.object(db, 'DB_PATH', str(Path(root)/'snapshot.sqlite')):
            db.init_db()
            with closing(db.get_connection()) as c:
                # Exact-text fixture exercises eligible amounts separately from
                # the binary-storage rejection tested below and on PostgreSQL.
                schema=c.execute("SELECT sql FROM sqlite_master WHERE name='statement_balances'").fetchone()[0]
                c.execute('DROP TABLE statement_balances')
                c.execute(schema.replace('opening_balance REAL','opening_balance TEXT').replace('closing_balance REAL','closing_balance TEXT'))
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
                c.commit()
                tied, _, _ = snapshot(db)
                tie=next(row for row in tied if row['Account number']=='A1')
                assert tie['Closing balance']==Decimal('30.03') and tie['Statement end date']=='2026-08-31'
                # Newer incomplete imports stay visible; never replace them with
                # older healthy balances or pretend a committed import is absent.
                statement(1,7,'2026-10-03T09:00:00+00:00','2026-09-30','999','999','USD',count=1)
                statement(6,8,'2026-10-03T09:00:00+00:00','2026-09-30','5','6','USD')
                statement(7,9,'2026-08-01T09:00:00+00:00','2026-07-31','-0.10','-0.10','EUR')
                # Production-shaped legacy rows have matching transaction counts,
                # but a missing field on the SAME balance row makes them ineligible.
                missing_fields = (('period_start', None), ('period_end', ''),
                                  ('opening_balance', None), ('closing_balance', None),
                                  ('account_number', ''), ('currency', ''),
                                  ('period_start', ''), ('period_end', None),
                                  ('opening_balance', ''), ('closing_balance', '  '))
                for import_id, (field, value) in enumerate(missing_fields, 11):
                    statement(1,import_id,'2026-10-04T09:00:00+00:00','2026-09-30','999','999','USD',count=1)
                    c.execute('INSERT INTO classified_transactions (statement_hash,row_hash,bank,account_number,currency) VALUES (?,?,?,?,?)',
                              (f'qa-{import_id}',f'row-{import_id}','Test bank','A1','USD'))
                    c.execute(f'UPDATE statement_balances SET {field}=? WHERE statement_hash=?',
                              (value,f'qa-{import_id}'))
                    c.commit()
                    checked,_,_=snapshot(db)
                    current=next(row for row in checked if row['Account number']=='A1')
                    assert current['Status']=='INCOMPLETE' and current['Closing balance converted to USD']=='NOT AVAILABLE',(field,current)
                    assert current['Opening balance']==(None if field=='opening_balance' else Decimal('999')),(field,current)
                    assert current['Closing balance']==(None if field=='closing_balance' else Decimal('999')),(field,current)
                # A non-empty whitespace date is also incomplete; no complete prior
                # import still means INCOMPLETE even when transaction_count matches.
                statement(5,40,'2026-10-04T09:00:00+00:00','2026-09-30','999','999','USD',count=1)
                c.execute('INSERT INTO classified_transactions (statement_hash,row_hash,bank,account_number,currency) VALUES (?,?,?,?,?)',
                          ('qa-40','row-40','Test bank','A5','USD'))
                c.execute("UPDATE statement_balances SET period_start='   ' WHERE statement_hash='qa-40'")
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
            for row in rows:
                if row['Status'] == 'IMPORTED':
                    assert all(row[k] not in ('', None) for k in COLUMNS[4:9])
            by_account = {r['Account number']: r for r in rows}
            a = by_account['A1']
            assert a['Status']=='INCOMPLETE' and a['Statement end date']=='2026-09-30',a
            assert a['Opening balance']==Decimal('999') and a['Closing balance'] is None
            assert a['Closing balance converted to USD']=='NOT AVAILABLE'
            assert by_account['A2']['Statement end date'] == '2026-09-30'
            assert by_account['A3']['Statement end date'] == '2026-07-31'
            assert by_account['A2']['Closing balance converted to USD'] == Decimal('120.00')
            assert by_account['A3']['Closing balance converted to USD'] == Decimal('260.00')
            assert by_account['A4']['Closing balance converted to USD'] == 'NOT AVAILABLE'
            assert total == Decimal('379.88') and len(warnings) == 4
            for number in ('A5','A6'):
                assert by_account[number]['Status'] == 'INCOMPLETE'
                assert by_account[number]['Closing balance converted to USD']=='NOT AVAILABLE'
            assert by_account['A5']['Closing balance']==Decimal('999')
            assert by_account['A6']['Opening balance']==Decimal('5') and by_account['A6']['Closing balance']==Decimal('6')
            assert all(any(number in warning and 'excluded' in warning.lower() for warning in warnings) for number in ('A1','A4','A5','A6'))
            # HTML is escaped, has exactly the selected rows, no extra metadata.
            rows[0]['Account name'] = '<script>secret</script>'
            html = print_document(rows, total, warnings, datetime.fromisoformat('2026-10-03T12:00:00+03:00'))
            assert '<script>secret</script>' not in html and '&lt;script&gt;' in html
            assert html.count('<tr>') == 8 and '@page { size: A4 landscape;' in html
            assert 'window.print()' in html and '379.88' in html and 'INTERIM PARTIAL REPORT' in html
            assert 'TOTAL USD VALUE OF ALL ACCOUNTS' not in html
            assert all(secret not in html for secret in ('SECRET NOTE','SECRET PATH','statement_hash','txn_date'))
            with closing(db.get_connection()) as c:
                assert '\n'.join(c.iterdump()) == before
            # Approved Safra Setup labels match canonical persisted IBANs.
            with closing(db.get_connection()) as c:
                # A genuinely newer complete Safra statement may become eligible;
                # the report itself performs no recovery or data modification.
                statement(1,41,'2026-10-05T09:00:00+00:00','2026-09-30','30.03','30.03','USD')
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
            assert len(warnings) == 4 and fx.call_count == 3, 'Missing/invalid dates cannot guess an FX month'
    print('PASS: per-account latest, timestamp/ID tie, incomplete imports, missing FX, exact USD total, print escaping, zero writes')
    if os.environ.get('ARETI_QA_PG_PORT'):
        postgres()
    binary_storage()


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
            cur.execute('SHOW data_directory')
            assert Path(cur.fetchone()[0]).resolve()==Path(os.environ['ARETI_QA_PG_DATA']).resolve(), 'Disposable PostgreSQL identity mismatch'
            cur.execute('CREATE DATABASE ' + name)
        raw = psycopg2.connect(dbname=name, **options)
        with raw:
            with raw.cursor() as c:
                c.execute('CREATE TABLE account_list (id integer, account_name text, bank text, account_number text, currency text, rate_type text)')
                c.execute('CREATE TABLE statement_imports (id integer PRIMARY KEY, statement_hash text UNIQUE, imported_at text, transaction_count integer)')
                c.execute('CREATE TABLE statement_balances (statement_hash text UNIQUE, account_name text, bank text, account_number text, currency text, period_start text, period_end text, opening_balance numeric, closing_balance numeric)')
                c.execute('CREATE TABLE classified_transactions (id integer, statement_hash text, split_parent_id integer, bank text, account_number text, currency text)')
                c.execute('CREATE TABLE rates (rate_month text, rate_type text, rate_value numeric)')
                c.execute("INSERT INTO account_list VALUES (1,'QA','QA bank','QA-1','USD','USD/USD')")
                c.execute("INSERT INTO statement_imports VALUES (1,'old','2026-10-03T14:00:00+03:00',0),(2,'new','2026-10-03T12:00:00+00:00',0),(3,'tie','2026-10-03T15:00:00+03:00',0)")
                for key, balance in (('old','1.01'),('new','2.02'),('tie','12345678901234567890.12')):
                    c.execute("INSERT INTO statement_balances VALUES (%s,'QA','QA bank','QA-1','USD','2026-08-01','2026-08-31',%s,%s)", (key,balance,balance))
                raw.commit()
                def tie_connect():
                    connection=psycopg2.connect(dbname=name,**options)
                    connection.set_session(readonly=True)
                    return db.PostgresConnection(connection)
                with patch.object(db,'get_connection',side_effect=tie_connect),patch.object(db,'USING_POSTGRES',True):
                    tied,tied_total,tied_warnings=snapshot(db)
                huge=Decimal('12345678901234567890.12')
                assert tied[0]['Closing balance']==huge and tied_total==huge and not tied_warnings
                assert '12,345,678,901,234,567,890.12' in print_document(tied,tied_total,tied_warnings)
                # Positive-count legacy imports are complete by transaction count
                # yet incomplete by their own balance row, exactly as production.
                missing_fields = (('period_start', None), ('period_end', ''),
                                  ('opening_balance', None), ('closing_balance', None),
                                  ('account_number', ''), ('currency', ''),
                                  ('period_start', '   '))
                for import_id, (field, value) in enumerate(missing_fields, 4):
                    key = f'incomplete-{import_id}'
                    c.execute("INSERT INTO statement_imports VALUES (%s,%s,'2026-10-04T12:00:00+00:00',1)",(import_id,key))
                    c.execute("INSERT INTO classified_transactions VALUES (%s,%s,NULL,'QA bank','QA-1','USD')",(import_id,key))
                    c.execute("INSERT INTO statement_balances VALUES (%s,'QA','QA bank','QA-1','USD','2026-09-01','2026-09-30',999,999)",(key,))
                    c.execute(f'UPDATE statement_balances SET {field}=%s WHERE statement_hash=%s',(value,key))
                for account_id, currency in ((2,'USD'),(3,'EUR'),(4,'GBP')):
                    key = f'account-{account_id}'
                    c.execute("INSERT INTO account_list VALUES (%s,'QA','QA bank',%s,%s,%s)",
                              (account_id,f'QA-{account_id}',currency,currency+'/USD'))
                    c.execute("INSERT INTO statement_imports VALUES (%s,%s,'2026-10-04T12:00:00+00:00',1)",(100+account_id,key))
                    c.execute("INSERT INTO classified_transactions VALUES (%s,%s,NULL,'QA bank',%s,%s)",(100+account_id,key,f'QA-{account_id}',currency))
                    c.execute("INSERT INTO statement_balances VALUES (%s,'QA','QA bank',%s,%s,%s,%s,%s,%s)",
                              (key,f'QA-{account_id}',currency,
                               '' if account_id==2 else '2026-08-01',
                               '' if account_id==2 else '2026-08-31',
                               None if account_id==2 else 100 if account_id==3 else 200,
                               None if account_id==2 else 100 if account_id==3 else 200))
                c.execute("INSERT INTO rates VALUES ('2026-08-01','EUR/USD',1.2),('2026-08-01','GBP/USD',1.3)")
        raw.close()
        def connect():
            connection = psycopg2.connect(dbname=name, **options)
            connection.set_session(readonly=True)
            return db.PostgresConnection(connection)
        with patch.object(db, 'get_connection', side_effect=connect), patch.object(db, 'USING_POSTGRES', True):
            rows, total, warnings = snapshot(db)
        expected = Decimal('12345678901234567890.12')
        by_account = {r['Account number']:r for r in rows}
        assert len(rows) == len(by_account) == 4 and total == Decimal('380.00') and len(warnings)==2
        assert by_account['QA-1']['Opening balance'] == by_account['QA-1']['Closing balance'] == Decimal('999')
        assert by_account['QA-1']['Closing balance converted to USD'] == 'NOT AVAILABLE'
        assert by_account['QA-1']['Status']=='INCOMPLETE' and by_account['QA-1']['Statement end date']=='2026-09-30'
        assert by_account['QA-2']['Status'] == 'INCOMPLETE'
        assert by_account['QA-2']['Closing balance'] is None
        assert by_account['QA-3']['Closing balance converted to USD'] == Decimal('120.00')
        assert by_account['QA-4']['Closing balance converted to USD'] == Decimal('260.00')
        assert all(any(number in warning and 'excluded' in warning.lower() for warning in warnings) for number in ('QA-1','QA-2'))
        print('PASS: PostgreSQL latest incomplete imports remain visible despite matching counts; missing balances excluded; exact NUMERIC and approved FX; timestamp/ID ties; read-only connections')
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
    assert st.download_button.call_count==2
    html_call,excel_call=st.download_button.call_args_list
    assert html_call.kwargs['file_name']=='latest_import_balances.html'
    assert excel_call.kwargs['file_name']=='latest_import_balances.xlsx'
    assert NOTE.encode() in html_call.args[1]
    from io import BytesIO
    from openpyxl import load_workbook
    assert NOTE in load_workbook(BytesIO(excel_call.args[1]))['Verification']['B2'].value
    st.info.assert_called_once_with(NOTE)
    preview.assert_called_once()
    assert 'PARTIAL TOTAL USD' in preview.call_args.args[0] and 'TOTAL USD VALUE OF ALL ACCOUNTS' not in preview.call_args.args[0]
    st = Mock()
    with patch.object(report, 'snapshot', side_effect=RuntimeError('SECRET DSN FILE PATH')), \
            patch.object(components, 'html') as preview:
        report.render(st)
    st.error.assert_called_once_with('Latest Import Balances is temporarily unavailable. Please try again.')
    st.download_button.assert_not_called()
    preview.assert_not_called()
    assert 'SECRET' not in str(st.mock_calls)
    print('PASS: actual authenticated main-app report dispatch/renderer; printable HTML, zero mutation')


def binary_storage():
    """Actual baseline SQLite REAL amounts stay visible and ineligible."""
    with tempfile.TemporaryDirectory(dir=os.environ['TEMP']) as root, patch.object(db,'DB_PATH',str(Path(root)/'binary.sqlite')):
        db.init_db()
        with closing(db.get_connection()) as c:
            c.execute("INSERT INTO account_list(account_name,bank,account_number,currency,rate_type) VALUES('Binary','Deposit','QA-REAL','USD','USD/USD')")
            c.execute("INSERT INTO statement_imports(statement_hash,imported_at,transaction_count) VALUES('qa-real','2026-10-05T09:00:00+00:00',0)")
            c.execute("INSERT INTO statement_balances(statement_hash,account_name,bank,account_number,currency,period_start,period_end,opening_balance,closing_balance) VALUES('qa-real','Binary','Deposit','QA-REAL','USD','2026-09-01','2026-09-30',17.43,17.43)")
            c.commit();before='\n'.join(c.iterdump())
        rows,total,warnings=snapshot(db)
        assert rows[0]['Closing balance']==Decimal('17.43') and total==0
        assert rows[0]['Verification']=='UNVERIFIED BINARY STORAGE' and rows[0]['Closing balance converted to USD']=='NOT AVAILABLE'
        assert any('excluded' in warning.lower() for warning in warnings)
        with closing(db.get_connection()) as c:assert '\n'.join(c.iterdump())==before
        print('PASS actual SQLite REAL storage stays visible, flagged and excluded; no invented precision or writes')


if __name__ == '__main__':
    main()
