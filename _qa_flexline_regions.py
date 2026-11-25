"""Synthetic format contracts plus private exact-source, disposable lifecycle QA."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from decimal import Decimal
from hashlib import sha256
from io import BytesIO
import json
import os
from pathlib import Path
import unittest
from unittest.mock import patch

import db
import flexline_balance_metadata as loc
import flexline_region_ocr as ocr
import _qa_post_import_message as completion_qa


def query(connection, statement, parameters=()):
    cursor = connection.cursor()
    cursor.execute(statement, parameters)
    return cursor


def synthetic():
    """Different month, amounts and payment count; no private source fixture."""
    values = ['100.00', '0.00', '3.00', '5.00', '98.00', '3.00']
    labels = ['ACCOUNT', 'PREVIOUS BALANCE', 'ADVANCES AND DEBITS', 'FINANCE CHARGE',
              'PAYMENTS AND CREDITS', 'NEW BALANCE', 'MINIMUM PAYMENT DUE']
    return dict(issuer='FIFTH THIRD', owner='WOKING WAY LLC', product='Fifth Third Equity FlexlineSM',
        identity=f'LINE OF CREDIT STATEMENT STATEMENT DATE JANUARY 06, 2031 ACCOUNT NUMBER {loc.SOURCE_ACCOUNT} '
                 'PAYMENT DUE DATE FEBRUARY 01, 2031 MINIMUM PAYMENT DUE 3.00 NEW BALANCE 98.00 MATURITY DATE SEPTEMBER 01, 2041',
        dates='STATEMENT DATE PAYMENT DATE CREDIT LINE AVAILABLE CREDIT MINIMUM PAYMENT DUE '
              '01/06/31 02/01/31 1,000.00 902.00 3.00',
        history='HISTORY SUMMARY DATE DESCRIPTION OF TRANSACTIONS, ADVANCES, PAYMENTS, AND CREDITS AMOUNT '
                '01/01/31 PAYMENT 2.00 CR 01/02/31 PAYMENT 3.00 CR',
        interest='INTEREST RATE SUMMARY ANNUAL PERCENTAGE DAILY AVERAGE DAYS FINANCE '
                 'ACCOUNT RATE PERIODIC RATE DAILY BALANCE INCYCLE CHARGE LINE OF CREDIT 7.750000 0.0212328 100.00 30 3.00',
        summary='ACCOUNT SUMMARY PREVIOUS ADVANCES PAYMENTS NEW MINIMUM ACCOUNT BALANCE AND DEBITS '
                'FINANCE CHARGE AND CREDITS BALANCE PAYMENT DUE LINE OF CREDIT ' + ' '.join(values),
        **{f'cell{i}': label+' '+value for i, (label, value) in enumerate(zip(labels, [loc.SOURCE_ACCOUNT]+values))})


class ContractTests(unittest.TestCase):
    def valid(self, r):
        return ocr.validate(r, loc.SOURCE_ACCOUNT, 'WOKING WAY LLC')

    def test_future_month_and_multiple_payments(self):
        result = self.valid(synthetic())
        self.assertEqual(result['period_end'], '2031-01-06')
        self.assertEqual(result['closing_balance'], Decimal('-98.00'))

    def test_each_missing_region_rejected(self):
        for key in synthetic():
            with self.subTest(key=key):
                r=synthetic();del r[key]
                with self.assertRaises(ocr.FlexlineValidationError):self.valid(r)

    def test_identity_dates_conflicts_malformed_and_ambiguous_rejected(self):
        mutations=[('issuer','FIFTH THIRD','OTHER'),('owner','WOKING WAY LLC','OTHER'),
            ('product','Equity Flexline','Checking'),('identity','LINE OF CREDIT STATEMENT','OTHER'),
            ('identity',loc.SOURCE_ACCOUNT,'00548546518'),('identity',loc.SOURCE_ACCOUNT,''),
            ('cell0',loc.SOURCE_ACCOUNT,'00548546518'),('cell0',loc.SOURCE_ACCOUNT,loc.SOURCE_ACCOUNT+' '+loc.SOURCE_ACCOUNT),
            ('dates','01/06/31','01/07/31'),('identity','JANUARY 06','JANUARY 99'),
            ('identity','98.00','98.01'),('cell1','100.00','100.01'),('cell2','0.00','[000'),
            ('cell3','3.00','3.01'),('cell4','5.00','5.01'),('cell5','98.00','98.01'),
            ('summary','100.00','100.01'),('summary','0.00 3.00','3.00'),
            ('interest','30 3.00','30 3.01'),('history','2.00','2.01'),('cell6','3.00','3.00 3.00')]
        for key, old, new in mutations:
            with self.subTest(key=key,old=old,new=new):
                r=synthetic();r[key]=r[key].replace(old,new)
                with self.assertRaises(ocr.FlexlineValidationError):self.valid(r)

    def test_reconciles_only_after_agreement_and_no_double_negation(self):
        r=synthetic()
        for key in ('summary','cell1'):r[key]=r[key].replace('100.00','100.01')
        with self.assertRaises(ocr.FlexlineValidationError):self.valid(r)
        r=synthetic()
        for key in ('summary','cell1'):r[key]=r[key].replace('100.00','-96.00')
        for key in ('summary','identity','cell5'):r[key]=r[key].replace('98.00','-98.00')
        self.assertEqual(self.valid(r)['closing_balance'],Decimal('-98.00'))

    def test_bounded_cache_and_failure_gate(self):
        import duplicate_balance_import as gate
        from unittest.mock import Mock
        ocr._CACHE.clear()
        with patch.object(ocr,'_extract',return_value=None) as run:
            for _ in range(2):self.assertIsNone(ocr.evidence(b'unrelated','account','owner'))
            self.assertEqual(run.call_count,1)
            for i in range(12):ocr.evidence(str(i).encode(),'account','owner')
            self.assertEqual(len(ocr._CACHE),8)
        fake=Mock();fake.session_state={}
        with patch.object(loc,'recognized',side_effect=ocr.FlexlineValidationError('manual review')):
            self.assertTrue(gate.render_controls(fake,b'probable','source.pdf'))
            self.assertTrue(gate.render_controls(fake,b'probable','source.pdf'))
        self.assertEqual(fake.error.call_count,2)
        ocr._CACHE.clear()


class LifecycleTests(unittest.TestCase):
    csv=completion_qa.CompletionTests.csv
    snapshot=completion_qa.CompletionTests.snapshot
    def counts(self):
        with closing(db.get_connection()) as c:
            return tuple(query(c, 'SELECT COUNT(*) FROM '+t).fetchone()[0]
                         for t in ('classified_transactions','statement_imports','statement_balances'))
    app=completion_qa.CompletionTests.app
    run_app=completion_qa.CompletionTests.run_app

    def setUp(self):
        completion_qa.CompletionTests.setUp(self)
        self.seed()

    def tearDown(self):
        completion_qa.CompletionTests.tearDown(self)

    def seed(self):
        with closing(db.get_connection()) as c:
            query(c, 'INSERT INTO account_list(account_name,bank,account_number,currency,rate_type) VALUES(?,?,?,?,?)',
                      ('WOKING WAY LLC','Fifth Third (ex-Comerica)',loc.SETUP_ACCOUNT,'USD','USD/USD'))
            c.commit()

    def source(self):
        content=Path(os.environ['ARETI_FLEXLINE_IMAGE_SOURCE']).read_bytes()
        self.assertEqual(sha256(content).hexdigest(),'6034fa9a036b707a0623418fb4a81bee5a1313c2b83238f2aa26b4b5ad5fb84b')
        return content

    def test_exact_source_metadata_idempotency_and_historical_preservation(self):
        content=self.source()
        old=(Path(os.environ['ARETI_PACKAGE_SOURCES'])/'flexline-original.pdf').read_bytes()
        with patch.object(loc,'region_evidence',side_effect=AssertionError('Native OCR forbidden')):
            self.assertTrue(loc.recognized(old))
            self.assertEqual(loc.parse(old)['closing_balance'],Decimal('-771915.46'))
            loc.record(db,old,'prior.pdf','Synthetic reviewer')
        with closing(db.get_connection()) as c:
            old_rows=query(c, 'SELECT * FROM statement_balances').fetchall()
        frozen=self.snapshot()
        self.assertTrue(loc.recognized(content));parsed,account=loc.prepare(db,content)
        self.assertEqual(frozen,self.snapshot())
        self.assertEqual(parsed['period_end'],'2026-11-06')
        self.assertEqual(parsed['closing_balance'],Decimal('-770939.53'))
        self.assertEqual(loc.record(db,content,'source.pdf','Synthetic reviewer'),'recorded')
        self.assertEqual(self.counts(),(0,2,2))
        with closing(db.get_connection()) as c:
            self.assertEqual(old_rows,query(c, 'SELECT * FROM statement_balances WHERE statement_hash=?',(sha256(old).hexdigest(),)).fetchall())
            row=query(c, 'SELECT period_start,opening_balance,money_in,money_out,notes FROM statement_balances WHERE statement_hash=?',(sha256(content).hexdigest(),)).fetchone()
        self.assertEqual(row[:4],('',None,None,None))
        audit=json.loads(row[4]);self.assertEqual(audit['transaction_changes'],0)
        frozen=self.snapshot()
        for data,name in ((content,'source.pdf'),(content,'renamed.pdf'),(content+b'\n','copy.pdf')):
            self.assertEqual(loc.record(db,data,name,'Synthetic reviewer'),'already recorded')
            self.assertEqual(frozen,self.snapshot())

    def test_rollback_conflict_setup_and_invalid_source_leave_state_unchanged(self):
        content=self.source();frozen=self.snapshot()
        with patch.object(db,'save_statement_balance',side_effect=RuntimeError('forced rollback')):
            with self.assertRaises(RuntimeError):loc.record(db,content,'source.pdf','Synthetic reviewer')
        self.assertEqual(frozen,self.snapshot())
        for key in synthetic():
            bad=synthetic();del bad[key]
            with patch.object(loc,'region_evidence',side_effect=lambda *a:ocr.validate(bad,loc.SOURCE_ACCOUNT,'WOKING WAY LLC')):
                with self.assertRaises(ValueError):loc.record(db,content,'bad.pdf','Synthetic reviewer')
            self.assertEqual(frozen,self.snapshot())
        loc.record(db,content,'source.pdf','Synthetic reviewer')
        with closing(db.get_connection()) as c:
            query(c, 'UPDATE statement_balances SET closing_balance=1');c.commit()
        frozen=self.snapshot()
        with self.assertRaisesRegex(ValueError,'conflicts'):loc.record(db,content,'source.pdf','Synthetic reviewer')
        self.assertEqual(frozen,self.snapshot())
        with closing(db.get_connection()) as c:
            query(c, "UPDATE account_list SET currency='EUR' WHERE account_number=?",(loc.SETUP_ACCOUNT,));c.commit()
        frozen=self.snapshot()
        with self.assertRaises(ValueError):loc.record(db,content,'source.pdf','Synthetic reviewer')
        self.assertEqual(frozen,self.snapshot())

    def test_concurrent_confirmation(self):
        content=self.source();loc.prepare(db,content)
        with ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(lambda _:loc.record(db,content,'source.pdf','Synthetic reviewer'),range(2)))
        self.assertEqual(sorted(results),['already recorded','recorded'])
        self.assertEqual(self.counts(),(0,1,1))

    def test_existing_transactions_are_never_rewritten(self):
        content=self.source()
        with closing(db.get_connection()) as c:
            query(c, 'INSERT INTO classified_transactions(statement_hash,statement_name,row_hash,amount,reviewed) VALUES(?,?,?,?,?)',
                  (sha256(content).hexdigest(),'historical.csv','synthetic-history',-1,1))
            c.commit()
        frozen=self.snapshot()
        with self.assertRaises(ValueError):loc.record(db,content,'source.pdf','Synthetic reviewer')
        self.assertEqual(frozen,self.snapshot())

    def test_ambiguous_setup_rejected_without_writes(self):
        content=self.source()
        with closing(db.get_connection()) as c:
            query(c, 'INSERT INTO account_list(account_name,bank,account_number,currency,rate_type) VALUES(?,?,?,?,?)',
                  ('Woking Way LLC','Fifth Third (ex-Comerica)',loc.SETUP_ACCOUNT,'USD','USD/USD'))
            c.commit()
        frozen=self.snapshot()
        with self.assertRaises(ValueError):loc.record(db,content,'source.pdf','Synthetic reviewer')
        self.assertEqual(frozen,self.snapshot())

    def test_actual_import_page_preview_confirmation_and_rerun(self):
        import parsing
        import streamlit as st
        content=self.source();self.file=BytesIO(content);self.file.name='source.pdf'
        frozen=self.snapshot()
        with patch.object(parsing,'parse_pdf',side_effect=AssertionError('Generic parser forbidden')):
            app=self.run_app(self.app())
            self.assertEqual(frozen,self.snapshot())
            button=next(b for b in app.button if b.label=='Import Line of Credit Balance Only')
            self.assertTrue(button.disabled)
            self.assertFalse(any(b.label=='Import statement' for b in app.button))
            rendered=str([e.value for e in app.table])
            self.assertIn('2026-11-06',rendered);self.assertIn('770,939.53',rendered)
            with patch.object(st,'file_uploader',return_value=self.file):
                app.checkbox(key='flexline_confirm_'+sha256(content).hexdigest()).check().run()
                self.assertEqual(frozen,self.snapshot())
                app.button(key='flexline_record').click().run()
            self.assertFalse(app.exception,[e.message for e in app.exception])
            self.assertEqual(self.counts(),(0,1,1))
            frozen=self.snapshot();self.run_app(app);self.assertEqual(frozen,self.snapshot())
