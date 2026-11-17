"""Exact private Greek source, independent wallets and fail-closed dispatch."""
import copy
from datetime import date
from decimal import Decimal as D
import hashlib
from io import BytesIO
import os
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import pdfplumber
import parsing
import revolut_greek as greek


class Page:
    def __init__(self, page):
        self.text = page.extract_text()
        self.words = page.extract_words()
        self.width = page.width

    def extract_text(self):
        return self.text

    def extract_words(self):
        return self.words


class GreekWalletTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.raw = (Path(os.environ['ARETI_GREEK_SOURCE_ROOT']) / 'Natalia Revolut 1.10.2026-31.10.2026.pdf').read_bytes()
        assert hashlib.sha256(cls.raw).hexdigest() == 'ecb014a65d873f118c7888da2a2866e034ff9d4d1a09b3ceef8759f3d8bebf2c'
        with pdfplumber.open(BytesIO(cls.raw)) as pdf:
            cls.pages = [Page(p) for p in pdf.pages]

    def test_exact_wallets_decimal_reconciliation(self):
        wallets = greek.parse(self.pages)
        self.assertEqual(len(wallets), 2)
        for w, expected in zip(wallets, [('EUR',115,'6.90','33136.73','22650.96','10492.67'), ('USD',7,'1073.78','0','549.47','524.31')]):
            currency, count, opening, credits, debits, closing = expected
            self.assertEqual((w.currency,len(w.transactions)), (currency,count))
            self.assertEqual((w.opening,w.credits,w.debits,w.closing),tuple(map(D,(opening,credits,debits,closing))))
            self.assertEqual((w.iban,w.owner),('LT453250028012133298','NATALIA FISHMAN'))
            self.assertEqual((w.start,w.end),(date(2026,10,1),date(2026,10,31)))
            previous = w.opening
            for t in w.transactions:
                self.assertEqual(t.currency,currency)
                self.assertIsInstance(t.amount,D);self.assertIsInstance(t.balance,D)
                self.assertEqual(previous+t.amount,t.balance)
                self.assertTrue(w.start<=t.date<=w.end)
                previous=t.balance
            self.assertEqual(previous,w.closing)
            self.assertEqual(w.opening+w.credits-w.debits,w.closing)

    def test_pending_reverted_excluded_and_details_preserved(self):
        eur,usd=greek.parse(self.pages)
        self.assertEqual((eur.pending_count,eur.reverted_count,usd.pending_count,usd.reverted_count),(2,8,0,3))
        descriptions='\n'.join(t.description for w in (eur,usd) for t in w.transactions)
        for detail in ('Αναφορά: own funds','Αναφορά: Wedding gift','480,00₪','Προς: Bolt.euo2609301640, Tallinn'):
            self.assertIn(detail,descriptions)
        self.assertNotIn('Bolt.euo2610011215',descriptions)
        self.assertEqual(eur.transactions[0].amount,D('3000'))
        self.assertEqual(usd.transactions[0].amount,D('-151.20'))

    def test_source_identity_pages_period_and_summary_rejections(self):
        changes=[(0,'Revolut Bank UAB','Other Bank'),(0,'LT453250028012133298','LT003250028012133298'),
                 (8,'NATALIA FISHMAN','OTHER OWNER'),(1,'EUR Κίνηση','USD Κίνηση'),
                 (1,'Σελίδα 2 από 8','Σελίδα 3 από 8'),(0,'10.492,67€','10.492,68€'),
                 (8,'31 Οκτωβρίου 2026','30 Οκτωβρίου 2026'),(0,'5 Οκτ 2026 Προσθήκη','5 Νοε 2026 Προσθήκη'),
                 (0,'3.006,90€','3.006,91€'),(0,'3.000,00€','3.000,00$')]
        for index,old,new in changes:
            with self.subTest(change=(index,old)):
                pages=copy.deepcopy(self.pages);self.assertIn(old,pages[index].text)
                pages[index].text=pages[index].text.replace(old,new)
                with self.assertRaises(greek.GreekRevolutError):greek.parse(pages)
        with self.assertRaises(greek.GreekRevolutError):greek.parse(self.pages[:-1])

    def test_column_sign_disagreement_rejected(self):
        pages=copy.deepcopy(self.pages)
        for word in pages[0].words:
            if word['text']=='3.000,00€':word['x0']=335.06;word['x1']=375.0
        with self.assertRaisesRegex(greek.GreekRevolutError,'column/sign'):greek.parse(pages)

    def test_dispatch_preserves_independent_wallets(self):
        with patch.object(parsing,'_parse_generic_pdf_text',side_effect=AssertionError('fallback forbidden')):
            frame=parsing.parse_pdf(BytesIO(self.raw))
            self.assertEqual([len(w.transactions) for w in frame.attrs['revolut_sections']],[115,7])
            self.assertEqual(frame.attrs['revolut_document_hash'],hashlib.sha256(self.raw).hexdigest())
            with self.assertRaises(greek.GreekRevolutError):
                parsing.extract_statement_balance(BytesIO(self.raw),'original.pdf')

    def test_validation_error_never_falls_back(self):
        pages=copy.deepcopy(self.pages);pages[0].text=pages[0].text.replace('6,90€','7,90€')
        document=SimpleNamespace(pages=pages)
        with patch.object(parsing.pdfplumber,'open') as opened,patch.object(parsing,'_parse_generic_pdf_text',side_effect=AssertionError('fallback forbidden')):
            opened.return_value.__enter__.return_value=document
            with self.assertRaises(greek.GreekRevolutError):parsing.parse_pdf(BytesIO(b'local'))

    def test_unexpected_wallet_error_never_falls_back(self):
        document=SimpleNamespace(pages=self.pages)
        with patch.object(parsing.pdfplumber,'open') as opened,patch('revolut_sections.from_document',side_effect=RuntimeError('reader failed')),patch.object(parsing,'_parse_generic_pdf_text',side_effect=AssertionError('fallback forbidden')):
            opened.return_value.__enter__.return_value=document
            with self.assertRaisesRegex(greek.GreekRevolutError,'could not be validated') as caught:
                parsing.parse_pdf(BytesIO(b'local'))
            self.assertIsInstance(caught.exception.__cause__,RuntimeError)


if __name__=='__main__':unittest.main(verbosity=2)
