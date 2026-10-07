"""Local-only balance metadata package checks; original source facts stay private."""
from contextlib import closing
from decimal import Decimal
from io import BytesIO
import os
import json
from pathlib import Path
import unittest
from unittest.mock import patch
import db, parsing
from import_history import commit_statement
from _qa_post_import_message import CompletionTests
from _qa_boc_package import fixture
from boc_import import parse_pages
from latest_import_balances import snapshot


class BalanceTests(CompletionTests):
    def source(self, movements=(('credit','10.00','Synthetic credit'),)):
        page,text=fixture(movements)
        rows,balance=parse_pages([page],[text])
        frame=parsing._frame_from_pdf_rows(rows)
        account=dict(account_name='Synthetic BOC',bank='Bank of Cyprus',account_number=balance['account_number'],currency='EUR',rate_type='EUR/USD')
        with closing(db.get_connection()) as conn:
            conn.execute('INSERT INTO account_list(account_name,bank,account_number,currency,rate_type) VALUES(?,?,?,?,?)',tuple(account.values()));conn.commit()
            # Synthetic local rate only; normal UI continues to reject missing FX.
            conn.execute("INSERT INTO rates(rate_month,rate_type,rate_value) VALUES('2027-02-01','EUR/USD',1)");conn.commit()
        with patch.object(db,'get_accounts',return_value=__import__('pandas').DataFrame([account])):
            frame=db.apply_account_and_rates(frame,account)
        return frame,balance,account

    def test_verified_overlap_atomic_no_new_rows_history_report_duplicate(self):
        frame,balance,account=self.source()
        self.assertEqual(commit_statement(db,frame,'first.pdf','first',balance,account),(1,False,0))
        before=db.get_all_transactions().copy(deep=True)
        self.assertEqual(commit_statement(db,frame,'overlap.pdf','overlap',balance,account),(0,False,1))
        __import__('pandas').testing.assert_frame_equal(db.get_all_transactions(),before)
        history=db.get_import_history();self.assertEqual(len(history),2)
        overlap=history[history.statement_hash=='overlap'].iloc[0]
        self.assertEqual(overlap.transaction_count,0);self.assertEqual(overlap.reconciliation_status,'OK')
        row=next(row for row in snapshot(db)[0] if row['Account number']==account['account_number'])
        self.assertEqual(Decimal(str(row['Closing balance'])),Decimal('110.00'))
        after=self.snapshot();self.assertEqual(commit_statement(db,frame,'overlap.pdf','overlap',balance,account),(0,True,0));self.assertEqual(self.snapshot(),after)

    def test_unproven_duplicate_flag_and_rollback_cannot_create_balance(self):
        frame,balance,account=self.source();frame['dup_flag']=True
        before=self.snapshot()
        with self.assertRaisesRegex(ValueError,'no exact stored counterpart'):
            commit_statement(db,frame,'false.pdf','false',balance,account)
        self.assertEqual(self.snapshot(),before)
        frame['dup_flag']=False;commit_statement(db,frame,'first.pdf','first',balance,account)
        before=self.snapshot()
        with patch.object(db,'save_statement_balance',side_effect=ValueError('rollback')):
            with self.assertRaisesRegex(ValueError,'rollback'):commit_statement(db,frame,'overlap.pdf','overlap',balance,account)
        self.assertEqual(self.snapshot(),before)

    def test_reference_mutation_invalidates_balance_only_record(self):
        frame,balance,account=self.source();commit_statement(db,frame,'first.pdf','first',balance,account)
        commit_statement(db,frame,'overlap.pdf','overlap',balance,account)
        with closing(db.get_connection()) as conn:
            conn.execute("UPDATE classified_transactions SET amount='11.00' WHERE statement_hash='first'");conn.commit()
        self.assertNotIn('overlap',db.get_import_history().statement_hash.tolist())
        row=next(row for row in snapshot(db)[0] if row['Account number']==account['account_number'])
        self.assertEqual(row['Verification'],'INCOMPLETE METADATA')

    def test_older_zero_row_statement_does_not_displace_newer(self):
        frame,balance,account=self.source(())
        commit_statement(db,frame,'newer.pdf','newer',balance,account)
        page,text=fixture(())
        text=text.replace('01/02/2027 - 28/02/2027','01/01/2027 - 28/01/2027')
        rows,older=parse_pages([page],[text])
        empty=parsing._frame_from_pdf_rows(rows)
        commit_statement(db,empty,'older.pdf','older',older,account)
        rows,total,warnings=snapshot(db)
        row=next(row for row in rows if row['Account number']==account['account_number'])
        self.assertEqual(row['Statement end date'],'2027-02-28')
        self.assertTrue(any('older verified statement' in warning for warning in warnings))
        self.assertEqual(len(db.get_import_history()),2)

    def test_normal_upload_all_overlap_records_balance_without_transactions(self):
        import streamlit as st
        frame,balance,account=self.source();frame.attrs['statement_balance']=balance
        self.file=BytesIO(b'synthetic-source-original');self.file.name='source.csv'
        with patch.object(parsing,'parse_csv',return_value=frame):
            app=self.run_app(self.app());self.import_file(app);self.complete(app,1)
            self.file=BytesIO(b'synthetic-source-overlap');self.file.name='overlap.csv'
            app=self.run_app(self.app());before=db.get_all_transactions().copy(deep=True)
            self.import_file(app)
            self.assertFalse(app.error,[e.value for e in app.error])
            self.assertTrue(any('No new transactions were added' in e.value for e in app.success))
            self.assertEqual(self.counts(),(1,2,2))
            __import__('pandas').testing.assert_frame_equal(db.get_all_transactions(),before)


