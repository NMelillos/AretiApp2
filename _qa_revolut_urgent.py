"""Source-bound English Revolut context, with disposable-only import tests."""
import copy
from contextlib import closing
from decimal import Decimal
from io import BytesIO
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import pandas as pd
import parsing
import db
from import_history import commit_statement
from revolut_account_context import source_context, effective_account


ROOT = Path(os.environ['ARETI_PACKAGE_SOURCES'])


def source():
    return parsing.parse_pdf(BytesIO((ROOT/'urgent-revolut-original.pdf').read_bytes()))


def live():
    return json.loads((ROOT/'urgent-revolut-live-before-private.json').read_text(), parse_float=Decimal)


def balance():
    return parsing.extract_statement_balance(BytesIO((ROOT/'urgent-revolut-original.pdf').read_bytes()), 'urgent-original.pdf')


class SourceTests(unittest.TestCase):
    def test_exact_source_and_preserved_all_descriptions(self):
        frame = source()
        prior = json.loads((ROOT/'urgent-revolut-baseline-private.json').read_text())
        self.assertEqual(len(frame), 167)
        current = json.loads(json.dumps(frame.to_dict('records'), default=str))
        self.assertEqual(current, prior['rows'])
        self.assertEqual(frame.attrs['parse_diagnostics'], prior['attrs']['parse_diagnostics'])
        self.assertEqual(frame.attrs['parse_diagnostics']['reverted_rows'], 7)
        b = balance()
        self.assertEqual(b['opening_balance']+sum(frame.Amount), b['closing_balance'])
        self.assertEqual(sum(x for x in frame.Amount if x > 0), b['money_in'])
        self.assertEqual(-sum(x for x in frame.Amount if x < 0), b['money_out'])

    def test_source_currency_context_does_not_remap_setup(self):
        frame = source()
        account = live()['account']
        original = copy.deepcopy(account)
        resolved = effective_account(frame, balance(), account)
        self.assertEqual(resolved['currency'], 'EUR')
        self.assertEqual(account, original)
        self.assertEqual(resolved['rate_type'], '')
        self.assertEqual({k:v for k,v in resolved.items() if k != 'currency'}, {k:v for k,v in account.items() if k != 'currency'})

    def test_wrong_account_currency_bank_and_owner_rejected(self):
        frame = source()
        account = live()['account']
        for override in ({'account_number': account['account_number'][:-1]+'9'}, {'currency':'USD'}, {'bank':'Other'}, {'account_name':'Other owner'}):
            with self.subTest(override=override), self.assertRaises(parsing.RevolutBusinessParseError):
                effective_account(frame, balance(), dict(account, **override))

    def test_changed_dates_signs_descriptions_or_metadata_rejected(self):
        frame = source()
        for column, value in [('Amount',Decimal('22.48')), ('Date','2026-09-30'), ('Description','Changed')]:
            changed = frame.copy()
            changed.loc[0,column] = value
            with self.subTest(column=column), self.assertRaises(parsing.RevolutBusinessParseError):
                effective_account(changed, balance(), live()['account'])
        for key, value in [('currency','USD'), ('account_number','Wrong'), ('closing_balance',Decimal('7.00'))]:
            with self.subTest(key=key), self.assertRaises(parsing.RevolutBusinessParseError):
                effective_account(frame, dict(balance(), **{key:value}), live()['account'])

    def test_source_header_page_and_running_balance_rejections(self):
        pages = json.loads((ROOT/'urgent-revolut-pages-private.json').read_text())
        replacements = [('Page 1 of 11','Page 2 of 11'), ('IBAN '+live()['account']['account_number'],'IBAN LT003250028012133298'), ('EUR Statement','USD Statement'), ('€7,091.22','€7,091.23')]
        for old,new in replacements:
            altered = list(pages)
            altered[0] = altered[0].replace(old,new)
            self.assertNotEqual(altered,pages)
            with self.subTest(old=old), self.assertRaises(parsing.RevolutBusinessParseError):
                source_context(altered,source())


class StorageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=os.environ['ARETI_ISOLATION_ROOT'])
        self.patch = patch.object(db,'DB_PATH',str(Path(self.tmp.name)/'isolated.sqlite'))
        self.patch.start()
        self.assertFalse(db.USING_POSTGRES)
        db.init_db()
        self.account = live()['account']
        with closing(db.get_connection()) as conn:
            conn.execute('INSERT INTO account_list(id,account_name,bank,account_number,currency,rate_type,created_at) VALUES(?,?,?,?,?,?,?)',tuple(self.account[k] for k in ('id','account_name','bank','account_number','currency','rate_type','created_at')))
            conn.execute("INSERT INTO category_list(category,subcategory) VALUES('Synthetic','General')")
            for r in live()['rates']:
                conn.execute('INSERT INTO rates(rate_month,rate_type,rate_value) VALUES(?,?,?)',(r['rate_month'],r['rate_type'],str(r['rate_value'])))
            conn.commit()

    def tearDown(self):
        self.patch.stop()
        self.tmp.cleanup()

    def snapshot(self):
        with closing(db.get_connection()) as conn:
            return '\n'.join(conn.iterdump())

    def preview(self):
        frame = source()
        effective = effective_account(frame,balance(),self.account)
        mapped = db.apply_account_and_rates(frame,effective)
        effective_account(mapped,balance(),self.account)
        return mapped

    def test_import_pending_history_duplicates_and_setup_preservation(self):
        frame = self.preview()
        self.assertTrue(frame.currency.eq('EUR').all())
        self.assertTrue(frame.amount_usd.notna().all())
        self.assertEqual(commit_statement(db,frame,'synthetic-original.pdf','urgent-synthetic-source',balance(),self.account),(167,False,0))
        self.assertEqual(len(db.get_pending_transactions()),167)
        self.assertEqual(db.get_import_history().iloc[0].transaction_count,167)
        with closing(db.get_connection()) as conn:
            self.assertEqual(conn.execute('SELECT currency,rate_type FROM account_list WHERE id=?',(self.account['id'],)).fetchone(),('',''))
        before = self.snapshot()
        self.assertEqual(commit_statement(db,frame,'renamed.pdf','urgent-synthetic-source',balance(),self.account)[:2],(0,True))
        self.assertEqual(self.snapshot(),before)
        self.assertEqual(commit_statement(db,frame,'other-hash.pdf','urgent-synthetic-other',balance(),self.account)[0],0)
        self.assertEqual(self.snapshot(),before)

    def test_rollback_and_invalid_account_write_nothing(self):
        frame = self.preview()
        before = self.snapshot()
        with patch.object(db,'save_statement_balance',side_effect=ValueError('Synthetic persistence failure')):
            with self.assertRaisesRegex(ValueError,'Synthetic persistence failure'):
                commit_statement(db,frame,'synthetic.pdf','rollback-source',balance(),self.account)
        self.assertEqual(self.snapshot(),before)
        with self.assertRaises(parsing.RevolutBusinessParseError):
            commit_statement(db,frame,'synthetic.pdf','wrong-account-source',balance(),dict(self.account,currency='USD'))
        self.assertEqual(self.snapshot(),before)

    def test_actual_ui_preview_and_import(self):
        import streamlit as st
        from streamlit.testing.v1 import AppTest
        file = BytesIO((ROOT/'urgent-revolut-original.pdf').read_bytes())
        file.name = 'urgent-original.pdf'
        st.cache_data.clear()
        st.cache_resource.clear()
        app = AppTest.from_file('app.py',default_timeout=45)
        app.session_state['authenticated'] = True
        app.session_state['login_user'] = 'Synthetic reviewer'
        app.query_params['page'] = 'Import'
        before = self.snapshot()
        try:
            with patch.object(st,'file_uploader',return_value=file):
                app.run()
            self.assertFalse(app.exception,[e.message for e in app.exception])
            self.assertFalse(app.error,[e.value for e in app.error])
            self.assertTrue(any('Prepared 167 transactions' in e.value for e in app.success))
            self.assertEqual(self.snapshot(),before)
            button = next(b for b in app.button if b.label=='Import statement')
            with patch.object(st,'file_uploader',return_value=file):
                button.click().run()
            self.assertFalse(app.exception,[e.message for e in app.exception])
            self.assertFalse(app.error,[e.value for e in app.error])
            self.assertTrue(any('167 uploaded transactions have been imported' in e.value for e in app.success))
            self.assertEqual(len(db.get_pending_transactions()),167)
        finally:
            st.cache_data.clear()
            st.cache_resource.clear()


if __name__=='__main__':
    unittest.main(verbosity=2)
