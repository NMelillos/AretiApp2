"""Exact private sources, disposable storage, metadata-only routes and preservation."""
import ast,importlib.util,json,os,unittest
from contextlib import closing
from decimal import Decimal
from hashlib import sha256
from io import BytesIO
from pathlib import Path
from unittest.mock import patch
import db,parsing,streamlit as st
from _qa_post_import_message import CompletionTests
from _qa_completed_balances_22oct import CompletedTests
from _qa_amex_metadata_22oct import MetadataTests
from import_history import commit_statement
from duplicate_balance_import import prepare,record,BalanceImportError
from latest_import_balances import snapshot,print_document,workbook_bytes,screen_document
import flexline_balance_metadata as loc

BASELINE=Path(__file__).resolve().parent.parent/'areti-24oct-release-validation'

class NataliaTests(CompletedTests):
    def seed_natalia(self):
        path,content,info=self.source('natalia')
        account=next(a for a in json.loads((Path(os.environ['ARETI_ISOLATION_ROOT']).parents[1]/'current-accounts-private.json').read_text()) if a['bank']=='Revolut')
        with closing(db.get_connection()) as c:
            c.execute('INSERT INTO account_list(id,account_name,bank,account_number,currency,rate_type) VALUES(?,?,?,?,?,?)',tuple(account[k] for k in ('id','account_name','bank','account_number','currency','rate_type')));c.commit()
        frame,balance,matched=prepare(db,content,path.name)
        digest=sha256(content).hexdigest();mapped=db.apply_account_and_rates(frame,matched)
        self.assertEqual(commit_statement(db,mapped,path.name,digest,balance,matched)[0],167)
        return path,content,account,balance,digest

    def test_reproduced_blank_report_then_link_only_and_repeat(self):
        path,content,account,balance,digest=self.seed_natalia()
        before=self.protected()
        with closing(db.get_connection()) as c:
            values=c.execute('SELECT period_start,period_end,opening_balance,money_in,money_out,closing_balance,imported_at,updated_at FROM statement_balances WHERE statement_hash=?',(digest,)).fetchone()
        old=next(r for r in snapshot(db)[0] if r['Account number']==account['account_number'])
        self.assertIsNone(old['Closing balance'])
        self.assertEqual(record(db,content,path.name,'Synthetic UAT reviewer'),'linked')
        self.assertEqual(before,self.protected())
        with closing(db.get_connection()) as c:
            self.assertEqual(values,c.execute('SELECT period_start,period_end,opening_balance,money_in,money_out,closing_balance,imported_at,updated_at FROM statement_balances WHERE statement_hash=?',(digest,)).fetchone())
            self.assertEqual(c.execute('SELECT currency FROM account_list WHERE id=?',(account['id'],)).fetchone()[0],'')
        row=next(r for r in snapshot(db)[0] if r['Account number']==account['account_number'])
        self.assertEqual(row['Currency'],'EUR');self.assertEqual(row['Closing balance'],balance['closing_balance'])
        self.assertEqual(row['Statement end date'],balance['period_end'])
        self.assertNotEqual(row['Transaction Import Date'],'—');self.assertNotEqual(row['Balance Import Date'],'—')
        # Do not invent the missing Setup FX policy while repairing visibility.
        self.assertEqual(row['Closing balance converted to USD'],'NOT AVAILABLE')
        unchanged=self.snapshot();self.assertEqual(record(db,content,path.name,'Synthetic UAT reviewer'),'already recorded');self.assertEqual(unchanged,self.snapshot())

    def test_actual_missing_completion_preserves_transactions_and_independent_dates(self):
        path,content,account,balance,digest=self.seed_natalia()
        with closing(db.get_connection()) as c:c.execute('UPDATE statement_balances SET closing_balance=NULL WHERE statement_hash=?',(digest,));c.commit()
        before=self.protected()
        self.assertEqual(record(db,content,path.name,'Synthetic UAT reviewer'),'recorded')
        row=next(r for r in snapshot(db)[0] if r['Account number']==account['account_number'])
        self.assertEqual(row['Closing balance'],balance['closing_balance']);self.assertEqual(before,self.protected())

    def test_wrong_or_missing_proof_and_unrelated_currency_cannot_supply_row(self):
        path,content,account,_,digest=self.seed_natalia()
        self.assertEqual(record(db,content,path.name,'Synthetic UAT reviewer'),'linked')
        with closing(db.get_connection()) as c:
            c.execute("INSERT INTO statement_imports(statement_hash,statement_name,imported_at,transaction_count) VALUES('unrelated-usd','USD','2099-01-01T00:00:00+00:00',0)")
            c.execute("INSERT INTO statement_balances(statement_hash,account_name,bank,account_number,currency,period_start,period_end,opening_balance,closing_balance) VALUES('unrelated-usd',?,?,?,'USD','2026-09-01','2026-09-30',999,999)",(account['account_name'],account['bank'],account['account_number']));c.commit()
        row=next(r for r in snapshot(db)[0] if r['Account number']==account['account_number'])
        self.assertEqual(row['Currency'],'EUR');self.assertNotEqual(row['Closing balance'],Decimal('999'))
        with closing(db.get_connection()) as c:c.execute("UPDATE statement_balances SET notes=REPLACE(notes,?,?) WHERE statement_hash=?",(digest,'0'*64,digest));c.commit()
        self.assertIsNone(next(r for r in snapshot(db)[0] if r['Account number']==account['account_number'])['Closing balance'])

    def test_newer_incomplete_eur_not_hidden_by_older_linked_statement(self):
        path,content,account,_,_=self.seed_natalia()
        self.assertEqual(record(db,content,path.name,'Synthetic UAT reviewer'),'linked')
        with closing(db.get_connection()) as c:
            c.execute("INSERT INTO statement_imports(statement_hash,statement_name,imported_at,transaction_count) VALUES('new-incomplete','New EUR','2099-01-01T00:00:00+00:00',0)")
            c.execute("INSERT INTO statement_balances(statement_hash,account_name,bank,account_number,currency,period_end) VALUES('new-incomplete',?,?,?,'EUR','2026-10-31')",(account['account_name'],account['bank'],account['account_number']));c.commit()
        row=next(r for r in snapshot(db)[0] if r['Account number']==account['account_number'])
        self.assertEqual(row['Statement end date'],'2026-10-31');self.assertEqual(row['Status'],'INCOMPLETE');self.assertIsNone(row['Closing balance'])

    def test_post_link_ambiguous_setup_fails_closed(self):
        path,content,account,_,_=self.seed_natalia()
        self.assertEqual(record(db,content,path.name,'Synthetic UAT reviewer'),'linked')
        with closing(db.get_connection()) as c:
            c.execute('INSERT INTO account_list(account_name,bank,account_number,currency) VALUES(?,?,?,?)',('NATALIA FISHMAN',account['bank'],account['account_number'],''));c.commit()
        rows=[r for r in snapshot(db)[0] if r['Account number']==account['account_number']]
        self.assertTrue(all(r['Closing balance'] is None for r in rows))
        with self.assertRaises(ValueError):prepare(db,content,path.name)