class CNBTests(CompletionTests):
    def original(self):
        expected=json.loads(Path(os.environ['CNB_EXPECTED']).read_text())
        path=Path(os.environ['CNB_SOURCE'])
        frame=parsing.parse_pdf(BytesIO(path.read_bytes()))
        account=dict(account_name='Source CNB QA',bank='CNB',account_number=expected['account'],currency='USD',rate_type='USD/USD')
        with closing(db.get_connection()) as conn:
            conn.execute('INSERT INTO account_list(account_name,bank,account_number,currency,rate_type) VALUES(?,?,?,?,?)',tuple(account.values()));conn.commit()
        return path,expected,frame,account

    def test_original_summary_and_rows_atomic_history_pending(self):
        path,expected,frame,account=self.original();balance=frame.attrs['statement_balance']
        for field in ('period_start','period_end'):self.assertEqual(balance[field],expected[field])
        for field in ('opening_balance','money_in','money_out','closing_balance'):self.assertEqual(balance[field],Decimal(expected[field]))
        self.assertEqual(frame.Date.tolist(),expected['dates']);self.assertEqual(list(map(lambda v:Decimal(str(v)),frame.Amount)),list(map(Decimal,expected['amounts'])))
        self.assertEqual(parsing.extract_statement_balance(BytesIO(path.read_bytes()),path.name),balance)
        frame=db.apply_account_and_rates(frame,account)
        before=self.snapshot()
        with patch.object(db,'save_statement_balance',side_effect=ValueError('CNB rollback')):
            with self.assertRaisesRegex(ValueError,'CNB rollback'):commit_statement(db,frame,path.name,'cnb-original',balance,account)
        self.assertEqual(self.snapshot(),before)
        self.assertEqual(commit_statement(db,frame,path.name,'cnb-original',balance,account),(3,False,0))
        history=db.get_import_history().iloc[0];self.assertEqual(history.reconciliation_status,'OK');self.assertEqual(history.balance_status,'OK')
        for field in ('opening_balance','money_in','money_out','closing_balance'):self.assertEqual(Decimal(str(history[field])),Decimal(expected[field]))
        self.assertEqual(len(db.get_pending_transactions()),3)
        after=self.snapshot();self.assertEqual(commit_statement(db,frame,path.name,'cnb-original',balance,account),(0,True,0));self.assertEqual(self.snapshot(),after)

    def test_cnb_source_and_commit_fail_closed(self):
        from cnb_import import CNBParseError
        from _qa_cnb_import import parse,TEXT
        with self.assertRaises(CNBParseError):parse(TEXT.replace('$250.00','$251.00'))
        path,expected,frame,account=self.original();frame=db.apply_account_and_rates(frame,account)
        balance=dict(frame.attrs['statement_balance']);balance['closing_balance']+=Decimal('0.01')
        before=self.snapshot()
        with self.assertRaises(CNBParseError):commit_statement(db,frame,path.name,'bad-cnb',balance,account)
        self.assertEqual(self.snapshot(),before)

    def test_cnb_normal_upload_uses_source_summary(self):
        path,expected,frame,account=self.original()
        import streamlit as st
        st.cache_data.clear();self.file=BytesIO(path.read_bytes());self.file.name=path.name
        before=self.snapshot();app=self.run_app(self.app());self.assertEqual(self.snapshot(),before)
        self.assertFalse(app.error,[e.value for e in app.error]);self.import_file(app);self.complete(app,3)
        history=db.get_import_history().iloc[0];self.assertEqual(history.period_start,expected['period_start']);self.assertEqual(history.period_end,expected['period_end']);self.assertEqual(history.reconciliation_status,'OK')
        row=next(row for row in snapshot(db)[0] if row['Account number']==account['account_number'])
        self.assertEqual(Decimal(str(row['Closing balance'])),Decimal(expected['closing_balance']))


