"""Source-bound independent balances; disposable local SQL only."""
import ast,hashlib,json,os,sqlite3,unittest
from contextlib import closing
from decimal import Decimal
from io import BytesIO
from pathlib import Path
from unittest.mock import patch
import pandas as pd
import db,parsing,streamlit as st
from streamlit.testing.v1 import AppTest
from _qa_post_import_message import CompletionTests
from _qa_autonomous_workflows import BalanceActionTests
from _qa_balance_date_association import AssociationTests,ActualCompletionTests
from _qa_revolut_closure import ParserTests,document,iban
from duplicate_balance_import import record,prepare,BalanceImportError
from import_history import commit_statement
from latest_import_balances import snapshot

BASELINE=Path(__file__).resolve().parent.parent/'areti-balance-date-association'


class BalanceTests(CompletionTests):
    def original(self):
        path=Path(os.environ['REVOLUT_BUSINESS_SOURCE']);content=path.read_bytes()
        frame=parsing.parse_pdf(BytesIO(content));balance=frame.attrs['statement_balance'];digest=hashlib.sha256(content).hexdigest()
        expected=json.loads((Path(os.environ['ARETI_ISOLATION_ROOT'])/'revolut-source-expectations-private.json').read_text())
        self.assertEqual(len(frame),expected['transaction_count'])
        for key in ('opening_balance','money_in','money_out','closing_balance'):self.assertEqual(balance[key],Decimal(expected[key]))
        account=dict(account_name='Synthetic BProjects',bank='Revolut',account_number=balance['account_number'],currency='EUR',rate_type='EUR/USD')
        with closing(db.get_connection()) as c:
            c.execute('INSERT INTO account_list(account_name,bank,account_number,currency,rate_type) VALUES(?,?,?,?,?)',tuple(account.values()))
            c.execute("INSERT INTO rates(rate_month,rate_type,rate_value) VALUES('2026-09-01','EUR/USD',1.1)");c.commit()
        mapped=db.apply_account_and_rates(frame,account)
        self.assertEqual(commit_statement(db,mapped,path.name,digest,balance,account)[0],8)
        return path,content,digest,balance

    def protected(self):
        with closing(db.get_connection()) as c:
            tables=[r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT IN ('statement_balances','sqlite_sequence') ORDER BY name")]
            return {name:c.execute('SELECT * FROM '+name).fetchall() for name in tables}

    def uploaded_file(self,path,content):
        result=BytesIO(content);result.name=path.name;return result

    def test_actual_duplicate_ui_before_after_and_confirmed_balance_only(self):
        path,content,digest,balance=self.original()
        with closing(db.get_connection()) as c:c.execute('DELETE FROM statement_balances WHERE statement_hash=?',(digest,));c.commit()
        self.file=self.uploaded_file(path,content);before=self.protected()
        old=AppTest.from_file(str(BASELINE/'app.py'),default_timeout=45)
        old.session_state['authenticated']=True;old.session_state['login_user']='Synthetic reviewer';old.query_params['page']='Import'
        self.run_app(old)
        self.assertFalse(any(b.key=='upload_balance_prepare' for b in old.button))
        self.assertTrue(any('already exists' in m.value for m in old.warning))
        app=self.run_app(self.app())
        self.assertTrue(any(b.key=='upload_balance_prepare' for b in app.button))
        self.assertFalse(any(b.label=='Import statement' for b in app.button))
        with patch.object(st,'file_uploader',return_value=self.file):
            app.button(key='upload_balance_prepare').click().run()
            self.assertFalse(app.exception,[e.message for e in app.exception])
            self.assertTrue(app.button(key='upload_balance_record').disabled)
            self.assertEqual(self.protected(),before)
            app.checkbox(key='upload_balance_confirm_'+digest).check().run()
            app.button(key='upload_balance_record').click().run()
        self.assertFalse(app.exception,[e.message for e in app.exception])
        self.assertTrue(any('Balances recorded' in m.value for m in app.success))
        self.assertEqual(self.protected(),before)
        with closing(db.get_connection()) as c:self.assertEqual(c.execute('SELECT COUNT(*) FROM statement_balances WHERE statement_hash=?',(digest,)).fetchone()[0],1)
        self.assertTrue(db.statement_already_imported(digest))
        saved=self.snapshot();self.assertEqual(record(db,content,path.name,'Synthetic reviewer'),'already recorded');self.assertEqual(self.snapshot(),saved)

    def test_missing_balance_and_partial_completion_preserve_history_and_date(self):
        path,content,digest,balance=self.original()
        with closing(db.get_connection()) as c:
            c.execute("UPDATE statement_balances SET closing_balance=NULL, imported_at='2026-10-01T08:00:00+00:00',updated_at='2026-10-01T08:00:00+00:00' WHERE statement_hash=?",(digest,));c.commit()
        before=self.protected();date_before=next(r for r in snapshot(db)[0] if r['Account number']==balance['account_number'])['Transaction Import Date']
        self.assertEqual(record(db,content,path.name,'Synthetic reviewer'),'recorded')
        self.assertEqual(self.protected(),before)
        row=next(r for r in snapshot(db)[0] if r['Account number']==balance['account_number'])
        self.assertEqual(row['Transaction Import Date'],date_before)
        self.assertNotEqual(row['Balance Import Date'],'—')
        with closing(db.get_connection()) as c:
            self.assertEqual(Decimal(str(c.execute('SELECT closing_balance FROM statement_balances WHERE statement_hash=?',(digest,)).fetchone()[0])),balance['closing_balance'])
        saved=self.snapshot();self.assertEqual(record(db,content,path.name,'Synthetic reviewer'),'already recorded');self.assertEqual(self.snapshot(),saved)

    def test_transaction_mismatch_conflicting_balance_and_missing_header_fail_closed(self):
        path,content,digest,_=self.original()
        with closing(db.get_connection()) as c:c.execute('UPDATE statement_balances SET closing_balance=closing_balance+1 WHERE statement_hash=?',(digest,));c.commit()
        before=self.snapshot()
        with self.assertRaisesRegex(BalanceImportError,'conflicts'):record(db,content,path.name,'Synthetic reviewer')
        self.assertEqual(self.snapshot(),before)
        with closing(db.get_connection()) as c:
            c.execute('DELETE FROM statement_balances WHERE statement_hash=?',(digest,));c.execute('UPDATE classified_transactions SET amount=amount+1 WHERE statement_hash=?',(digest,));c.commit()
        before=self.snapshot()
        with self.assertRaisesRegex(BalanceImportError,'do not match'):record(db,content,path.name,'Synthetic reviewer')
        self.assertEqual(self.snapshot(),before)
        with closing(db.get_connection()) as c:c.execute('DELETE FROM statement_imports WHERE statement_hash=?',(digest,));c.commit()
        before=self.snapshot()
        with self.assertRaisesRegex(BalanceImportError,'normally first'):record(db,content,path.name,'Synthetic reviewer')
        self.assertEqual(self.snapshot(),before)

    def test_forced_rollback_retry_and_actor_guard(self):
        path,content,digest,_=self.original()
        with closing(db.get_connection()) as c:
            c.execute('DELETE FROM statement_balances WHERE statement_hash=?',(digest,))
            c.execute("CREATE TRIGGER reject_balance BEFORE INSERT ON statement_balances BEGIN SELECT RAISE(ABORT,'forced rollback'); END");c.commit()
        before=self.snapshot()
        with self.assertRaisesRegex(sqlite3.IntegrityError,'forced rollback'):record(db,content,path.name,'Synthetic reviewer')
        self.assertEqual(self.snapshot(),before)
        with self.assertRaisesRegex(BalanceImportError,'Authenticated'):record(db,content,path.name,'')
        with closing(db.get_connection()) as c:c.execute('DROP TRIGGER reject_balance');c.commit()
        self.assertEqual(record(db,content,path.name,'Synthetic reviewer'),'recorded')

    def test_zero_activity_source_creates_no_transactions_and_repeat_is_idempotent(self):
        import revolut_localized
        text=document().replace('Прибыль €200.00','Прибыль €0.00').replace('Расходы - €100.00','Расходы €0.00').replace('Конечный остаток €1 100.00','Конечный остаток €1 000.00')
        text='\n'.join(line for line in text.splitlines() if not line.startswith(('23 сент.','01 сент.')))
        frame=revolut_localized.parse([text]);self.assertTrue(frame.empty)
        with closing(db.get_connection()) as c:
            c.execute('INSERT INTO account_list(account_name,bank,account_number,currency,rate_type) VALUES(?,?,?,?,?)',('Synthetic zero','Revolut',iban(),'EUR','EUR/USD'));c.commit()
        before=db.get_all_transactions().copy(deep=True)
        with patch('parsing.parse_pdf',return_value=frame):
            self.assertEqual(record(db,b'validated synthetic empty source','empty.pdf','Synthetic reviewer'),'recorded')
            saved=self.snapshot();self.assertEqual(record(db,b'validated synthetic empty source','empty.pdf','Synthetic reviewer'),'already recorded');self.assertEqual(self.snapshot(),saved)
        pd.testing.assert_frame_equal(db.get_all_transactions(),before)

    def test_currency_identity_or_unverified_parser_cannot_record_balance(self):
        path,content,digest,_=self.original()
        with closing(db.get_connection()) as c:c.execute("UPDATE account_list SET currency='USD' WHERE bank='Revolut'");c.commit()
        before=self.snapshot()
        with self.assertRaisesRegex(BalanceImportError,'matching'):record(db,content,path.name,'Synthetic reviewer')
        self.assertEqual(self.snapshot(),before)

    def test_stale_setup_context_aborts_before_balance_write(self):
        path,content,digest,_=self.original()
        checked=prepare(db,content,path.name)
        with closing(db.get_connection()) as c:
            c.execute('DELETE FROM statement_balances WHERE statement_hash=?',(digest,))
            c.execute("UPDATE account_list SET currency='USD' WHERE bank='Revolut'");c.commit()
        before=self.snapshot()
        with patch('duplicate_balance_import.prepare',return_value=checked):
            with self.assertRaisesRegex(BalanceImportError,'Setup account changed'):record(db,content,path.name,'Synthetic reviewer')
        self.assertEqual(self.snapshot(),before)
        with patch('parsing.parse_pdf',return_value=pd.DataFrame()):
            with self.assertRaisesRegex(BalanceImportError,'source-verified'):record(db,b'unsupported','other.pdf','Synthetic reviewer')
        self.assertEqual(self.snapshot(),before)


class ScopeTests(unittest.TestCase):
    def test_all_preexisting_application_code_protected_except_exact_entry_and_audit_action(self):
        repo=Path(__file__).resolve().parent
        for path in BASELINE.glob('*.py'):
            if path.name.startswith('_qa') or path.name in ('app.py','latest_import_balances.py'):continue
            self.assertEqual(path.read_bytes(),(repo/path.name).read_bytes(),path.name)
        old=(BASELINE/'app.py').read_text(encoding='utf-8-sig');new=(repo/'app.py').read_text(encoding='utf-8-sig')
        addition='        from duplicate_balance_import import render_controls as render_balance_only_controls\n        render_balance_only_controls(st, file_bytes, uploaded_statement.name)\n'
        oldtree,newtree=ast.parse(old),ast.parse(new)
        oldscope=next(n for n in oldtree.body if isinstance(n,ast.FunctionDef) and n.name=='_third_report_group_scope')
        newscope=next(n for n in newtree.body if isinstance(n,ast.FunctionDef) and n.name=='_third_report_group_scope')
        self.assertEqual(new.replace(addition,'').replace(ast.get_source_segment(new,newscope),ast.get_source_segment(old,oldscope)),old)
        oldast=ast.parse((BASELINE/'latest_import_balances.py').read_text(encoding='utf-8-sig'));newast=ast.parse((repo/'latest_import_balances.py').read_text(encoding='utf-8-sig'))
        for tree in (oldast,newast):tree.body=[n for n in tree.body if not isinstance(n,ast.FunctionDef) or n.name!='_balance_import_date']
        self.assertEqual(ast.dump(oldast),ast.dump(newast))


def suite():
    tests=unittest.TestSuite();tests.addTests(unittest.defaultTestLoader.loadTestsFromTestCase(ScopeTests))
    for name in BalanceTests.__dict__:
        if name.startswith('test_'):tests.addTest(BalanceTests(name))
    for cls,names in (
      (BalanceActionTests,('test_original_balance_action_preserves_all_transactions_and_history_identity','test_empty_verified_source_creates_one_zero_count_history_and_balance')),
      (ParserTests,('test_localized_dates_signs_and_reconciliation','test_invalid_structure_never_falls_back','test_supported_english_layouts_and_currency_signs_unchanged')),
      (AssociationTests,('test_normal_import_has_three_distinct_date_sources','test_later_balance_record_does_not_replace_transaction_timestamp','test_selected_statement_owns_all_dates_when_older_import_arrives_later','test_verified_overlap_uses_its_referenced_transaction_history','test_source_bound_completion_audit_and_wrong_hash_rejection','test_invalid_or_outdated_audit_cannot_supply_date','test_ui_print_excel_share_balance_date_and_preserve_formatting')),
      (ActualCompletionTests,('test_balance_completion_audit_sets_date_without_rewriting_history_or_repeat',)),
    ):tests.addTests(cls(name) for name in names)
    return tests


if __name__=='__main__':
    result=unittest.TextTestRunner(verbosity=2).run(suite())
    Path(os.environ['ARETI_ISOLATION_ROOT'],'balance-test-counts.json').write_text(json.dumps(dict(tests=result.testsRun,failures=len(result.failures),errors=len(result.errors),skipped=len(result.skipped))))
    raise SystemExit(not result.wasSuccessful())