class FlexlineTests(CompletionTests):
    def source(self):
        path=Path(os.environ['ARETI_PACKAGE_SOURCES'])/'flexline-original.pdf';content=path.read_bytes()
        self.assertEqual(sha256(content).hexdigest(),'c334c6b32d856e995d775659f47333130835e032b56d916af1b63319765a53e3')
        return path,content

    def seed(self):
        account=next(a for a in json.loads((Path(os.environ['ARETI_ISOLATION_ROOT']).parents[1]/'current-accounts-private.json').read_text()) if a['bank'].startswith('Fifth Third'))
        with closing(db.get_connection()) as c:
            c.execute('INSERT INTO account_list(id,account_name,bank,account_number,currency,rate_type) VALUES(?,?,?,?,?,?)',tuple(account[k] for k in ('id','account_name','bank','account_number','currency','rate_type')));c.commit()
        return account

    def test_exact_two_values_no_parser_no_transactions_and_copy_repeat(self):
        path,content=self.source();account=self.seed()
        source=loc.parse(content);self.assertEqual(source['period_end'],'2026-10-07');self.assertEqual(source['closing_balance'],Decimal('771915.46'))
        before=self.counts()
        with patch.object(parsing,'parse_pdf',side_effect=AssertionError('Transaction parser forbidden')):
            self.assertEqual(loc.record(db,content,path.name,'Synthetic UAT reviewer'),'recorded')
        self.assertEqual(self.counts()[0],before[0])
        with closing(db.get_connection()) as c:
            row=c.execute('SELECT period_start,period_end,opening_balance,money_in,money_out,closing_balance FROM statement_balances WHERE statement_hash=?',(sha256(content).hexdigest(),)).fetchone()
            self.assertEqual(row[0],'');self.assertEqual(row[1],'2026-10-07')
            self.assertEqual(row[2:5],(None,None,None));self.assertEqual(Decimal(str(row[5])),Decimal('771915.46'))
            self.assertEqual(c.execute('SELECT COUNT(*) FROM classified_transactions WHERE amount=5855.63').fetchone()[0],0)
        frozen=self.snapshot()
        self.assertEqual(loc.record(db,content,path.name,'Synthetic UAT reviewer'),'already recorded')
        self.assertEqual(loc.record(db,content+b'\n',path.name,'Synthetic UAT reviewer'),'already recorded')
        self.assertEqual(frozen,self.snapshot())
        row=next(r for r in snapshot(db)[0] if r['Account number']==account['account_number'])
        self.assertEqual(row['Closing balance'],Decimal('771915.46'));self.assertEqual(row['Closing balance converted to USD'],'NOT AVAILABLE')
        self.assertEqual(row['Status'],'INCOMPLETE')  # No invented reconciliation.

    def test_route_stops_before_generic_preview_and_pending_creation(self):
        path,content=self.source();self.seed();self.file=BytesIO(content);self.file.name=path.name
        frozen=self.snapshot()
        with patch.object(parsing,'parse_pdf',side_effect=AssertionError('Transaction parser forbidden')):
            app=self.run_app(self.app())
        self.assertTrue(any(b.label=='Import Line of Credit Balance Only' for b in app.button))
        self.assertFalse(any(b.label=='Import statement' for b in app.button));self.assertFalse(any('Prepared 1 transactions' in m.value for m in app.success))
        self.assertEqual(frozen,self.snapshot())

    def test_wrong_account_ambiguous_date_and_minimum_due_do_not_bypass(self):
        _,content=self.source();self.seed();text=loc.first_page(content)
        for bad in (text.replace('00548546519','00548546518'),text+'\nSTATEMENTDATE OCTOBER 07, 2026',text.replace('NEWBALANCE 771,915.46','NEWBALANCE 5,855.63\nNEWBALANCE 771,915.46')):
            with patch.object(loc,'first_page',return_value=bad):
                with self.assertRaises(ValueError):loc.parse(content)
        with closing(db.get_connection()) as c:c.execute("UPDATE account_list SET account_number='055-9000-0991 Credit line' WHERE bank=?",('Fifth Third (ex-Comerica)',));c.commit()
        with self.assertRaises(ValueError):loc.prepare(db,content)

    def test_rollback_existing_transaction_and_conflicting_balance_reject(self):
        path,content=self.source();self.seed();frozen=self.snapshot()
        with patch.object(db,'save_statement_balance',side_effect=RuntimeError('synthetic rollback')):
            with self.assertRaises(RuntimeError):loc.record(db,content,path.name,'Synthetic UAT reviewer')
        self.assertEqual(frozen,self.snapshot())
        self.assertEqual(loc.record(db,content,path.name,'Synthetic UAT reviewer'),'recorded')
        with closing(db.get_connection()) as c:c.execute('UPDATE statement_balances SET closing_balance=1 WHERE statement_hash=?',(sha256(content).hexdigest(),));c.commit()
        frozen=self.snapshot()
        with self.assertRaisesRegex(ValueError,'conflicts'):loc.record(db,content,path.name,'Synthetic UAT reviewer')
        self.assertEqual(frozen,self.snapshot())

