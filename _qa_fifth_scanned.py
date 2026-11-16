"""Private exact-source OCR and disposable import lifecycle regression tests."""
from contextlib import closing
from copy import deepcopy
from decimal import Decimal as D
from hashlib import sha256
from io import BytesIO
import json
import os
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import pdfplumber
import db
import parsing
import fifth_third_scanned as scanned
import _qa_post_import_message as completion
from import_history import commit_statement


class ScannedTests(unittest.TestCase):
    setUp = completion.CompletionTests.setUp
    tearDown = completion.CompletionTests.tearDown
    csv = completion.CompletionTests.csv
    snapshot = completion.CompletionTests.snapshot
    app = completion.CompletionTests.app
    run_app = completion.CompletionTests.run_app
    import_file = completion.CompletionTests.import_file
    counts = completion.CompletionTests.counts

    @classmethod
    def setUpClass(cls):
        cls.sources = Path(os.environ['ARETI_FIFTH_SOURCE_ROOT'])
        cls.raw = (cls.sources/'original.pdf').read_bytes()
        assert sha256(cls.raw).hexdigest() == 'dc512ae77dba94d3a9399972e074e7cc3fe672aec7fe0163b52a063776bff9a1'
        original = scanned.parse_ocr
        def record(pages):
            cls.ocr_pages = pages
            return original(pages)
        with patch.object(scanned, 'parse_ocr', side_effect=record):
            cls.frame = parsing.parse_pdf(BytesIO(cls.raw))

    def configured(self):
        b = deepcopy(self.frame.attrs['statement_balance'])
        a = dict(account_name='Synthetic scanned account', bank='Comerica',
                 account_number=b['account_number'], currency='USD', rate_type='USD/USD')
        with closing(db.get_connection()) as c:
            c.execute('INSERT INTO account_list(account_name,bank,account_number,currency,rate_type) VALUES(?,?,?,?,?)', tuple(a.values()))
            c.commit()
        return db.apply_account_and_rates(deepcopy(self.frame), a), b, a

    def test_exact_image_ocr_and_balance_reader(self):
        b = self.frame.attrs['statement_balance']
        self.assertEqual(len(self.frame), 9)
        self.assertEqual(b['account_number'], '1894484128')
        self.assertEqual((b['period_start'], b['period_end']), ('2026-10-01', '2026-10-31'))
        self.assertEqual(self.frame.Amount.tolist(), list(map(D, ['-5000','-225','-149.31','-1119.02','-5401.83','-16','-75','-52.26','9960'])))
        for key, value in dict(opening_balance='10490.66',money_in='9960',money_out='12038.42',closing_balance='8412.24').items():
            self.assertEqual(b[key], D(value))
        self.assertEqual(b['opening_balance']+sum(self.frame.Amount,D(0)), b['closing_balance'])
        self.assertEqual(b['daily_balance_count'], 8)
        self.assertEqual(parsing.extract_statement_balance(BytesIO(self.raw), 'any.pdf'), b)

    def test_corrupt_or_incomplete_recognizable_layer_uses_fresh_ocr(self):
        for transform in (lambda t:'FTCSTMT002\n'+t, lambda t:t.replace('Ending Balance','Unreadable label')):
            with pdfplumber.open(BytesIO(self.raw)) as pdf:
                pages = [transform(p.extract_text() or '') for p in pdf.pages]
                result = SimpleNamespace(returncode=0,stdout=json.dumps({'pages':self.ocr_pages}))
                with patch.object(scanned.subprocess,'run',return_value=result) as ocr:
                    frame = scanned.parse_document(pdf,pages)
                self.assertEqual(ocr.call_count,1)
                self.assertEqual(frame.attrs,self.frame.attrs)

    def test_bad_ocr_fails_closed_without_writes(self):
        before = self.snapshot()
        for old,new in [('149.31','14931'),('7,038.42','7,038.a2'),('8,412.24','8,412.25'),
                        ('7 Withdrawals','8 Withdrawals'),('15,076.35','15,076.36'),
                        ('Account Summary - 1894484128','Account Summary - 1111111111'),
                        ('10/06 149.31','10/40 149.31')]:
            with self.subTest(old=old),self.assertRaisesRegex(scanned.FifthThirdParseError,'MANUAL REVIEW'):
                scanned.parse_ocr([self.ocr_pages[0].replace(old,new),*self.ocr_pages[1:]])
        self.assertEqual(self.snapshot(),before)

    def test_missing_runtime_fails_closed(self):
        before = self.snapshot()
        with patch.object(scanned.subprocess,'run',side_effect=FileNotFoundError('node absent')):
            with self.assertRaisesRegex(scanned.FifthThirdParseError,'MANUAL REVIEW'):
                parsing.parse_pdf(BytesIO(self.raw))
        self.assertEqual(before,self.snapshot())

    def test_future_period_and_different_account_not_hardcoded(self):
        pages=[p.replace('1894484128','1234567890').replace('10/','11/').replace('11/31/2026','11/30/2026').replace('11/31 Ending','11/30 Ending') for p in self.ocr_pages]
        b=scanned.parse_ocr(pages).attrs['statement_balance']
        self.assertEqual(b['account_number'],'1234567890')
        self.assertEqual((b['period_start'],b['period_end']),('2026-11-01','2026-11-30'))
        self.assertEqual(b['transaction_count'],9)

    def test_atomic_rollback_retry_history_and_duplicate(self):
        f,b,a=self.configured();before=self.snapshot();digest=sha256(self.raw).hexdigest()
        with patch.object(db,'save_statement_balance',side_effect=RuntimeError('forced rollback')):
            with self.assertRaisesRegex(RuntimeError,'forced rollback'):
                commit_statement(db,f,'source.pdf',digest,b,a)
        self.assertEqual(before,self.snapshot())
        self.assertEqual(commit_statement(db,f,'source.pdf',digest,b,a),(9,False,0))
        self.assertEqual(self.counts(),(9,1,1))
        saved=self.snapshot()
        self.assertEqual(commit_statement(db,f,'renamed.pdf',digest,b,a),(0,True,0))
        self.assertEqual(saved,self.snapshot())
        with closing(db.get_connection()) as c:
            self.assertEqual(c.execute('SELECT transaction_count FROM statement_imports').fetchone()[0],9)
            self.assertEqual(D(str(c.execute('SELECT closing_balance FROM statement_balances').fetchone()[0])),D('8412.24'))

    def test_actual_preview_explicit_import_and_rerun(self):
        self.configured();self.file=BytesIO(self.raw);self.file.name='source.pdf'
        before=self.snapshot();app=self.app();app.default_timeout=90
        self.run_app(app);self.assertEqual(before,self.snapshot());self.assertFalse(app.error,[e.value for e in app.error])
        selected=next(s for s in app.selectbox if s.label=='Account')
        self.assertIn('Synthetic scanned account',selected.value)
        self.import_file(app);self.assertFalse(app.error,[e.value for e in app.error]);self.assertEqual(self.counts(),(9,1,1))
        saved=self.snapshot();self.run_app(app);self.assertEqual(saved,self.snapshot())

    def test_previous_september_no_ocr(self):
        raw=(self.sources/'september-reference.pdf').read_bytes()
        with patch.object(scanned.subprocess,'run',side_effect=AssertionError('Unexpected OCR')):
            f=parsing.parse_pdf(BytesIO(raw));b=parsing.extract_statement_balance(BytesIO(raw),'reference.pdf')
        self.assertEqual(len(f),2)
        self.assertEqual(b['account_number'],'1894484128')
        self.assertEqual(b['opening_balance'],D('8492.67'))
        self.assertEqual(sum(f.Amount,D(0)),D('-361.48'))
        self.assertEqual(b['closing_balance'],D('8131.19'))


if __name__=='__main__':
    unittest.main()
