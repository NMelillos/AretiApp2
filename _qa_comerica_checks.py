"""Checks-only Comerica source/import tests; originals remain outside Git."""
import ast
from contextlib import closing
from decimal import Decimal
from io import BytesIO
import json
import importlib.util
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import db
import parsing
from comerica_checks import ComericaChecksError, parse_checks_only
import streamlit as st
from streamlit.testing.v1 import AppTest


class Page:
    width=612; height=792
    def __init__(self,text): self.text=text
    def extract_text(self): return self.text
    def crop(self,bbox): return self


class PDF:
    def __init__(self,text): self.pages=[Page(text)]


def fixture():
    return ('COMERICA BANK\nSeptember 1, 2026 to September 4, 2026\nAccount number 9090905055\n'
            'Beginning balance\non September 1, 2026 $1,000.00\nLess withdrawals\nChecks -$25.00\n'
            'Ending balance $975.00\non September 4, 2026\nChecks paid this statement period\n'
            '#42 -25.00 Sep01 123456789\nTotal checks paid this statement period: -$25.00\n'
            'Total number of checks paid this statement period: 1\n')


class ChecksTests(unittest.TestCase):
    def test_original_source(self):
        expected=json.loads(Path(os.environ['COMERICA_EXPECTED']).read_text())
        source=Path(os.environ['COMERICA_SOURCE'])
        rows=parsing.parse_pdf(BytesIO(source.read_bytes()))
        balance=parsing.extract_statement_balance(BytesIO(source.read_bytes()),source.name)
        self.assertEqual(len(rows),1)
        self.assertEqual(rows.Date.tolist(),[expected['date']])
        self.assertEqual(rows.Amount.tolist(),[Decimal(expected['amount'])])
        for key in ('account_number','period_start','period_end','currency'):
            self.assertEqual(balance[key],expected[key])
        for key in ('opening_balance','closing_balance','money_in','money_out'):
            self.assertEqual(balance[key],Decimal(expected[key]))
        self.assertEqual(balance['opening_balance']+balance['money_in']+balance['money_out'],balance['closing_balance'])

    def test_synthetic_signed_check(self):
        rows,balance=parse_checks_only(PDF(fixture()))
        self.assertEqual(rows[0][2],Decimal('-25.00'))
        self.assertEqual(rows[0][0],'2026-09-01')
        self.assertIn('Check #42',rows[0][1])
        self.assertEqual(balance['closing_balance'],Decimal('975.00'))

    def test_count_total_balance_date_fail_closed(self):
        for changed in (fixture().replace('period: 1','period: 2'),
                        fixture().replace('period: -$25.00','period: -$26.00'),
                        fixture().replace('$975.00','$974.00'),
                        fixture().replace('Sep01','Sep05')):
            with self.subTest(source=changed):
                with self.assertRaises(ComericaChecksError): parse_checks_only(PDF(changed))
                with patch.object(parsing.pdfplumber,'open',return_value=PDFContext(changed)):
                    with self.assertRaises(ComericaChecksError): parsing.parse_pdf(BytesIO(b'synthetic'))

    def test_legacy_comerica_unchanged(self):
        text='COMERICA BANK\nSeptember 1, 2026 to September 4, 2026\nElectronic withdrawals\nSep 01 25.00 Synthetic fee\n'
        self.assertIsNone(parse_checks_only(PDF(text)))
        self.assertEqual(parsing._parse_comerica_pdf_text(text)[0][2],Decimal('-25.00'))

    def test_other_bank_sources(self):
        spec=importlib.util.spec_from_file_location('baseline_parsing',os.environ['COMERICA_BASELINE'])
        baseline=importlib.util.module_from_spec(spec); spec.loader.exec_module(baseline)
        import pandas as pd
        for env in ('ARETI_SUMMARY_SOURCE','TIMUR_SOURCE'):
            source=Path(os.environ[env])
            rows=parsing.parse_pdf(BytesIO(source.read_bytes()))
            prior=baseline.parse_pdf(BytesIO(source.read_bytes()))
            if env == 'ARETI_SUMMARY_SOURCE':
                # This package intentionally adds source-column BOC metadata.
                # Preserve this known-good source's existing transaction values
                # and descriptions; separate BOC QA validates new source facts.
                pd.testing.assert_frame_equal(rows[prior.columns], prior)
                meta = rows.attrs['statement_balance']
                self.assertEqual(meta['opening_balance'] + meta['money_in'] - meta['money_out'], meta['closing_balance'])
                continue
            pd.testing.assert_frame_equal(rows,prior)
            self.assertEqual(rows.attrs,prior.attrs)
            if env=='TIMUR_SOURCE':
                self.assertEqual([s['transaction_count'] for s in rows.attrs['safra_sections']],[0,3,0,0])

    def test_normal_upload_persistence_duplicate_and_rollback(self):
        self.assertFalse(db.USING_POSTGRES)
        source=Path(os.environ['COMERICA_SOURCE'])
        expected=json.loads(Path(os.environ['COMERICA_EXPECTED']).read_text())
        uploaded=BytesIO(source.read_bytes()); uploaded.name=source.name
        with tempfile.TemporaryDirectory(dir=os.environ['TEMP']) as root, patch.object(db,'DB_PATH',str(Path(root)/'checks.sqlite')):
            db.init_db()
            with closing(db.get_connection()) as c:
                c.execute("INSERT INTO category_list(category,subcategory) VALUES('Synthetic','General')")
                c.execute('INSERT INTO account_list(account_name,bank,account_number,currency,rate_type) VALUES(?,?,?,?,?)',
                          ('Synthetic','Comerica',expected['account_number'],'USD','USD/USD'))
                c.execute("INSERT INTO rates(rate_month,rate_type,rate_value) VALUES('2026-09-01','USD/USD',1)")
                c.commit()
            st.cache_data.clear(); st.cache_resource.clear()
            app=AppTest.from_file('app.py',default_timeout=45)
            app.session_state['authenticated']=True; app.session_state['login_user']='Synthetic reviewer'
            app.query_params['page']='Import'
            with patch.object(st,'file_uploader',return_value=uploaded):
                app.run()
                self.assertFalse(app.exception)
                self.assertFalse(app.error,[e.value for e in app.error])
                button=next(b for b in app.button if b.label=='Import statement')
                with closing(db.get_connection()) as c: before='\n'.join(c.iterdump())
                with patch.object(db,'save_statement_balance',side_effect=ValueError('Synthetic rollback')):
                    button.click().run()
                self.assertTrue(any('Synthetic rollback' in e.value for e in app.error))
                with closing(db.get_connection()) as c: self.assertEqual('\n'.join(c.iterdump()),before)
                next(b for b in app.button if b.label=='Import statement').click().run()
                self.assertFalse(app.exception); self.assertFalse(app.error)
                pending=db.get_pending_transactions()
                self.assertEqual(len(pending),1)
                self.assertEqual(Decimal(str(pending.amount.iloc[0])),Decimal(expected['amount']))
                self.assertEqual(pending.account_number.iloc[0],expected['account_number'])
                history=db.get_import_history()
                self.assertEqual(len(history),1)
                self.assertEqual(history.transaction_count.iloc[0],1)
                self.assertEqual(history.reconciliation_status.iloc[0],'OK')
                with closing(db.get_connection()) as c: before='\n'.join(c.iterdump())
                app.run()
                self.assertTrue(any('already exists' in e.value for e in app.warning))
                self.assertFalse(any(b.label=='Import statement' for b in app.button))
                with closing(db.get_connection()) as c: self.assertEqual('\n'.join(c.iterdump()),before)
            st.cache_data.clear(); st.cache_resource.clear()


class PDFContext(PDF):
    metadata={}
    def __enter__(self): return self
    def __exit__(self,*args): pass


if __name__=='__main__': unittest.main(verbosity=2)
