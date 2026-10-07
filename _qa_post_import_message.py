"""Priority 3 message state only; button-state work stays deferred."""
from contextlib import closing
from io import BytesIO
import ast
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import db
import parsing
import streamlit as st
from streamlit.testing.v1 import AppTest


class CompletionTests(unittest.TestCase):
    def setUp(self):
        self.assertFalse(db.USING_POSTGRES)
        self.root = tempfile.TemporaryDirectory(dir=os.environ['TEMP'])
        self.db_patch = patch.object(db, 'DB_PATH', str(Path(self.root.name)/'completion.sqlite'))
        self.db_patch.start()
        db.init_db()
        with closing(db.get_connection()) as conn:
            conn.execute("INSERT INTO category_list(category,subcategory) VALUES('Synthetic','General')")
            conn.execute("INSERT INTO account_list(account_name,bank,account_number,currency,rate_type) VALUES('Synthetic','Test','0001','USD','USD/USD')")
            conn.execute("INSERT INTO rates(rate_month,rate_type,rate_value) VALUES('2026-09-01','USD/USD',1)")
            conn.commit()
        st.cache_data.clear()
        st.cache_resource.clear()
        self.file = self.csv('first.csv', [('2026-09-10','Synthetic fee A','-12.34'),
                                          ('2026-09-11','Synthetic fee B','-2.00')])

    def tearDown(self):
        st.cache_data.clear()
        st.cache_resource.clear()
        self.db_patch.stop()
        self.root.cleanup()

    def csv(self, name, rows):
        file = BytesIO(('Date,Description,Amount\n'+'\n'.join(','.join(row) for row in rows)).encode())
        file.name = name
        return file

    def app(self):
        app = AppTest.from_file(os.environ.get('ARETI_UI_APP','app.py'), default_timeout=45)
        app.session_state['authenticated'] = True
        app.session_state['login_user'] = 'Synthetic reviewer'
        app.query_params['page'] = 'Import'
        return app

    def run_app(self, app):
        with patch.object(st, 'file_uploader', return_value=self.file):
            app.run()
        self.assertFalse(app.exception, [e.message for e in app.exception])
        return app

    def import_file(self, app):
        button = next(b for b in app.button if b.label == 'Import statement')
        with patch.object(st, 'file_uploader', return_value=self.file):
            button.click().run()
        self.assertFalse(app.exception, [e.message for e in app.exception])

    def counts(self):
        with closing(db.get_connection()) as conn:
            return tuple(conn.execute('SELECT COUNT(*) FROM '+t).fetchone()[0]
                         for t in ('classified_transactions','statement_imports','statement_balances'))

    def snapshot(self):
        with closing(db.get_connection()) as conn:
            return '\n'.join(conn.iterdump())

    def complete(self, app, count):
        self.assertFalse(app.error, [e.value for e in app.error])
        messages = [e.value for e in app.success]
        self.assertTrue(any(f'{count} uploaded transactions have been imported into the database' in m for m in messages),messages)
        self.assertFalse(any('nothing has been imported' in m for m in messages))

    def test_only_import_ui_branch_changed(self):
        baseline=ast.parse(Path(os.environ['ARETI_PROTECTED_BASELINE']).read_text())
        current=ast.parse(Path('app.py').read_text())
        find=lambda tree: next(n for n in tree.body if isinstance(n,ast.If) and ast.unparse(n.test)=="page == 'Import'")
        find(current).body=find(baseline).body
        # Only the explicit BOC reuse branch may differ in this shared helper.
        current_hint=next(n for n in current.body if isinstance(n,ast.FunctionDef) and n.name=='guess_account_index')
        prior_hint=next(n for n in baseline.body if isinstance(n,ast.FunctionDef) and n.name=='guess_account_index')
        reuse_try=next(n for n in ast.walk(current_hint) if isinstance(n,ast.Try))
        reuse=reuse_try.body[0]
        self.assertIsInstance(reuse,ast.If)
        expected="balance_info.get('source') == 'BOC bank columns' and balance_info.get('structural_validation') and balance_info.get('source_account_hint_text')"
        self.assertEqual(ast.dump(reuse.test),ast.dump(ast.parse(expected,mode='eval').body))
        self.assertEqual(ast.dump(reuse.body[0]),ast.dump(ast.parse("sample = balance_info['source_account_hint_text'] + '\\n' + sample").body[0]))
        self.assertEqual(len(reuse.body),1)
        reuse_try.body=reuse.orelse
        self.assertEqual(ast.dump(current_hint),ast.dump(prior_hint))
        self.assertEqual(ast.dump(current),ast.dump(baseline))

    def test_preview_does_not_write(self):
        before=self.snapshot()
        app=self.run_app(self.app())
        self.assertTrue(any('nothing has been imported' in m.value for m in app.success))
        self.assertTrue(any(b.label=='Import statement' for b in app.button))
        self.assertEqual(self.snapshot(),before)

    def test_success_and_completed_rerun(self):
        app=self.run_app(self.app())
        self.import_file(app)
        self.complete(app,2)
        self.assertEqual(self.counts(),(2,1,1))
        self.assertEqual(len(db.get_import_history()),1)
        self.assertEqual(db.get_pending_transactions().amount.astype(str).tolist(),['-12.34','-2.0'])
        before=self.snapshot()
        self.run_app(app)
        self.complete(app,2)
        self.assertEqual(self.snapshot(),before)

    def test_new_file_is_not_completed_receipt(self):
        app=self.run_app(self.app()); self.import_file(app)
        self.file=self.csv('second.csv',[('2026-09-12','New synthetic transaction','-4.56')])
        self.run_app(app)
        self.assertTrue(any('Prepared 1' in e.value for e in app.success))
        self.import_file(app); self.complete(app,1)
        self.assertEqual(self.counts(),(3,2,2))

    def test_atomic_failure_then_retry(self):
        app=self.run_app(self.app()); before=self.snapshot()
        with patch.object(db,'save_statement_balance',side_effect=ValueError('Synthetic save failure')):
            self.import_file(app)
        self.assertTrue(any('Synthetic save failure' in e.value for e in app.error))
        self.assertFalse(any('have been imported into the database' in e.value for e in app.success))
        self.assertEqual(self.snapshot(),before)
        self.assertNotIn('completed_statement_import_message',app.session_state)
        self.import_file(app); self.complete(app,2)
        self.assertEqual(self.counts(),(2,1,1))

    def test_fresh_session_duplicate_stays_blocked(self):
        app=self.run_app(self.app()); self.import_file(app)
        before=self.snapshot()
        other=self.run_app(self.app())
        self.assertTrue(any('already exists' in e.value for e in other.warning))
        self.assertFalse(other.success)
        self.assertFalse(any(b.label=='Import statement' for b in other.button))
        self.assertEqual(self.snapshot(),before)

    def test_duplicate_at_commit_has_no_false_success(self):
        app=self.run_app(self.app()); before=self.snapshot()
        with patch('import_history.commit_statement',return_value=(0,True,0)):
            self.import_file(app)
        self.assertTrue(any('already exists' in e.value for e in app.warning))
        self.assertFalse(any('have been imported into the database' in e.value for e in app.success))
        self.assertNotIn('completed_statement_import_message',app.session_state)
        self.assertEqual(self.snapshot(),before)

    def test_success_message_only_after_real_commit_returns(self):
        from import_history import commit_statement
        from streamlit.delta_generator import DeltaGenerator
        original_success=DeltaGenerator.success
        committed=False
        def commit(*args,**kwargs):
            nonlocal committed
            self.assertNotIn('completed_statement_import_message',app.session_state)
            result=commit_statement(*args,**kwargs)
            self.assertEqual(self.counts(),(2,1,1))
            committed=True
            return result
        def success(slot,body,*args,**kwargs):
            if 'have been imported into the database' in str(body):
                self.assertTrue(committed,'Success rendered before confirmed persistence')
            return original_success(slot,body,*args,**kwargs)
        app=self.run_app(self.app())
        with patch('import_history.commit_statement',side_effect=commit), patch.object(DeltaGenerator,'success',success):
            self.import_file(app)
        self.complete(app,2)
        # Priority 4 supersedes retention: completed previews have no import control.
        self.assertFalse(any(b.label=='Import statement' for b in app.button))
        self.assertFalse(any('before confirming Import statement' in e.value for e in app.warning))

    def test_overlap_count_uses_inserted_not_preview(self):
        app=self.run_app(self.app()); self.import_file(app)
        self.file=self.csv('overlap.csv',[('2026-09-10','Synthetic fee A','-12.34'),
                                          ('2026-09-12','New synthetic transaction','-4.56')])
        self.run_app(app); self.import_file(app); self.complete(app,1)
        self.assertTrue(any('Skipped 1 duplicate' in e.value for e in app.info))
        self.assertEqual(self.counts(),(3,2,2))

    def test_safra_completion_and_identity(self):
        from safra_page_qa import fixture
        from _qa_safra_uat import labelled_accounts
        rows=parsing._parse_safra_pages(fixture())
        accounts=labelled_accounts(rows)
        self.file=BytesIO(b'synthetic validated Safra fixture'); self.file.name='safra.csv'
        with patch.object(parsing,'parse_csv',return_value=rows), patch.object(db,'get_accounts',return_value=accounts):
            app=self.run_app(self.app()); self.import_file(app); self.complete(app,len(rows))
            self.assertEqual(self.counts(),(len(rows),4,4))
            saved=db.get_pending_transactions()
            self.assertEqual(len(saved),len(rows))
            # The unchanged persistence path retains the exact Setup label,
            # including "Current account USD / IBAN ..." in this fixture.
            expected_account=db._safra_page_accounts(rows,accounts)[2]['account_number']
            self.assertEqual(set(saved.account_number),{expected_account})
            before=self.snapshot(); self.run_app(app); self.assertEqual(self.snapshot(),before)

    def test_empty_safra_sections_do_not_claim_transactions(self):
        from _qa_safra_completion import fixture
        from _qa_safra_uat import labelled_accounts
        rows=parsing._parse_safra_pages(fixture(empty=True))
        self.assertTrue(rows.empty)
        accounts=labelled_accounts(rows)
        self.file=BytesIO(b'synthetic empty Safra fixture'); self.file.name='empty-safra.csv'
        with patch.object(parsing,'parse_csv',return_value=rows), patch.object(db,'get_accounts',return_value=accounts):
            app=self.run_app(self.app()); self.import_file(app)
            self.assertFalse(app.error,[e.value for e in app.error])
            self.assertTrue(any('No new transactions were added' in e.value for e in app.success))
            self.assertFalse(any('nothing has been imported' in e.value for e in app.success))
            self.assertEqual(self.counts(),(0,6,6))
            self.assertEqual(len(db.get_import_history()),6)
            before=self.snapshot(); self.run_app(app); self.assertEqual(self.snapshot(),before)


if __name__=='__main__':
    unittest.main(verbosity=2)
