"""THIRD-only display mode; disposable database and unchanged default output."""
import ast
import copy
from datetime import datetime
from decimal import Decimal
from pathlib import Path
import re
import subprocess
import types
import unittest
from unittest.mock import patch

import db
import latest_balances_compact as compact
import latest_import_balances as normal
from balance_report_sections import grand_total, totals
from _qa_post_import_message import CompletionTests
from streamlit.testing.v1 import AppTest

BASE = 'f5e8429724472713c726ad0ebf23ba273ea46162'


def baseline(name):
    return subprocess.check_output(['git', 'show', BASE + ':' + name]).decode('utf-8')


def rows():
    result = []
    for bank, value in [('Test bank', '100.25'), ('AMEX', '-25.00')]:
        row = dict.fromkeys(normal.COLUMNS, '')
        row.update({'Bank': bank, 'Account name': 'Disposable', 'Account number': bank,
                    'Currency': 'USD', 'Status': 'IMPORTED',
                    'Verification': 'SOURCE RECONCILIATION NOT VERIFIED',
                    'Closing balance': Decimal(value),
                    'Closing balance converted to USD': Decimal(value)})
        result.append(row)
    result.append(dict(result[0], Status='NO IMPORT', **{'Closing balance': None,
                       'Closing balance converted to USD': ''}))
    result.append(dict(result[0], **{'Closing balance converted to USD': 'NOT AVAILABLE'}))
    return result


class DisplayTests(unittest.TestCase):
    def test_default_identical_and_rows_and_section_totals_preserved(self):
        old = types.ModuleType('baseline_compact')
        exec(baseline('latest_balances_compact.py'), old.__dict__)
        data = rows()
        before = copy.deepcopy(data)
        when = datetime.fromisoformat('2026-11-18T12:00:00+02:00')
        strict = old.compact_html(data, when)
        self.assertEqual(strict, compact.compact_html(data, when))
        shown = compact.compact_html(data, when, third_available_total=True)
        self.assertEqual(re.findall(r'<tbody>.*?</tbody>', strict, re.S),
                         re.findall(r'<tbody>.*?</tbody>', shown, re.S))
        self.assertEqual(re.findall(r'<tfoot><tr>.*?</tr>', strict, re.S),
                         re.findall(r'<tfoot><tr>.*?</tr>', shown, re.S))
        self.assertEqual(data, before)
        self.assertEqual(totals(data), {'Deposits': Decimal('100.25'), 'Debts': Decimal('-25')})
        self.assertIsNone(grand_total(data))

    def test_same_normal_report_basis_and_exclusions(self):
        data = rows()
        for html in (compact.compact_html(data, third_available_total=True),
                     normal.screen_document(normal.print_document(data, Decimal('100.25'), []), data)):
            self.assertIn('Grand Total (available balances only): $75.25', html)
            self.assertIn('Excludes 1 accounts with no current imported balance.', html)
            self.assertIn('Also excludes 1 accounts without eligible USD balances.', html)
        for data in ([], rows()[2:]):
            html = compact.compact_html(data, third_available_total=True)
            self.assertIn('Grand Total (available balances only): NOT AVAILABLE', html)
        data = rows()[:2]
        data[0]['Closing balance converted to USD'] = Decimal('25')
        self.assertIn('Grand Total (available balances only): $0.00',
                      compact.compact_html(data, third_available_total=True))

    def test_only_third_call_changes_and_financial_modules_unchanged(self):
        old = ast.parse(baseline('app.py'))
        new = ast.parse(Path('app.py').read_text(encoding='utf-8'))
        fn = next(n for n in new.body if isinstance(n, ast.FunctionDef) and n.name == 'render_third_link_report')
        call = next(n for n in ast.walk(fn) if isinstance(n, ast.Call)
                    and isinstance(n.func, ast.Name) and n.func.id == 'render_compact_balances')
        self.assertEqual([(k.arg, k.value.value) for k in call.keywords], [('third_available_total', True)])
        call.keywords = []
        self.assertEqual(ast.dump(old), ast.dump(new))
        for name in ('latest_import_balances.py', 'balance_report_sections.py', 'financial_decimal.py',
                     'db.py', 'auth.py', 'reporting.py'):
            self.assertEqual(baseline(name).replace('\r\n', '\n'),
                             Path(name).read_text(encoding='utf-8'))


class NavigationTests(CompletionTests):
    def test_actual_third_read_only_open_and_return(self):
        before = self.snapshot()
        locked = AppTest.from_file('app.py', default_timeout=60)
        locked.query_params['page'] = 'TB & NF Family Office Report'
        locked.run()
        self.assertFalse(locked.exception)
        self.assertFalse(any(b.key == 'third_latest_import_balances' for b in locked.button))
        app = AppTest.from_file('app.py', default_timeout=60)
        app.query_params['page'] = 'TB & NF Family Office Report'
        app.session_state['third_report_authenticated'] = True
        app.session_state['third_report_user'] = 'Disposable reviewer'
        app.run()
        self.assertFalse(app.exception)
        original = [m.value for m in app.markdown]
        with patch.object(db, 'backfill_missing_usd_amounts', side_effect=AssertionError('No backfill')):
            app.button(key='third_latest_import_balances').click().run()
        self.assertFalse(app.exception)
        self.assertFalse(app.error)
        self.assertTrue(any('Grand Total (available balances only)' in m.value for m in app.markdown))
        self.assertTrue(any('Excludes ' in m.value for m in app.markdown))
        self.assertEqual(before, self.snapshot())
        app.button(key='third_latest_balances_return').click().run()
        self.assertFalse(app.exception)
        self.assertEqual(original, [m.value for m in app.markdown])
        self.assertEqual(before, self.snapshot())


if __name__ == '__main__':
    suite = unittest.TestSuite([unittest.defaultTestLoader.loadTestsFromTestCase(DisplayTests),
                               NavigationTests('test_actual_third_read_only_open_and_return')])
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    raise SystemExit(not result.wasSuccessful())