class AmexUXTests(MetadataTests):
    def test_two_uploads_enable_named_action_and_zero_transaction_commit(self):
        path,pdf,csv,_,_=self.seed()
        self.file=BytesIO(pdf);self.file.name=path.name
        evidence=BytesIO(csv);evidence.name='existing.csv'
        upload=lambda *args,**kwargs:evidence if str(kwargs.get('key','')).startswith('amex_balance_csv_') else self.file
        before=self.protected()
        with patch.object(st,'file_uploader',side_effect=upload),patch.object(parsing,'parse_pdf',side_effect=AssertionError('PDF activity forbidden')):
            app=self.app().run();self.assertFalse(app.exception)
            self.assertTrue(any(b.label=='Import AMEX Balance Metadata' for b in app.button))
            identity=sha256(pdf).hexdigest()+sha256(csv).hexdigest()
            app.checkbox(key='amex_balance_confirm_'+identity).check().run()
            app.button(key='amex_balance_record').click().run()
        self.assertFalse(app.exception);self.assertTrue(any('Zero PDF/CSV transactions imported' in m.value for m in app.success))
        self.assertEqual(before,self.protected())

    def test_missing_csv_shows_clear_disabled_action(self):
        path,pdf,_,_,_=self.seed();self.file=BytesIO(pdf);self.file.name=path.name
        upload=lambda *args,**kwargs:None if str(kwargs.get('key','')).startswith('amex_balance_csv_') else self.file
        before=self.snapshot()
        with patch.object(st,'file_uploader',side_effect=upload):app=self.app().run()
        self.assertFalse(app.exception);self.assertTrue(app.button(key='amex_balance_record').disabled)
        self.assertTrue(any('Use the Screen Reader Optimized PDF.' in m.value for m in app.info));self.assertEqual(before,self.snapshot())

