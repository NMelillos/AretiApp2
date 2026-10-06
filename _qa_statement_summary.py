"""Source-bound summary, real zero-transaction PDF and no-database route tests."""
import ast
from io import BytesIO
import json
import os
from pathlib import Path
import unittest
from unittest.mock import patch

from statement_summary import extract_pdf, extract_pages


def zero_pdf():
    """Minimal valid synthetic text PDF, no transaction rows, no persisted records."""
    lines = ['SYNTHETIC TEST STATEMENT - BankOfCyprusPublicCompanyLtd',
             'Account Number 000000001234', 'Currency USD',
             'Statement Period: 01/08/2026 - 31/08/2026',
             'Balance brought forward 123.4500',
             'No transactions this period',
             'Total / Balance Carried Forward 0.00 0.00 123.4500']
    content = 'BT /F1 11 Tf 40 750 Td ' + ' '.join(('0 -22 Td ' if i else '') + '(' + line + ') Tj' for i, line in enumerate(lines)) + ' ET'
    stream = content.encode('ascii')
    objects = [b'<< /Type /Catalog /Pages 2 0 R >>',
               b'<< /Type /Pages /Kids [3 0 R] /Count 1 >>',
               b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>',
               b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>',
               b'<< /Length ' + str(len(stream)).encode() + b' >>\nstream\n' + stream + b'\nendstream']
    pdf = b'%PDF-1.4\n'; offsets = [0]
    for i, obj in enumerate(objects, 1):
        offsets.append(len(pdf)); pdf += str(i).encode() + b' 0 obj\n' + obj + b'\nendobj\n'
    xref = len(pdf)
    pdf += b'xref\n0 6\n0000000000 65535 f \n' + b''.join(f'{offset:010} 00000 n \n'.encode() for offset in offsets[1:])
    return pdf + f'trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n'.encode()


BASE = 'BankOfCyprusPublicCompanyLtd\nAccount Number 000000001234\nCurrency USD\nStatement Period: 01/08/2026 - 31/08/2026\nBalance brought forward 123.4500\nTotal / Balance Carried Forward 0.00 0.00 123.4500'


