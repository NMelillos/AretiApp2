"""Actual login/rerun and local-only navigation regression checks."""
from contextlib import closing
from io import BytesIO
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import streamlit as st
from streamlit.testing.v1 import AppTest


class NavigationTests(unittest.TestCase):
    def prefix(self, page):
        # Execute the unchanged real auth gate and summary route; stop before DB imports.
        source = Path('app.py').read_text().split('# Keep heavy data/reporting imports', 1)[0] + '\nst.stop()\n'
        app = AppTest.from_string(source, default_timeout=30)
        app.query_params['page'] = page
        return app

    def login(self, app):
        app.text_input(key='login_username').set_value('Areti')
        app.text_input(key='login_password').set_value(os.environ['LOGIN_PASSWORD'])
        app.button[0].click().run()
        self.assertFalse(app.exception)

    def test_direct_url_real_login(self):
        app = self.prefix('Statement Summary').run()
        self.assertFalse(app.exception)
        self.assertEqual(len(app.text_input), 2)
        self.assertFalse(app.title)
        self.login(app)
        self.assertEqual(app.title[0].value, 'Statement Summary')
        self.assertEqual(app.query_params['page'], ['Statement Summary'])

    def test_lost_query_on_login_rerun(self):
        app = self.prefix('Statement Summary').run()
        app.query_params['page'] = 'Import'
        self.login(app)
        self.assertTrue(app.title, 'Summary login intent was lost after query fallback')
        self.assertEqual(app.title[0].value, 'Statement Summary')

    def test_encoded_summary_alias(self):
        app = self.prefix('Statement+Summary').run()
        self.login(app)
        self.assertTrue(app.title, 'Encoded summary query was ignored')
        self.assertEqual(app.title[0].value, 'Statement Summary')

    def test_authenticated_direct_url(self):
        app = self.prefix('Statement Summary')
        app.session_state['authenticated'] = True
        app.session_state['login_user'] = 'Synthetic reviewer'
        app.run()
        self.assertFalse(app.exception)
        self.assertEqual(app.title[0].value, 'Statement Summary')
        self.assertFalse(app.text_input)

    def test_authenticated_button_and_return(self):
        app = self.prefix('Import')
        app.session_state['authenticated'] = True
        app.session_state['login_user'] = 'Synthetic reviewer'
        app.run()
        self.assertFalse(app.exception)
        app.button(key='open_statement_summary').click().run()
        self.assertEqual(app.title[0].value, 'Statement Summary')
        self.assertTrue(app.session_state['authenticated'])
        app.button(key='return_from_summary').click().run()
        self.assertFalse(app.title)
        self.assertEqual(app.query_params['page'], ['Import'])
        self.assertTrue(app.session_state['authenticated'])

    def test_supplied_pdf_after_login(self):
        app = self.prefix('Statement Summary').run()
        self.login(app)
        with patch.object(st, 'file_uploader', return_value=BytesIO(Path(os.environ['ARETI_SUMMARY_SOURCE']).read_bytes())):
            app.run()
            app.button(key='extract_summary').click().run()
        self.assertFalse(app.exception)
        expected = __import__('json').loads(Path(os.environ['ARETI_SUMMARY_EXPECTED']).read_text())
        values = app.table[0].value['Value'].tolist()
        self.assertEqual(values, [expected['fields']['period_start']['value'], expected['fields']['period_end']['value'],
                                 expected['fields']['opening_balance']['value'] + ' ' + expected['currency'],
                                 expected['fields']['closing_balance']['value'] + ' ' + expected['currency']])

    def test_normal_import_login_and_no_writes(self):
        import db
        self.assertFalse(db.USING_POSTGRES)
        with tempfile.TemporaryDirectory(dir=os.environ['TEMP']) as root, patch.object(db, 'DB_PATH', str(Path(root)/'routing.sqlite')):
            db.init_db()
            with closing(db.get_connection()) as c:
                c.execute("INSERT INTO category_list(category,subcategory) VALUES('Synthetic','General')")
                c.execute("INSERT INTO account_list(account_name,bank,account_number,currency,rate_type) VALUES('Synthetic','Test','0001','USD','USD/USD')")
                c.execute("INSERT INTO rates(rate_month,rate_type,rate_value) VALUES('2026-08-01','USD/USD',1)")
                c.commit(); before='\n'.join(c.iterdump())
            app = AppTest.from_file('app.py', default_timeout=45)
            app.query_params['page'] = 'Import'
            app.run()
            self.login(app)
            self.assertIn('Import Statement', [v.value for v in app.subheader])
            self.assertTrue(app.button(key='open_statement_summary'))
            app.button(key='open_statement_summary').click().run()
            self.assertFalse(app.exception)
            self.assertEqual(app.title[0].value, 'Statement Summary')
            app.button(key='return_from_summary').click().run()
            self.assertFalse(app.exception)
            self.assertIn('Import Statement', [v.value for v in app.subheader])
            self.assertTrue(app.session_state['authenticated'])
            with closing(db.get_connection()) as c:
                self.assertEqual('\n'.join(c.iterdump()), before)


if __name__ == '__main__':
    unittest.main(verbosity=2)