class PreservationTests(unittest.TestCase):
    def test_accepted_financial_and_parser_modules_and_exports_unchanged(self):
        here=Path(__file__).resolve().parent
        allowed={'duplicate_balance_import.py','amex_balance_metadata.py','latest_import_balances.py','latest_balances_compact.py'}
        for path in BASELINE.glob('*.py'):
            if 'qa' in path.name.lower() or path.name in allowed:continue
            self.assertEqual(path.read_text(encoding='utf-8-sig'),(here/path.name).read_text(encoding='utf-8-sig'),path.name)
        def funcs(path):return {n.name:ast.dump(n) for n in ast.parse(path.read_text(encoding='utf-8-sig')).body if isinstance(n,ast.FunctionDef)}
        before,after=funcs(BASELINE/'latest_import_balances.py'),funcs(here/'latest_import_balances.py')
        for name in before:
            if name not in ('snapshot','render'):self.assertEqual(before[name],after[name],name)
        before,after=funcs(BASELINE/'latest_balances_compact.py'),funcs(here/'latest_balances_compact.py')
        for name in before:
            if name!='_section_html':self.assertEqual(before[name],after[name],name)
        self.assertEqual((BASELINE/'balance_report_sections.py').read_bytes(),(here/'balance_report_sections.py').read_bytes())

    def test_screen_only_compaction_retains_download_document(self):
        original='<!doctype html><html><head></head><body><h1>Latest Import Balances</h1><table><td>123.45</td></table></body></html>'
        self.assertIn('padding:1px 3px',screen_document(original));self.assertIn('white-space:normal',screen_document(original))
        self.assertIn('<td>123.45</td>',screen_document(original));self.assertNotIn('balances-screen',original)

if __name__=='__main__':
    suite=unittest.TestSuite(cls(n) for cls in (NataliaTests,FlexlineTests,AmexUXTests,PreservationTests) for n in cls.__dict__ if n.startswith('test_'))
    result=unittest.TextTestRunner(verbosity=2).run(suite)
    Path(os.environ['ARETI_ISOLATION_ROOT'],'uat-followup-counts.json').write_text(json.dumps(dict(tests=result.testsRun,failures=len(result.failures),errors=len(result.errors),skipped=len(result.skipped))))
    raise SystemExit(not result.wasSuccessful())