class ReportTests(CompletionTests):
    def test_compact_currency_totals_exclusions_escaped_html(self):
        from latest_balances_compact import currency_totals,compact_html
        base={'Status':'IMPORTED','Verification':'SOURCE RECONCILIATION NOT VERIFIED','Account name':'<script>','Bank':'Synthetic'}
        rows=[dict(base,Currency='USD',**{'Closing balance':Decimal('10.10')}),dict(base,Currency='EUR',**{'Closing balance':Decimal('20.20')}),dict(base,Currency='USD',**{'Closing balance':Decimal('-0.10')})]
        rows.append(dict(base,Currency='USD',Verification='INCOMPLETE METADATA',**{'Closing balance':Decimal('999.99')}))
        self.assertEqual(currency_totals(rows),{'EUR':Decimal('20.20'),'USD':Decimal('10.00')})
        html=compact_html(rows);self.assertIn('font-size:12px',html);self.assertNotIn('<script>',html);self.assertIn('&lt;script&gt;',html)

    def test_third_report_button_read_only_and_no_backfill(self):
        import streamlit as st
        from streamlit.testing.v1 import AppTest
        app=AppTest.from_file('app.py',default_timeout=45)
        app.session_state['third_report_authenticated']=True
        app.session_state['third_report_user']='Synthetic report operator'
        app.query_params['page']='TB & NF Family Office Report'
        app.run();self.assertFalse(app.exception)
        before=self.snapshot()
        # ensure_usd_backfilled is an app wrapper; intercept its actual DB writer.
        with patch.object(db,'backfill_missing_usd_amounts',side_effect=AssertionError('Compact route attempted backfill')) as writer:
            app.button(key='third_latest_import_balances').click().run()
            writer.assert_not_called()
        self.assertFalse(app.exception,[e.message for e in app.exception]);self.assertFalse(app.error)
        self.assertIn('Latest Import Balances — compact list',[e.value for e in app.subheader])
        self.assertTrue(any('INTERIM PARTIAL REPORT' in e.value for e in app.info))
        self.assertEqual(self.snapshot(),before)
        app.button(key='third_latest_balances_return').click().run()
        self.assertFalse(app.exception);self.assertEqual(self.snapshot(),before)

    def test_unauthenticated_third_cannot_read_compact_balances(self):
        from streamlit.testing.v1 import AppTest
        app=AppTest.from_file('app.py',default_timeout=45)
        app.query_params['page']='TB & NF Family Office Report'
        before=self.snapshot();app.run()
        self.assertFalse(app.exception);self.assertFalse(any(b.label=='Latest Import Balances' for b in app.button));self.assertEqual(self.snapshot(),before)

    def test_compact_database_failure_does_not_expose_details(self):
        from latest_balances_compact import render
        from unittest.mock import Mock
        ui=Mock()
        with patch('latest_balances_compact.snapshot',side_effect=RuntimeError('PRIVATE CONNECTION DETAILS')):
            render(ui,db)
        ui.error.assert_called_once_with('Latest Import Balances is temporarily unavailable. Please try again.')
        ui.markdown.assert_not_called();self.assertNotIn('PRIVATE',str(ui.mock_calls))


class SmokeTests(CompletionTests):
    def test_protected_authenticated_pages_read_only(self):
        from streamlit.testing.v1 import AppTest
        for page in ('Setup','Reports','Corrections','Balances','Pending Review','Import History'):
            with self.subTest(page=page):
                app=AppTest.from_file('app.py',default_timeout=45)
                app.session_state['authenticated']=True;app.session_state['login_user']='Areti'
                app.query_params['page']=page
                before=self.snapshot();app.run()
                self.assertFalse(app.exception,[e.message for e in app.exception]);self.assertFalse(app.error,[e.value for e in app.error])
                self.assertEqual(self.snapshot(),before)


if __name__=='__main__':unittest.main()
