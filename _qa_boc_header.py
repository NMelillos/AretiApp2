"""Strict BOC header regression; original financial evidence stays outside Git."""
from collections import Counter
from decimal import Decimal
from hashlib import sha256
from io import BytesIO
import json, os
from pathlib import Path
import unittest
import pdfplumber
import boc_import, parsing
from boc_summary_balances import prepare
from _qa_boc_package import fixture


class HeaderTests(unittest.TestCase):
    def test_inline_and_interleaved_country_headers_have_identical_output(self):
        page,text=fixture([('credit','17.43','Synthetic credit')])
        expected=boc_import.parse_document([page],[text])
        changed=text.replace('Account Number ', 'Account Number\nCYPRUS ')
        self.assertEqual(boc_import.parse_document([page],[changed]),expected)

    def test_arbitrary_intervening_text_is_not_a_fallback(self):
        page,text=fixture()
        for replacement in ('Account Number UNKNOWN ', 'Account Number CYPRUS UNKNOWN ',
                            'Account Number CYPRUS CYPRUS ', 'Account Number CYPRUS '):
            changed=text.replace('Account Number ',replacement)
            if replacement=='Account Number CYPRUS ':
                changed=changed.replace('000000000000','')
            with self.subTest(replacement=replacement), self.assertRaises(boc_import.BOCParseError):
                boc_import.parse_document([page],[changed])

    def test_conflicting_headers_rejected(self):
        page,text=fixture()
        with self.assertRaisesRegex(boc_import.BOCParseError,'conflicting account'):
            boc_import.parse_document([page],[text+'\nAccount Number CYPRUS 999999999999'])

    def test_interleaved_header_keeps_iban_currency_and_arithmetic_guards(self):
        page,text=fixture([('credit','17.43','Synthetic credit')])
        text=text.replace('Account Number ', 'Account Number CYPRUS ')
        for changed in (text.replace('IBAN CY','IBAN XX'),text+'\nCurrency USD',
                        text.replace('01/02/2027 - 28/02/2027','28/02/2027 - 01/02/2027')):
            with self.subTest(changed=changed),self.assertRaises(boc_import.BOCParseError):
                boc_import.parse_document([page],[changed])
        words=page.extract_words()
        next(w for w in words if w['top']==310 and w['x0']==540)['text']='999.99'
        with self.assertRaisesRegex(boc_import.BOCParseError,'running balance'):
            boc_import.parse_document([page],[text])


@unittest.skipUnless(os.environ.get('BOC6128_PRIVATE'),'Original evidence must be supplied explicitly')
class OriginalTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root=Path(os.environ['BOC6128_PRIVATE'])
        cls.source=next(s for s in json.loads((cls.root/'boc-atomic-repair-private.json').read_text())['sources']
                        if '346128-' in s['file'])
        cls.content=(cls.root/'open-items-20261009'/cls.source['file']).read_bytes()
        assert sha256(cls.content).hexdigest()==cls.source['sha256']
        assert Path(boc_import.__file__).resolve().parent==Path.cwd().resolve()
        assert Path(parsing.__file__).resolve().parent==Path.cwd().resolve()

    def test_exact_original_transaction_set_and_balance_only_source_proof(self):
        frame,balance=prepare(self.content)
        actual=Counter((str(r['Date']),Decimal(str(r['Amount'])),r['Description']) for r in frame.to_dict('records'))
        expected=Counter((r['Date'],Decimal(r['Amount']),r['Description']) for r in self.source['rows'])
        self.assertEqual(actual,expected)
        self.assertEqual(len(frame),15)
        self.assertEqual(balance['account_number'][-4:],'6128')
        self.assertEqual(balance['currency'],'EUR')
        self.assertEqual((balance['period_start'],balance['period_end']),('2026-09-01','2026-09-30'))
        expected_facts=json.loads((self.root/'open-items-20261009/source-expectations-private.json').read_text())[self.source['file']]
        for key in ('opening_balance','money_in','money_out','closing_balance'):
            self.assertEqual(balance[key],Decimal(expected_facts[key]))
        self.assertTrue(balance['structural_validation'])
        self.assertEqual(balance['opening_balance']+balance['money_in']-balance['money_out'],balance['closing_balance'])

    def test_original_continuation_identity_and_page_guards(self):
        with pdfplumber.open(BytesIO(self.content)) as document:
            pages=list(document.pages);texts=[p.extract_text() or '' for p in pages]
            self.assertIn('Account Number\nCYPRUS ',texts[1])
            # Explicit continuation-account mismatch, duplicate section and changed page count.
            account=boc_import.metadata(texts[0])['account_number']
            cases=[(pages[:1],texts[:1]),(pages[::-1],texts[::-1]),(pages,[texts[0],texts[1].replace(account,'999999999999')]),
                            (pages*2,texts*2),(pages,[texts[0],texts[1].replace('Page 2 / 2','Page 2 / 3')])]
            for ps,ts in cases:
                with self.subTest(pages=len(ps)),self.assertRaises(boc_import.BOCParseError):
                    boc_import.parse_document(ps,ts)

    def test_original_preview_mutations_rejected(self):
        frame,balance=prepare(self.content)
        account=dict(account_number=balance['account_number'],currency=balance['currency'])
        frame=frame.assign(account_number=account['account_number'],currency=account['currency'])
        boc_import.validate_preview(frame,balance,account)
        for field,value in (('Amount',Decimal('0')),('Date','2026-08-31'),('account_number','999999999999'),('currency','USD')):
            changed=frame.copy();changed.loc[0,field]=value
            with self.subTest(field=field),self.assertRaises(boc_import.BOCParseError):
                boc_import.validate_preview(changed,balance,account)

    def test_all_known_originals_preserve_outputs_or_existing_rejections(self):
        baseline=json.loads((self.root/'boc6128-validation/probe-areti-performance-measured.json').read_text())
        previous=json.loads((self.root/'open-items-20261009/baseline-source-probe-private.json').read_text())
        for source in baseline:
            path=Path(source['file'])
            with self.subTest(source=path.name):
                frame=parsing.parse_pdf(BytesIO(path.read_bytes()))
                actual=json.loads(json.dumps(dict(rows=frame.to_dict('records'),attrs=frame.attrs),default=str))
                expected=source if 'error' not in source else next(s for s in previous if Path(s['file']).name==path.name)
                self.assertEqual(actual['rows'],expected['rows'])
                self.assertEqual(actual['attrs'],expected['attrs'])


if __name__=='__main__':
    unittest.main(verbosity=2)
