"""Localized source, strict guards and disposable import/storage regressions."""
import ast
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
import revolut_localized as localized
from _qa_revolut_business import TEXT, parse as parse_english


def iban():
    bban = '1000011101001000'
    return 'LT' + str(98 - int(bban + '212900') % 97).zfill(2) + bban


def document():
    return '\n'.join([
        'Revolut Bank UAB', 'Выписка со счета', 'Synthetic entity',
        'IBAN ' + iban(), 'Валюта EUR',
        'Начальный баланс €1 000.00', 'Прибыль €200.00',
        'Расходы - €100.00', 'Конечный остаток €1 100.00',
        'Операции от 1 сентября 2026 г. перед 30 сентября 2026 г.',
        'Дата (UTC) Описание Расходы Прибыль Баланс',
        '23 сент. 2026 Synthetic purchase €100.00 €1 100.00',
        '01 сент. 2026 Synthetic receipt €200.00 €1 200.00',
        'Типы операций', '1/1',
    ])


def account(currency='EUR'):
    return dict(account_name='Synthetic entity', bank='Revolut',
                account_number=iban(), currency=currency, rate_type='')


def frame():
    result = localized.parse([document()])
    result['account_number'] = iban()
    result['account_name'] = 'Synthetic entity'
    result['bank'] = 'Revolut'
    result['currency'] = 'EUR'
    return result


class ParserTests(unittest.TestCase):
    def test_original_pdf_exact_rows_summary_and_identity(self):
        source = Path(os.environ['ARETI_REVOLUT_SOURCE'])
        expected = json.loads(Path(os.environ['ARETI_REVOLUT_EXPECTED']).read_text())[source.name]
        result = parsing.parse_pdf(BytesIO(source.read_bytes()))
        summary = result.attrs['statement_balance']
        self.assertEqual(len(result), 8)
        self.assertEqual(result.Amount.tolist(), list(map(Decimal, expected['amounts'])))
        for key in ('bank', 'account_number', 'currency', 'period_start', 'period_end'):
            self.assertEqual(summary[key], expected[key])
        for key in ('opening_balance', 'money_in', 'money_out', 'closing_balance'):
            self.assertEqual(summary[key], Decimal(expected[key]))
        self.assertEqual(summary['opening_balance'] + sum(result.Amount), summary['closing_balance'])
        self.assertEqual(parsing.extract_statement_balance(BytesIO(source.read_bytes()), source.name), summary)
        result['account_number'] = expected['account_number']
        result['currency'] = 'EUR'
        localized.validate_preview(result, summary, dict(bank='Revolut', account_number=expected['account_number'], currency='EUR'))

    def test_localized_dates_signs_and_reconciliation(self):
        result = frame()
        self.assertEqual(result.Date.tolist(), ['2026-09-23', '2026-09-01'])
        self.assertEqual(result.Amount.tolist(), [Decimal('-100'), Decimal('200')])
        localized.validate_preview(result, result.attrs['statement_balance'], account())

    def test_invalid_structure_never_falls_back(self):
        from _qa_revolut_business import Document
        original = document()
        changes = [('IBAN ' + iban(), 'IBAN LT001000011101001000'),
                   ('Валюта EUR', 'Валюта USD'), ('1/1', '2/1'),
                   ('23 сент. 2026', '32 сент. 2026'),
                   ('23 сент. 2026', '23 окт. 2026'),
                   ('Конечный остаток €1 100.00', 'Конечный остаток €1 101.00'),
                   ('Дата (UTC) Описание Расходы Прибыль Баланс', 'Unknown columns')]
        for old, new in changes:
            with self.subTest(change=old), patch.object(parsing.pdfplumber, 'open', return_value=Document(original.replace(old, new))), patch.object(parsing, '_parse_generic_pdf_text') as generic, patch.object(parsing, 'convert_from_bytes') as ocr:
                with self.assertRaises(parsing.RevolutBusinessParseError):
                    parsing.parse_pdf(BytesIO(b'synthetic'))
                generic.assert_not_called()
                ocr.assert_not_called()

    def test_preview_account_currency_and_movements_are_strict(self):
        result = frame()
        summary = result.attrs['statement_balance']
        for override in ({'account_number': iban()[:-1]+'9'}, {'currency': ''}, {'currency': 'USD'}, {'bank': 'Other'}):
            with self.subTest(override=override), self.assertRaises(parsing.RevolutBusinessParseError):
                localized.validate_preview(result, summary, dict(account(), **override))
        changed = result.copy()
        changed.loc[0, 'Amount'] = Decimal('-99')
        with self.assertRaises(parsing.RevolutBusinessParseError):
            localized.validate_preview(changed, summary, account())

    def test_supported_english_layouts_and_currency_signs_unchanged(self):
        for symbol, currency in [('€', 'EUR'), ('£', 'GBP'), ('$', 'USD')]:
            result = parse_english(TEXT.replace('€', symbol), 'synthetic.pdf')
            self.assertEqual(result.Amount.tolist(), [Decimal('-100'), Decimal('200')])
            self.assertTrue(result.statement_currency.eq(currency).all())
        legacy = 'Revolut Bank\nEUR Statement\nAccount (Current Account) €100.00 €0.00 €10.00 €90.00\nAccount transactions from July 1, 2026 to July 31, 2026\nJul 1, 2026 Example purchase €10.00 €90.00\n'
        self.assertEqual(parsing._parse_revolut_pdf_text(legacy)[0][2], Decimal('-10'))
        for text in (TEXT.replace('1 100.00', '1 101.00'), TEXT.replace('23 Jul 2026', '32 Jul 2026')):
            with self.assertRaises(parsing.RevolutBusinessParseError):
                parse_english(text, 'invalid.pdf')

    def test_real_app_preview_hook_is_present(self):
        tree = ast.parse(Path('app.py').read_text(encoding='utf-8'))
        branch = next(n for n in ast.walk(tree) if isinstance(n, ast.If) and ast.unparse(n.test) == "balance_info.get('source') == 'Russian Revolut Business'")
        self.assertEqual(ast.unparse(branch.body[-1]), 'validate_revolut(parsed, balance_info, selected_account)')


