from contextlib import closing
from decimal import Decimal
from hashlib import sha256
from io import BytesIO
import os, json, sqlite3
from pathlib import Path
from unittest.mock import patch, Mock
import unittest
import db, streamlit as st
from streamlit.testing.v1 import AppTest
from _qa_post_import_message import CompletionTests
from import_history import commit_statement
from boc_summary_balances import prepare, record
from latest_balances_compact import compact_html,compact_model,render

ROOT=Path(os.environ['ARETI_PACKAGE_SOURCES'])

class BalanceActionTests(CompletionTests):
    def original(self):
        p=Path(os.environ['BOC_BALANCE_ONLY_SOURCE'])
        content=p.read_bytes(); frame,balance=prepare(content)
        account=dict(account_name='Synthetic BOC source',bank='Bank of Cyprus',account_number=balance['account_number'],currency=balance['currency'],rate_type='USD/USD')
        with closing(db.get_connection()) as conn:
            conn.execute('INSERT INTO account_list(account_name,bank,account_number,currency,rate_type) VALUES(?,?,?,?,?)',tuple(account.values()));conn.commit()
        frame=db.apply_account_and_rates(frame,account)
        digest=sha256(content).hexdigest()
        commit_statement(db,frame,p.name,digest,balance,account)
        with closing(db.get_connection()) as conn:
            conn.execute("UPDATE statement_balances SET period_start='',period_end='',opening_balance=NULL,money_in=NULL,money_out=NULL,closing_balance=NULL WHERE statement_hash=?",(digest,));conn.commit()
        return p,content,frame,balance,digest
    def unchanged_transactions(self):
        with closing(db.get_connection()) as conn:
            return conn.execute('SELECT * FROM classified_transactions ORDER BY id').fetchall(),conn.execute('SELECT * FROM transaction_memory ORDER BY id').fetchall(),conn.execute('SELECT * FROM statement_imports ORDER BY id').fetchall()

    def test_original_balance_action_preserves_all_transactions_and_history_identity(self):
        path,content,frame,balance,digest=self.original();before=self.unchanged_transactions()
        self.assertEqual(record(db,content,path.name,'Synthetic operator'),'recorded')
        self.assertEqual(self.unchanged_transactions(),before)
        row=db.get_import_history().iloc[0]
        self.assertEqual(row.reconciliation_status,'OK');self.assertEqual(row.balance_status,'OK')
        self.assertEqual(Decimal(str(row.closing_balance)),balance['closing_balance'])
        self.assertTrue(db.statement_already_imported(digest))
        saved=self.snapshot();self.assertEqual(record(db,content,path.name,'Synthetic operator'),'already recorded');self.assertEqual(self.snapshot(),saved)
        self.assertEqual(commit_statement(db,frame,path.name,digest,balance,dict(account_number=balance['account_number'],currency='USD')),(0,True,0))

    def test_conflicts_missing_rows_actor_and_rollback_fail_safely(self):
        path,content,_,balance,digest=self.original()
        before=self.snapshot()
        with self.assertRaisesRegex(ValueError,'Authenticated'):record(db,content,path.name,'')
        self.assertEqual(self.snapshot(),before)
        with closing(db.get_connection()) as conn:
            conn.execute('UPDATE statement_balances SET closing_balance=? WHERE statement_hash=?',(balance['closing_balance']+Decimal(1),digest));conn.commit()
        before=self.snapshot()
        with self.assertRaisesRegex(ValueError,'conflicts'):record(db,content,path.name,'Synthetic operator')
        self.assertEqual(self.snapshot(),before)
        with closing(db.get_connection()) as conn:
            conn.execute('UPDATE statement_balances SET closing_balance=NULL WHERE statement_hash=?',(digest,))
            conn.execute("CREATE TRIGGER reject_balance_update BEFORE UPDATE ON statement_balances BEGIN SELECT RAISE(ABORT,'forced rollback'); END");conn.commit()
        before=self.snapshot()
        with self.assertRaisesRegex(sqlite3.IntegrityError,'forced rollback'):record(db,content,path.name,'Synthetic operator')
        self.assertEqual(self.snapshot(),before)
        with closing(db.get_connection()) as conn:
            conn.execute('DROP TRIGGER reject_balance_update')
            conn.execute('UPDATE classified_transactions SET amount=amount+1 WHERE statement_hash=?',(digest,));conn.commit()
        before=self.snapshot()
        with self.assertRaisesRegex(ValueError,'Stored source rows'):record(db,content,path.name,'Synthetic operator')
        self.assertEqual(self.snapshot(),before)

    def test_actual_summary_preview_cancel_confirmation_and_stale_file(self):
        path,content,_,_,_=self.original();file=BytesIO(content);file.name=path.name
        app=AppTest.from_file('app.py',default_timeout=45);app.session_state['authenticated']=True;app.session_state['login_user']='Synthetic operator';app.query_params['page']='Statement Summary'
        before=self.snapshot()
        with patch.object(st,'file_uploader',return_value=file):
            app.run();app.button(key='extract_summary').click().run()
            self.assertFalse(app.exception);self.assertEqual(self.snapshot(),before)
            app.button(key='summary_prepare_balances').click().run()
            self.assertFalse(app.exception);self.assertEqual(self.snapshot(),before)
            self.assertTrue(app.button(key='summary_record_balances').disabled)
            app.checkbox(key='summary_balance_confirm_'+sha256(content).hexdigest()).check().run()
            app.button(key='summary_record_balances').click().run()
            self.assertFalse(app.exception,[e.message for e in app.exception]);self.assertFalse(app.error)
            self.assertTrue(any('Balance metadata recorded' in m.value for m in app.success))
            self.assertFalse(any(b.key=='summary_record_balances' for b in app.button))
        new_file=BytesIO(b'not a PDF');new_file.name='other.pdf'
        with patch.object(st,'file_uploader',return_value=new_file):app.run()
        self.assertFalse(app.success);self.assertNotIn('summary_balance_preview',app.session_state)

    def test_new_activity_requires_normal_upload_and_missing_identity_filled(self):
        path,content,frame,balance,digest=self.original()
        with closing(db.get_connection()) as conn:
            conn.execute('DELETE FROM classified_transactions WHERE statement_hash=?',(digest,))
            conn.execute('DELETE FROM statement_balances WHERE statement_hash=?',(digest,))
            conn.execute('DELETE FROM statement_imports WHERE statement_hash=?',(digest,));conn.commit()
        before=self.snapshot()
        with self.assertRaisesRegex(ValueError,'normal Upload'):record(db,content,path.name,'Synthetic operator')
        self.assertEqual(self.snapshot(),before)
        account=db.get_accounts().query("bank == 'Bank of Cyprus'").iloc[0].to_dict()
        commit_statement(db,frame,path.name,digest,balance,account)
        with closing(db.get_connection()) as conn:
            conn.execute("UPDATE statement_balances SET bank='',account_number='',currency='',account_name='' WHERE statement_hash=?",(digest,));conn.commit()
        transactions=self.unchanged_transactions()
        self.assertEqual(record(db,content,path.name,'Synthetic operator'),'recorded')
        self.assertEqual(self.unchanged_transactions(),transactions)
        self.assertEqual(db.get_import_history().iloc[0].account_number,balance['account_number'])

    def test_empty_verified_source_creates_one_zero_count_history_and_balance(self):
        from _qa_boc_package import fixture
        from boc_import import parse_pages
        import parsing
        page,text=fixture();rows,balance=parse_pages([page],[text]);frame=parsing._frame_from_pdf_rows(rows)
        account=dict(account_name='Synthetic empty',bank='Bank of Cyprus',account_number=balance['account_number'],currency='EUR',rate_type='EUR/USD')
        with closing(db.get_connection()) as conn:
            conn.execute('INSERT INTO account_list(account_name,bank,account_number,currency,rate_type) VALUES(?,?,?,?,?)',tuple(account.values()));conn.commit()
        with patch('boc_summary_balances.prepare',return_value=(frame,balance)):
            self.assertEqual(record(db,b'validated synthetic zero source','zero.pdf','Synthetic operator'),'recorded')
            self.assertEqual(self.counts(),(0,1,1))
            saved=self.snapshot()
            self.assertEqual(record(db,b'validated synthetic zero source','zero.pdf','Synthetic operator'),'already recorded')
            self.assertEqual(self.snapshot(),saved)

