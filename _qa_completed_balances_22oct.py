"""Strict English Revolut/CNB completion; source financial evidence remains private."""
import hashlib,json,os,sqlite3,unittest
from pathlib import Path
from io import BytesIO
from contextlib import closing
from decimal import Decimal
from collections import Counter
from unittest.mock import patch
import pandas as pd
import db,parsing,streamlit as st
from _qa_post_import_message import CompletionTests
from duplicate_balance_import import prepare,record,BalanceImportError
from import_history import commit_statement

class CompletedTests(CompletionTests):
    def source(self,key):
        root=Path(os.environ['ARETI_ISOLATION_ROOT'])
        evidence=json.loads((root/'source-probe-private.json').read_text(encoding='utf-8'))
        info=next(r for r in evidence if r['key']==key)
        path=(root.parent/'0200046706.pdf') if key=='cnb' else Path(r'C:/Users/Student/Downloads')/info['name']
        return path,path.read_bytes(),info

    def setup_source(self,key):
        path,content,info=self.source(key)
        live=json.loads((Path(os.environ['ARETI_ISOLATION_ROOT'])/'import-state-private.json').read_text(encoding='utf-8'))
        account=next(a for a in live['accounts'] if str(a['id'])==('900' if key=='natalia' else '911'))
        with closing(db.get_connection()) as c:
            c.execute('INSERT INTO account_list(id,account_name,bank,account_number,currency,rate_type) VALUES(?,?,?,?,?,?)',tuple(account[k] for k in ('id','account_name','bank','account_number','currency','rate_type')))
            c.execute("INSERT OR IGNORE INTO rates(rate_month,rate_type,rate_value) VALUES('2026-09-01','EUR/USD',1.1)");c.commit()
        frame,balance,matched=prepare(db,content,path.name)
        mapped=db.apply_account_and_rates(frame,matched)
        digest=hashlib.sha256(content).hexdigest()
        self.assertEqual(commit_statement(db,mapped,path.name,digest,balance,matched)[0],len(frame))
        return path,content,info,balance,digest,account

    def protected(self):
        with closing(db.get_connection()) as c:
            names=[r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT IN ('statement_balances','sqlite_sequence')")]
            return {n:c.execute('SELECT * FROM '+n).fetchall() for n in names}

    def test_exact_sources_match_current_accounts_and_stored_movements(self):
        live=json.loads((Path(os.environ['ARETI_ISOLATION_ROOT'])/'import-state-private.json').read_text(encoding='utf-8'))
        for key in ('natalia','cnb'):
            with self.subTest(source=key):
                path,content,info,balance,digest,account=self.setup_source(key)
                frame,checked,matched=prepare(db,content,path.name)
                self.assertEqual(len(frame),len(info['rows']))
                for field in ('period_start','period_end','currency','account_number'):self.assertEqual(balance[field],info['balance'][field])
                for field in ('opening_balance','money_in','money_out','closing_balance'):self.assertEqual(balance[field],Decimal(info['balance'][field]))
                rows=[t for t in live['transactions'] if t['statement_hash']==digest and t['split_parent_id'] is None]
                current=Counter((str(t['txn_date']),Decimal(t['amount']),t['original_description'],t['bank'],t['account_number'],t['currency']) for t in rows)
                expected=Counter((str(t['Date']),Decimal(str(t['Amount'])),t['Description'],matched['bank'],matched['account_number'],matched['currency']) for t in frame.to_dict('records'))
                self.assertEqual(current,expected)
                self.assertEqual(db.get_accounts().loc[lambda d:d.id==account['id'],'currency'].iloc[0],account['currency'])

    def test_missing_balances_record_once_preserving_all_other_tables(self):
        for key in ('natalia','cnb'):
            path,content,_,balance,digest,_=self.setup_source(key)
            with closing(db.get_connection()) as c:c.execute('DELETE FROM statement_balances WHERE statement_hash=?',(digest,));c.commit()
            before=self.protected()
            self.assertEqual(record(db,content,path.name,'Synthetic operator'),'recorded')
            self.assertEqual(before,self.protected())
            with closing(db.get_connection()) as c:
                actual=c.execute('SELECT opening_balance,closing_balance,period_end FROM statement_balances WHERE statement_hash=?',(digest,)).fetchone()
            self.assertEqual(Decimal(str(actual[1])),balance['closing_balance']);self.assertEqual(actual[2],balance['period_end'])
            unchanged=self.snapshot();self.assertEqual(record(db,content,path.name,'Synthetic operator'),'already recorded');self.assertEqual(unchanged,self.snapshot())

    def test_existing_complete_balance_is_noop_and_conflicting_balance_rejects(self):
        for key in ('natalia','cnb'):
            path,content,_,_,digest,_=self.setup_source(key)
            before=self.snapshot();self.assertEqual(record(db,content,path.name,'Synthetic operator'),'already recorded');self.assertEqual(before,self.snapshot())
            with closing(db.get_connection()) as c:c.execute('UPDATE statement_balances SET closing_balance=closing_balance+1 WHERE statement_hash=?',(digest,));c.commit()
            before=self.snapshot()
            with self.assertRaisesRegex(BalanceImportError,'conflicts'):record(db,content,path.name,'Synthetic operator')
            self.assertEqual(before,self.snapshot())

    def test_transaction_changes_and_rollback_do_not_bypass_safeguards(self):
        for key in ('natalia','cnb'):
            path,content,_,_,digest,_=self.setup_source(key)
            with closing(db.get_connection()) as c:c.execute('DELETE FROM statement_balances WHERE statement_hash=?',(digest,));c.commit()
            with patch.object(db,'save_statement_balance',side_effect=RuntimeError('Synthetic persistence failure')):
                before=self.snapshot()
                with self.assertRaisesRegex(RuntimeError,'Synthetic'):record(db,content,path.name,'Synthetic operator')
                self.assertEqual(before,self.snapshot())
            with closing(db.get_connection()) as c:c.execute('UPDATE classified_transactions SET amount=amount+1 WHERE statement_hash=?',(digest,));c.commit()
            before=self.snapshot()
            with self.assertRaisesRegex(BalanceImportError,'do not match'):record(db,content,path.name,'Synthetic operator')
            self.assertEqual(before,self.snapshot())

    def test_currency_identity_ambiguity_and_stale_setup_fail_closed(self):
        path,content,_,_,digest,account=self.setup_source('natalia')
        checked=prepare(db,content,path.name)
        with closing(db.get_connection()) as c:
            c.execute('DELETE FROM statement_balances WHERE statement_hash=?',(digest,));c.execute("UPDATE account_list SET currency='USD' WHERE id=?",(account['id'],));c.commit()
        before=self.snapshot()
        with self.assertRaises(ValueError):prepare(db,content,path.name)
        with patch('duplicate_balance_import.prepare',return_value=checked):
            with self.assertRaisesRegex(BalanceImportError,'Setup account changed'):record(db,content,path.name,'Synthetic operator')
        self.assertEqual(before,self.snapshot())

    def test_duplicate_ui_offers_balance_preview_and_keeps_transaction_block(self):
        path,content,_,_,digest,_=self.setup_source('natalia')
        self.file=BytesIO(content);self.file.name=path.name
        before=self.snapshot();app=self.run_app(self.app())
        self.assertFalse(any(b.label=='Import statement' for b in app.button))
        with patch.object(st,'file_uploader',return_value=self.file):app.button(key='upload_balance_prepare').click().run()
        self.assertFalse(app.exception)
        self.assertFalse(any('validation failed' in e.value for e in app.error))
        self.assertTrue(app.button(key='upload_balance_record').disabled)
        self.assertEqual(before,self.snapshot())

    def test_available_cnb_source_normal_import_still_rejects_changed_movement(self):
        path,content,_,_,_,account=self.setup_source('cnb')
        frame,balance,matched=prepare(db,content,path.name)
        changed=frame.copy();changed['Amount']=changed['Amount'].map(lambda value:Decimal(str(value))+1)
        mapped=db.apply_account_and_rates(changed,matched);before=self.snapshot()
        with self.assertRaises(ValueError):commit_statement(db,mapped,'synthetic-bad-cnb.pdf','synthetic-bad-cnb',balance,matched)
        self.assertEqual(before,self.snapshot())

    def test_available_cnb_source_rejects_wrong_setup_currency(self):
        path,content,_,_,_,account=self.setup_source('cnb')
        with closing(db.get_connection()) as c:c.execute("UPDATE account_list SET currency='EUR' WHERE id=?",(account['id'],));c.commit()
        before=self.snapshot()
        with self.assertRaises(ValueError):prepare(db,content,path.name)
        self.assertEqual(before,self.snapshot())

if __name__=='__main__':
    names=[n for n in CompletedTests.__dict__ if n.startswith('test_')]
    raise SystemExit(not unittest.TextTestRunner(verbosity=2).run(unittest.TestSuite(CompletedTests(n) for n in names)).wasSuccessful())