class StorageTests(unittest.TestCase):
    def setUp(self):
        import db
        self.db = db
        self.tmp = tempfile.TemporaryDirectory(dir=os.environ['ARETI_ISOLATION_ROOT'])
        self.dbpatch = patch.object(db, 'DB_PATH', str(Path(self.tmp.name)/'disposable.sqlite'))
        self.dbpatch.start()
        self.assertFalse(db.USING_POSTGRES)
        db.init_db()

    def tearDown(self):
        self.dbpatch.stop()
        self.tmp.cleanup()

    def snapshot(self):
        with closing(self.db.get_connection()) as conn:
            return '\n'.join(conn.iterdump())

    def test_import_history_pending_and_duplicates(self):
        from import_history import commit_statement
        result = frame()
        summary = result.attrs['statement_balance']
        self.assertEqual(commit_statement(self.db, result, 'synthetic.pdf', 'synthetic-fingerprint', summary, account()), (2, False, 0))
        self.assertEqual(len(self.db.get_pending_transactions()), 2)
        self.assertEqual(len(self.db.get_import_history()), 1)
        self.assertEqual(len(self.db.get_statement_balances()), 1)
        before = self.snapshot()
        self.assertEqual(commit_statement(self.db, result, 'renamed.pdf', 'synthetic-fingerprint', summary, account())[:2], (0, True))
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(commit_statement(self.db, result, 'different.pdf', 'different-fingerprint', summary, account())[0], 0)
        self.assertEqual(self.snapshot(), before)

    def test_failed_balance_rolls_back_everything(self):
        from import_history import commit_statement
        result = frame()
        before = self.snapshot()
        with patch.object(self.db, 'save_statement_balance', side_effect=ValueError('Synthetic persistence failure')):
            with self.assertRaisesRegex(ValueError, 'Synthetic persistence failure'):
                commit_statement(self.db, result, 'synthetic.pdf', 'rollback-fingerprint', result.attrs['statement_balance'], account())
        self.assertEqual(self.snapshot(), before)

    def test_invalid_identity_rejected_before_storage(self):
        from import_history import commit_statement
        result = frame()
        before = self.snapshot()
        with self.assertRaises(parsing.RevolutBusinessParseError):
            commit_statement(self.db, result, 'synthetic.pdf', 'invalid-fingerprint', result.attrs['statement_balance'], account('USD'))
        self.assertEqual(self.snapshot(), before)

    def test_source_currency_drives_fx_without_storing_rate_type(self):
        result = localized.parse([document()])
        with patch.object(self.db, '_load_rate_lookup', return_value={}), patch.object(self.db, 'get_accounts', return_value=pd.DataFrame([account()])), patch.object(self.db, '_resolve_rate_for_values', return_value=('EUR/USD', Decimal('1.1'))) as resolve:
            converted = self.db.apply_account_and_rates(result, account())
        self.assertTrue(converted.currency.eq('EUR').all())
        self.assertTrue(converted.rate_type.eq('EUR/USD').all())
        self.assertTrue(all(call.args[1:3] == ('EUR/USD', 'EUR') for call in resolve.call_args_list))
        localized.validate_preview(converted, result.attrs['statement_balance'], account())

    def test_actual_original_pdf_ui_preview_is_readonly(self):
        import streamlit as st
        from streamlit.testing.v1 import AppTest
        source = Path(os.environ['ARETI_REVOLUT_SOURCE'])
        expected = json.loads(Path(os.environ['ARETI_REVOLUT_EXPECTED']).read_text())[source.name]
        with closing(self.db.get_connection()) as conn:
            conn.execute("INSERT INTO category_list(category,subcategory) VALUES('Synthetic','General')")
            conn.execute('INSERT INTO account_list(account_name,bank,account_number,currency,rate_type) VALUES(?,?,?,?,?)', ('Synthetic entity', 'Revolut', expected['account_number'], 'EUR', ''))
            conn.execute("INSERT INTO rates(rate_month,rate_type,rate_value) VALUES('2026-09-01','EUR/USD',1.1)")
            conn.commit()
        st.cache_data.clear()
        st.cache_resource.clear()
        before = self.snapshot()
        uploaded = BytesIO(source.read_bytes())
        uploaded.name = source.name
        app = AppTest.from_file('app.py', default_timeout=45)
        app.session_state['authenticated'] = True
        app.session_state['login_user'] = 'Synthetic reviewer'
        app.query_params['page'] = 'Import'
        try:
            with patch.object(st, 'file_uploader', return_value=uploaded):
                app.run()
            self.assertFalse(app.exception, [e.message for e in app.exception])
            self.assertFalse(app.error, [e.value for e in app.error])
            self.assertTrue(any('Prepared 8 transactions' in e.value for e in app.success))
            self.assertTrue(any(b.label == 'Import statement' for b in app.button))
            self.assertEqual(self.snapshot(), before)
        finally:
            st.cache_data.clear()
            st.cache_resource.clear()


if __name__ == '__main__':
    unittest.main(verbosity=2)
