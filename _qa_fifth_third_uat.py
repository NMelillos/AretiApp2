"""Exact private source expectations; disposable DB and blocked external access."""
import json, os, unittest
from pathlib import Path
from io import BytesIO
from decimal import Decimal
from hashlib import sha256
from contextlib import closing
from unittest.mock import patch
import pdfplumber, parsing, db
from import_history import commit_statement
from latest_import_balances import snapshot
from _qa_post_import_message import CompletionTests
ROOT = Path(os.environ['ARETI_ISOLATION_ROOT'])
FACTS = {name: fact for name, fact in json.loads((ROOT / 'source-expectations-private.json').read_text(encoding='utf-8')).items() if name in ('Woking Way.pdf', 'Woking Way 2.pdf')}

class SourceTests(unittest.TestCase):

    def test_exact_source_fields_counts_signs_reconciliation(self):
        self.assertEqual(Path(parsing.__file__).resolve().parent, Path.cwd().resolve())
        for name, expected in FACTS.items():
            with self.subTest(source=name):
                content = (ROOT / name).read_bytes()
                frame = parsing.parse_pdf(BytesIO(content))
                meta = frame.attrs['statement_balance']
                self.assertEqual(len(frame), len(expected['amounts']) if 'amounts' in expected else expected['count'])
                for k in ('bank', 'account_number', 'period_start', 'period_end', 'currency'):
                    self.assertEqual(meta[k], expected[k])
                for k in ('opening_balance', 'money_in', 'money_out', 'closing_balance'):
                    self.assertEqual(meta[k], Decimal(expected[k]))
                if 'amounts' in expected:
                    self.assertEqual(frame.Amount.tolist(), list(map(Decimal, expected['amounts'])))
                self.assertEqual(sum((x for x in frame.Amount if x > 0), Decimal(0)), meta['money_in'])
                self.assertEqual(-sum((x for x in frame.Amount if x < 0), Decimal(0)), meta['money_out'])
                self.assertEqual(meta['opening_balance'] + meta['money_in'] - meta['money_out'], meta['closing_balance'])
                self.assertTrue(frame.Date.between(meta['period_start'], meta['period_end']).all())
                self.assertEqual(parsing.extract_statement_balance(BytesIO(content), name), meta if not name.startswith('eStatements') else dict(meta))

    def test_fifth_source_structure_and_modified_rows_fail_closed(self):
        from fifth_third_import import parse, FifthThirdParseError, validate_preview
        for name in ('Woking Way.pdf', 'Woking Way 2.pdf'):
            with pdfplumber.open(ROOT / name) as doc:
                pages = [p.extract_text() or '' for p in doc.pages]
            original = parse(pages)
            for bad in (pages[:-1], list(reversed(pages)), [pages[0].replace('Ending Balance $', 'Ending Balance $9'), *pages[1:]], [pages[0].replace('09/22 7.29 INTEREST', ''), *pages[1:]] if name == 'Woking Way.pdf' else [pages[0].replace('09/14 17.76 SERVICE CHARGE', ''), *pages[1:]]):
                with self.assertRaises(FifthThirdParseError):
                    parse(bad)
            balance = original.attrs['statement_balance']
            account = dict(bank='Fifth Third Bank', account_number=balance['account_number'], currency='USD')
            frame = original.copy()
            frame['account_number'] = account['account_number']
            frame['currency'] = 'USD'
            validate_preview(frame, balance, account)
            frame.loc[0, 'Amount'] += Decimal(1)
            with self.assertRaises(FifthThirdParseError):
                validate_preview(frame, balance, account)

    def test_new_source_setup_identity_and_currency_fail_closed(self):
        from fifth_third_import import validate_preview as fifth, FifthThirdParseError
        for name, expected in FACTS.items():
            if name.startswith('eStatements'):
                continue
            frame = parsing.parse_pdf(BytesIO((ROOT / name).read_bytes()))
            balance = frame.attrs['statement_balance']
            account = dict(bank=expected['bank'], account_number=expected['account_number'], currency=expected['currency'])
            frame['account_number'] = account['account_number']
            frame['currency'] = account['currency']
            validator = fifth
            error = FifthThirdParseError
            validator(frame, balance, account)
            for override in ({'account_number': '0000'}, {'currency': 'GBP'}, {'bank': 'Unrelated bank'}):
                with self.assertRaises(error):
                    validator(frame, balance, dict(account, **override))
            if name.startswith('Woking'):
                validator(frame, balance, dict(account, bank='Comerica'))

