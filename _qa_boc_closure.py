"""Source-bound final-summary layout checks; originals never enter Git."""
from collections import Counter
from decimal import Decimal
from hashlib import sha256
from io import BytesIO
import json,os
from pathlib import Path
import unittest
from types import SimpleNamespace
import pdfplumber
import boc_import,parsing
from boc_summary_balances import prepare

@unittest.skipUnless(os.environ.get('BOC6128_PRIVATE'),'Private original evidence required')
class ClosureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root=Path(os.environ['BOC6128_PRIVATE'])
        cls.sources=json.loads((cls.root/'boc-atomic-repair-private.json').read_text())['sources']
        assert Path(boc_import.__file__).resolve().parent==Path.cwd().resolve()

    def test_three_exact_sources_all_143_rows_and_financial_facts(self):
        facts=json.loads((self.root/'open-items-20261009/source-expectations-private.json').read_text())
        total=0
        for source in self.sources:
            with self.subTest(source=source['file']):
                content=(self.root/'open-items-20261009'/source['file']).read_bytes()
                self.assertEqual(sha256(content).hexdigest(),source['sha256'])
                frame,balance=prepare(content)
                actual=Counter((str(r['Date']),Decimal(str(r['Amount'])),r['Description']) for r in frame.to_dict('records'))
                expected=Counter((r['Date'],Decimal(r['Amount']),r['Description']) for r in source['rows'])
                self.assertEqual(actual,expected)
                f=facts[source['file']]
                self.assertEqual(len(frame),f['count']);total+=len(frame)
                for key in ('account_number','currency','period_start','period_end'):
                    self.assertEqual(balance[key],f[key])
                for key in ('opening_balance','money_in','money_out','closing_balance'):
                    self.assertEqual(balance[key],Decimal(f[key]))
                self.assertTrue(balance['structural_validation'])
                self.assertEqual(balance['opening_balance']+balance['money_in']-balance['money_out'],balance['closing_balance'])
        self.assertEqual(total,143)

    def test_summary_layout_still_rejects_hidden_activity_and_missing_proof(self):
        source=next(s for s in self.sources if '3804-' in s['file'])
        content=(self.root/'open-items-20261009'/source['file']).read_bytes()
        with pdfplumber.open(BytesIO(content)) as document:
            pages=list(document.pages);texts=[p.extract_text() or '' for p in pages]
            for mode in ('truncated','reordered','changed_account','date_before_total','amount_before_total','missing_total','wrong_total','middle_caption','missing_continuation'):
                ps=list(pages);ts=list(texts)
                if mode=='truncated':ps=ps[:-1];ts=ts[:-1]
                elif mode=='reordered':ps[-2:]=ps[-2:][::-1];ts[-2:]=ts[-2:][::-1]
                elif mode=='changed_account':ts[-1]=ts[-1].replace('357036873804','999999999999')
                elif mode=='date_before_total':ts[-1]='01/09/2026\n'+ts[-1]
                elif mode=='middle_caption':ts[1]=ts[1].replace('Continue on next Page','')
                elif mode=='missing_continuation':ts[-2]=ts[-2].replace('From Previous Page','')
                if mode in ('middle_caption','missing_continuation'):
                    index=1 if mode=='middle_caption' else -2
                    words=[dict(w) for w in ps[index].extract_words()]
                    marker='Continue' if mode=='middle_caption' else 'From'
                    ys={w['top'] for w in words if w['text']==marker}
                    self.assertTrue(ys, 'Fixture must locate the original caption')
                    words=[w for w in words if not any(abs(w['top']-y)<2 for y in ys)]
                    ps[index]=SimpleNamespace(extract_words=lambda:words)
                elif mode in ('amount_before_total','missing_total','wrong_total'):
                    words=[dict(w) for w in ps[-1].extract_words()]
                    if mode=='amount_before_total':words.append(dict(text='1.00',x0=400,x1=425,top=10,upright=True))
                    elif mode=='missing_total':words=[w for w in words if w['text']!='Total']
                    elif mode=='wrong_total':
                        next(w for w in words if w['text']=='24,356.62')['text']='24,356.63'
                    ps[-1]=SimpleNamespace(extract_words=lambda:words)
                with self.subTest(mode=mode),self.assertRaises(boc_import.BOCParseError):
                    boc_import.parse_document(ps,ts)

if __name__=='__main__':unittest.main(verbosity=2)
