"""Applied FX presentation only; disposable database and unchanged calculations."""
from decimal import Decimal, localcontext, ROUND_DOWN
from contextlib import closing
from io import BytesIO
import unittest
from unittest.mock import patch

import db
import latest_import_balances as report
from _qa_post_import_message import CompletionTests
from _qa_balance_sapphire_20261111 import fixture


class FXDisplayTests(CompletionTests):
    def test_artifacts_precision_fixed_notation_and_immutable_input(self):
        cases = {
            '1.1200000000000001': '1.12',
            '0.9235000000000001': '0.9235',
            '1.0': '1',
            '1.23456789012345': '1.23456789012345',
            '1E-20': '0.00000000000000000001',
            '1E+20': '100000000000000000000',
        }
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                value = Decimal(raw)
                before = value.as_tuple()
                with localcontext() as context:
                    context.prec = 4
                    context.rounding = ROUND_DOWN
                    actual = report._format_applied_fx(value)
                self.assertEqual(actual, expected)
                self.assertNotIn('e', actual.lower())
                self.assertEqual(value.as_tuple(), before)

    def test_snapshot_exports_calculation_and_all_stored_rows_preserved(self):
        fixture()
        with closing(db.get_connection()) as connection:
            before_db = '\n'.join(connection.iterdump())
        rate = Decimal('1.1200000000000001')
        with patch.object(db, '_resolve_rate_for_values', return_value=('EUR/USD', rate)):
            with patch.object(report, '_format_applied_fx', side_effect=str):
                before = report.snapshot(db)
            with patch.object(db, '_usd_from_amount', wraps=db._usd_from_amount) as convert:
                after = report.snapshot(db)
        self.assertTrue(convert.call_args_list)
        for call in convert.call_args_list:
            self.assertEqual(call.args[1].as_tuple(), rate.as_tuple())
        changed = 0
        for old, new in zip(before[0], after[0]):
            for key in old:
                if key == 'Applied FX' and old[key].startswith(str(rate)):
                    self.assertEqual(new[key], '1.12 (Setup rate at statement month)')
                    changed += 1
                else:
                    self.assertEqual(old[key], new[key], key)
        self.assertGreater(changed, 0)
        self.assertEqual(len(before[0]), len(after[0]))
        self.assertEqual(before[1:], after[1:])
        from balance_report_sections import totals, grand_total
        self.assertEqual(totals(before[0]), totals(after[0]))
        self.assertEqual(grand_total(before[0]), grand_total(after[0]))
        html = report.print_document(*after)
        for document in (html, report.screen_document(html)):
            self.assertIn('1.12 (Setup rate at statement month)', document)
            self.assertNotIn(str(rate), document)
        from openpyxl import load_workbook
        book = load_workbook(BytesIO(report.workbook_bytes(*after)))
        cells = [cell for sheet in book for row in sheet.iter_rows(values_only=True) for cell in row]
        self.assertIn('1.12 (Setup rate at statement month)', cells)
        self.assertFalse(any(str(rate) in str(cell) for cell in cells))
        with closing(db.get_connection()) as connection:
            self.assertEqual('\n'.join(connection.iterdump()), before_db)


if __name__ == '__main__':
    suite = unittest.TestSuite(FXDisplayTests(name) for name in FXDisplayTests.__dict__ if name.startswith('test_'))
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    raise SystemExit(not result.wasSuccessful())