class WorkflowTests(CompletionTests):

    def test_exact_sources_pending_history_balances_and_duplicates(self):
        with closing(db.get_connection()) as conn:
            conn.execute("INSERT INTO rates(rate_month,rate_type,rate_value) VALUES('2026-09-01','EUR/USD',1.17)")
            conn.commit()
        for name, expected in FACTS.items():
            with self.subTest(source=name):
                content = (ROOT / name).read_bytes()
                frame = parsing.parse_pdf(BytesIO(content))
                meta = frame.attrs['statement_balance']
                digest = sha256(content).hexdigest()
                account = dict(account_name='Synthetic ' + digest[:6], bank=meta['bank'], account_number=meta['account_number'], currency=meta['currency'], rate_type=meta['currency'] + '/USD')
                with closing(db.get_connection()) as conn:
                    conn.execute('INSERT INTO account_list(account_name,bank,account_number,currency,rate_type) VALUES(?,?,?,?,?)', tuple(account.values()))
                    conn.commit()
                frame = db.apply_account_and_rates(frame, account)
                before = len(db.get_pending_transactions())
                self.assertEqual(commit_statement(db, frame, name, digest, meta, account), (len(frame), False, 0))
                self.assertEqual(len(db.get_pending_transactions()), before + len(frame))
                history = db.get_import_history()
                self.assertEqual(int(history.loc[history.statement_hash == digest].iloc[0].transaction_count), len(frame))
                state = self.snapshot()
                self.assertEqual(commit_statement(db, frame, name, digest, meta, account), (0, True, 0))
                self.assertEqual(self.snapshot(), state)
                changed = commit_statement(db, frame, name, digest + 'changed', meta, account)
                self.assertEqual(changed[0], 0)
                self.assertEqual(len(db.get_pending_transactions()), before + len(frame))

    def test_new_formats_actual_upload_ui(self):
        with closing(db.get_connection()) as conn:
            conn.execute("INSERT INTO rates(rate_month,rate_type,rate_value) VALUES('2026-09-01','EUR/USD',1.17)")
            conn.commit()
        for name, expected in FACTS.items():
            if name.startswith('eStatements'):
                continue
            with self.subTest(source=name):
                self.file = BytesIO((ROOT / name).read_bytes())
                self.file.name = name
                account = dict(account_name='Source ' + expected['account_number'][-4:], bank='Comerica' if name.startswith('Woking') else expected['bank'], account_number=expected['account_number'], currency=expected['currency'], rate_type=expected['currency'] + '/USD')
                with closing(db.get_connection()) as conn:
                    conn.execute('INSERT INTO account_list(account_name,bank,account_number,currency,rate_type) VALUES(?,?,?,?,?)', tuple(account.values()))
                    conn.commit()
                import streamlit as st
                st.cache_data.clear()
                before = len(db.get_pending_transactions())
                app = self.run_app(self.app())
                selected = next((s for s in app.selectbox if s.label == 'Account'))
                self.assertIn(account['account_name'], selected.value)
                self.import_file(app)
                self.complete(app, len(expected['amounts']))
                self.assertEqual(len(db.get_pending_transactions()), before + len(expected['amounts']))
                digest = sha256(self.file.getvalue()).hexdigest()
                history = db.get_import_history()
                self.assertEqual(history.loc[history.statement_hash == digest].iloc[0]['bank'], account['bank'])
                state = self.snapshot()
                self.run_app(app)
                self.complete(app, len(expected['amounts']))
                self.assertEqual(self.snapshot(), state)
                self.assertFalse(any((b.label == 'Import statement' and (not b.disabled) for b in app.button)))

def load_tests(loader,tests,pattern):
 return unittest.TestSuite([loader.loadTestsFromTestCase(SourceTests),WorkflowTests("test_exact_sources_pending_history_balances_and_duplicates"),WorkflowTests("test_new_formats_actual_upload_ui")])