class SummaryTests(unittest.TestCase):
    def test_supplied_original_pdf(self):
        expected = json.loads(Path(os.environ['ARETI_SUMMARY_EXPECTED']).read_text())
        result = extract_pdf(Path(os.environ['ARETI_SUMMARY_SOURCE']).read_bytes())
        for key, value in expected['fields'].items():
            field = getattr(result, key)
            self.assertEqual(field.value, value['value'], key)
            self.assertEqual(field.evidence[0].page, value['page'], key)
            self.assertIn(value['anchor'], field.evidence[0].anchor)
        self.assertEqual(result.currency.value, expected['currency'])
        self.assertTrue(result.account.endswith(expected['account_last_four']))
        self.assertEqual(result.opening_balance.currency, expected['currency'])
        self.assertEqual(result.closing_balance.currency, expected['currency'])

    def test_real_zero_transaction_pdf(self):
        result = extract_pdf(zero_pdf())
        self.assertEqual(result.period_start.value, '2026-08-01')
        self.assertEqual(result.period_end.value, '2026-08-31')
        self.assertEqual(result.opening_balance.value, '123.4500')
        self.assertEqual(result.closing_balance.value, '123.4500')
        self.assertEqual(result.closing_balance.currency, 'USD')

    def test_missing_field_is_independent(self):
        result = extract_pages([BASE.replace('Balance brought forward 123.4500', '')])
        self.assertFalse(result.opening_balance.verified)
        self.assertTrue(result.closing_balance.verified)
        self.assertTrue(result.period_start.verified)

    def test_conflicting_candidates(self):
        result = extract_pages([BASE, 'Statement Period: 02/08/2026 - 31/08/2026\nBalance brought forward 999.00'])
        self.assertFalse(result.period_start.verified)
        self.assertTrue(result.period_end.verified)
        self.assertFalse(result.opening_balance.verified)
        self.assertTrue(result.closing_balance.verified)

    def test_no_header_substitution(self):
        result = extract_pages([BASE.replace('Total / Balance Carried Forward 0.00 0.00 123.4500', 'Balance 123.4500')])
        self.assertFalse(result.closing_balance.verified)

    def test_header_conflict(self):
        self.assertFalse(extract_pages([BASE + '\nBalance 999.00']).closing_balance.verified)

    def test_sign_and_long_precision(self):
        for raw, expected in [('(123.4500)', '-123.4500'), ('123.4500 DR', '-123.4500'),
                              ('123.4500 CR', '123.4500'), ('-123.4500', '-123.4500'),
                              ('123456789012345678901234567890.123456', '123456789012345678901234567890.123456')]:
            with self.subTest(raw=raw):
                text = BASE.replace('Balance brought forward 123.4500', 'Balance brought forward ' + raw)
                self.assertEqual(extract_pages([text]).opening_balance.value, expected)

    def test_conflicting_sign_rejected(self):
        self.assertFalse(extract_pages([BASE.replace('Balance brought forward 123.4500', 'Balance brought forward -123.4500 CR')]).opening_balance.verified)

    def test_missing_ambiguous_currency(self):
        for text in [BASE.replace('Currency USD', ''), BASE + '\nCurrency EUR']:
            self.assertFalse(extract_pages([text]).opening_balance.verified)
            self.assertFalse(extract_pages([text]).closing_balance.verified)

    def test_multiple_accounts_rejected(self):
        result = extract_pages([BASE + '\nAccount Number 000000005678'])
        self.assertTrue(all(not getattr(result, key).verified for key in ('period_start', 'period_end', 'opening_balance', 'closing_balance')))

    def test_invalid_date_and_reversed_period(self):
        result = extract_pages([BASE.replace('01/08/2026', '32/08/2026')])
        self.assertFalse(result.period_start.verified)
        self.assertTrue(result.period_end.verified)
        result = extract_pages([BASE.replace('01/08/2026 - 31/08/2026', '31/08/2026 - 01/08/2026')])
        self.assertFalse(result.period_start.verified)
        self.assertFalse(result.period_end.verified)

    def test_unsupported_scanned_and_bad_pdf(self):
        result = extract_pages(['Bank account balance 100.00'])
        self.assertFalse(result.opening_balance.verified)
        self.assertFalse(extract_pages(['']).period_start.verified)
        with self.assertRaises(ValueError):
            extract_pdf(b'not a PDF')
        with self.assertRaises(ValueError):
            extract_pdf(b'%PDF-' + b'0' * (20 * 1024 * 1024))

    def test_actual_authenticated_app_route_without_database(self):
        import sys
        from streamlit.testing.v1 import AppTest
        import streamlit as st
        at = AppTest.from_file('app.py', default_timeout=30)
        at.query_params['page'] = 'Statement Summary'
        at.session_state['authenticated'] = True
        at.session_state['login_user'] = 'Synthetic local reviewer'
        with patch.object(st, 'file_uploader', return_value=BytesIO(zero_pdf())):
            at.run()
            self.assertFalse(at.exception)
            at.button(key='extract_summary').click().run()
            self.assertFalse(at.exception)
        rows = at.table[0].value
        self.assertEqual(rows['Value'].tolist(), ['2026-08-01', '2026-08-31', '123.4500 USD', '123.4500 USD'])
        self.assertNotIn('db', sys.modules)
        self.assertNotIn('parsing', sys.modules)
        self.assertIn('Statement Summary', [title.value for title in at.title])
        tree = ast.parse(Path('app.py').read_text())
        self.assertLess(Path('app.py').read_text().index('if _REQUESTED_PAGE == "Statement Summary"'), Path('app.py').read_text().index('from db import'))


if __name__ == '__main__':
    unittest.main(verbosity=2)
