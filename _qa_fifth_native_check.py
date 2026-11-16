"""Downloaded originals remain on the native path; private PDFs are not committed."""
import hashlib
import os
from decimal import Decimal as D
from io import BytesIO
from pathlib import Path
import unittest
from unittest.mock import patch

import pdfplumber
import parsing
from fifth_third_import import parse, recognized, FifthThirdParseError

FACTS = {
    '4128': ('2c9b4de88aefb7b007408faa6b4ca46acc2cf9e6d1c886d222ba9f40c999fae9', '1894484128', 9, '10490.66', '9960.00', '5000.00', '7038.42', '8412.24'),
    '3143': ('615965bc93ceff9c7be414624e1d23dd87586fbe165857572a495fef97e35a22', '1894173143', 1, '868.36', '0.00', '0.00', '16.97', '851.39'),
    '5055': ('d3988e67ca2ac7d19b4de91733fcbac4cebd029aa23430b3c2109539c63fffec', '1892775055', 2, '40575.60', '0.00', '31000.00', '11.00', '9564.60'),
}

class NativeCheckTests(unittest.TestCase):
    def source(self, key):
        raw = (Path(os.environ['ARETI_FIFTH_NATIVE_ROOT']) / ('#' + key + '.pdf')).read_bytes()
        self.assertEqual(hashlib.sha256(raw).hexdigest(), FACTS[key][0])
        with pdfplumber.open(BytesIO(raw)) as doc:
            pages = [page.extract_text() or '' for page in doc.pages]
        self.assertTrue(recognized('\n'.join(pages)))
        return raw, pages

    def verify(self, key):
        raw, pages = self.source(key)
        _, account, count, opening, credits, checks, debits, closing = FACTS[key]
        native = parse(pages)
        with patch('fifth_third_scanned.subprocess.run', side_effect=AssertionError('OCR must not run')) as ocr:
            frame = parsing.parse_pdf(BytesIO(raw))
            balance = parsing.extract_statement_balance(BytesIO(raw), '#' + key + '.pdf')
            ocr.assert_not_called()
        self.assertEqual(frame.to_dict('records'), native.to_dict('records'))
        self.assertEqual(balance, native.attrs['statement_balance'])
        self.assertEqual(len(frame), count)
        for field, value in {'account_number': account, 'bank': 'Fifth Third Bank', 'currency': 'USD',
                             'period_start': '2026-10-01', 'period_end': '2026-10-31'}.items():
            self.assertEqual(balance[field], value)
        for field, value in {'opening_balance': D(opening), 'money_in': D(credits),
                             'money_out': D(checks) + D(debits), 'closing_balance': D(closing)}.items():
            self.assertEqual(balance[field], value)
        check_rows = [r for r in frame.to_dict('records') if r['Description'].startswith('Check ')]
        self.assertEqual(-sum((r['Amount'] for r in check_rows), D(0)), D(checks))
        self.assertEqual(sum((a for a in frame.Amount if a > 0), D(0)), D(credits))
        self.assertEqual(-sum((a for a in frame.Amount if a < 0), D(0)), D(checks) + D(debits))
        self.assertEqual(D(opening) + sum(frame.Amount, D(0)), D(closing))
        if key == '4128':
            self.assertIn(D('-149.31'), frame.Amount.tolist())
            self.assertEqual(sum(a < 0 for a in frame.Amount), 8)

    def test_original_4128_native(self): self.verify('4128')
    def test_original_3143_native(self): self.verify('3143')
    def test_original_5055_native(self): self.verify('5055')

    def test_singular_and_plural_headings_are_equivalent(self):
        _, pages = self.source('4128')
        original = parse(pages)
        plural = [pages[0].replace('Check 1 check totaling', 'Checks 1 checks totaling'), *pages[1:]]
        self.assertNotEqual(plural, pages)
        result = parse(plural)
        self.assertEqual(result.to_dict('records'), original.to_dict('records'))
        self.assertEqual(result.attrs, original.attrs)

    def test_counts_totals_dates_account_and_pages_still_fail_closed(self):
        _, pages = self.source('4128')
        mutations = [('Check 1 check totaling', 'Check 2 checks totaling'),
                     ('totaling $5,000.00', 'totaling $5,001.00'),
                     ('1004 i 10/01 5,000.00', '1004 i 10/01 4,999.00'),
                     ('1004 i 10/01', '1004 i 11/01'),
                     ('Account Summary - 1894484128', 'Account Summary - 1894484129'),
                     ('Ending Balance $8,412.24', 'Ending Balance $8,412.25')]
        for old, new in mutations:
            with self.subTest(change=old):
                self.assertIn(old, pages[0])
                with self.assertRaises(FifthThirdParseError):
                    parse([pages[0].replace(old, new), *pages[1:]])
        with self.assertRaises(FifthThirdParseError): parse(pages[:-1])

if __name__ == '__main__': unittest.main()