class CompactTests(CompletionTests):
    def rows(self):
        base={'Status':'IMPORTED','Verification':'SOURCE RECONCILIATION NOT VERIFIED','Account name':'Synthetic <script>','Currency':'USD','Closing balance':Decimal('10.00'),'Closing balance converted to USD':Decimal('10.00')}
        return [base,dict(base,Currency='EUR',**{'Closing balance':Decimal('20.00'),'Closing balance converted to USD':Decimal('24.00')}),dict(base,Currency='GBP',**{'Closing balance':Decimal('3.00'),'Closing balance converted to USD':'NOT AVAILABLE'}),dict(base,Verification='LIABILITY CONVENTION UNVERIFIED',**{'Closing balance':Decimal('99.00'),'Closing balance converted to USD':Decimal('99.00')})]
    def test_visible_usd_sum_missing_fx_exclusion_no_wrap_or_warning_wall(self):
        rows=self.rows();visible,total,missing,unverified=compact_model(rows)
        self.assertEqual(total,Decimal('34.00'));self.assertEqual(missing,1);self.assertEqual(unverified,1)
        self.assertEqual(total,sum((r['usd'] for r in visible if r['usd'] is not None),Decimal(0)))
        html=compact_html(rows);self.assertIn('white-space:nowrap',html);self.assertIn('font-size:10px',html);self.assertIn('USD TOTAL',html);self.assertIn('34.00',html)
        self.assertNotIn('<script>',html);self.assertNotIn('Statement end date',html);self.assertNotIn('Verification',html)
        ui=Mock()
        with patch('latest_balances_compact.snapshot',return_value=(rows,Decimal('34.00'),['DO NOT SHOW legacy timestamp diagnostic'])):render(ui,db)
        ui.warning.assert_not_called();ui.info.assert_not_called();self.assertNotIn('DO NOT SHOW',str(ui.mock_calls));self.assertIn('no approved FX rate',str(ui.caption.call_args))

    def test_actual_section_five_button_auth_read_only_and_return(self):
        app=AppTest.from_file('app.py',default_timeout=45);app.query_params['page']='TB & NF Family Office Report';app.session_state['third_report_authenticated']=True;app.session_state['third_report_user']='Synthetic report operator'
        app.run();self.assertFalse(app.exception)
        self.assertEqual(app.button(key='third_latest_import_balances').label,'5. Latest Import Balances')
        before=self.snapshot()
        with patch.object(db,'backfill_missing_usd_amounts',side_effect=AssertionError('Compact route must not write')):
            app.button(key='third_latest_import_balances').click().run()
        self.assertFalse(app.exception);self.assertFalse(app.warning);self.assertEqual(self.snapshot(),before)
        self.assertIn('5. Latest Import Balances',[e.value for e in app.subheader])
        app.button(key='third_latest_balances_return').click().run();self.assertFalse(app.exception);self.assertEqual(self.snapshot(),before)

if __name__=='__main__':unittest.main()
