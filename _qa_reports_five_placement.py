"""Authenticated normal Reports placement; disposable local DB only."""
import ast
import os
from pathlib import Path
import unittest
from unittest.mock import patch
from _qa_post_import_message import CompletionTests
from streamlit.testing.v1 import AppTest
import db

class ReportsPlacementTests(CompletionTests):
    def reports(self):
        app=AppTest.from_file(os.environ.get('ARETI_UI_APP','app.py'),default_timeout=45)
        app.session_state['authenticated']=True
        app.session_state['login_user']='Synthetic reviewer'
        app.query_params['page']='Reports'
        app.run()
        self.assertFalse(app.exception)
        return app

    def test_authenticated_reports_button_compact_return_read_only(self):
        before=self.snapshot()
        app=self.reports()
        self.assertEqual(app.button(key='reports_latest_import_balances').label,'5. Latest Import Balances')
        with patch.object(db,'backfill_missing_usd_amounts',side_effect=AssertionError('Compact view must not backfill')):
            app.button(key='reports_latest_import_balances').click().run()
        self.assertFalse(app.exception)
        self.assertTrue(any('5. Latest Import Balances' in m.value for m in app.markdown))
        self.assertTrue(any('INTERIM PARTIAL REPORT' in m.value for m in app.caption))
        self.assertEqual(app.query_params['page'],['Reports'])
        self.assertEqual(self.snapshot(),before)
        app.button(key='reports_latest_balances_return').click().run()
        self.assertFalse(app.exception)
        self.assertTrue(any(s.value=='Sample Expenses Report' for s in app.subheader))
        self.assertEqual(self.snapshot(),before)

    def test_compact_error_keeps_return_and_no_write(self):
        app=self.reports(); before=self.snapshot()
        with patch('latest_balances_compact.snapshot',side_effect=RuntimeError('Synthetic read failure')):
            app.button(key='reports_latest_import_balances').click().run()
        self.assertFalse(app.exception)
        self.assertTrue(any('temporarily unavailable' in e.value for e in app.error))
        self.assertEqual(app.button(key='reports_latest_balances_return').label,'Return to Reports')
        self.assertEqual(self.snapshot(),before)
        app.button(key='reports_latest_balances_return').click().run()
        self.assertFalse(app.exception)

    def test_reports_requires_authentication_and_preserves_login_route(self):
        app=AppTest.from_file('app.py',default_timeout=45)
        app.query_params['page']='Reports'
        before=self.snapshot()
        app.run(); self.assertFalse(app.exception)
        self.assertFalse(any(b.key=='reports_latest_import_balances' for b in app.button))
        app.text_input(key='login_username').set_value('Areti')
        app.text_input(key='login_password').set_value(os.environ['LOGIN_PASSWORD'])
        app.button[0].click().run()
        self.assertFalse(app.exception)
        self.assertEqual(app.button(key='reports_latest_import_balances').label,'5. Latest Import Balances')
        self.assertEqual(self.snapshot(),before)

    def test_only_reports_prefix_changed(self):
        baseline=ast.parse(Path(os.environ['ARETI_REPORTS_BASELINE']).read_text(encoding='utf-8-sig'))
        current=ast.parse(Path('app.py').read_text(encoding='utf-8-sig'))
        branch=lambda tree: next(n for n in ast.walk(tree) if isinstance(n,ast.If) and ast.unparse(n.test)=="page == 'Reports'")
        old,new=branch(baseline),branch(current)
        self.assertEqual(len(new.body),len(old.body)+2)
        self.assertEqual([ast.dump(n) for n in new.body[2:]],[ast.dump(n) for n in old.body])
        reference=Path(os.environ['ARETI_REPORTS_REFERENCE'])
        for source in reference.glob('*.py'):
            if source.name!='app.py':
                self.assertEqual(Path(source.name).read_bytes(),source.read_bytes(),source.name)
        self.assertEqual(Path(db.__file__).resolve(),Path('db.py').resolve())
        new.body=old.body
        self.assertEqual(ast.dump(current),ast.dump(baseline))

if __name__=='__main__':
    suite=unittest.TestSuite(ReportsPlacementTests(name) for name in (
        'test_authenticated_reports_button_compact_return_read_only',
        'test_compact_error_keeps_return_and_no_write',
        'test_reports_requires_authentication_and_preserves_login_route','test_only_reports_prefix_changed'))
    result=unittest.TextTestRunner(verbosity=2).run(suite)
    raise SystemExit(not result.wasSuccessful())
