"""Focused source/balance/import regressions; private originals stay outside Git."""
from contextlib import closing
from decimal import Decimal
from io import BytesIO
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import pandas as pd
import parsing
import db
import streamlit as st
from streamlit.testing.v1 import AppTest
from comerica_balances import ComericaBalanceError, withdrawals_only_balance


def baseline():
    spec = importlib.util.spec_from_file_location('pinned_5055', os.environ['COMERICA_5055_BASELINE'])
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    return module


def fixture():
    panel = ('Account summary\nBeginning balance\nonSeptember1,2026 $1,000.00\n'
             'Less withdrawals\nElectronic(EFT)withdrawals -$25.00\n'
             'Endingbalance\n$975.00\nonSeptember4,2026')
    text = (panel+'\nAccount number 9090904128\nTotal Electronic Withdrawals: -$25.00\n'
            'Total Number of Electronic Withdrawals: 1')
    rows = [['2026-09-01', 'Synthetic fee', Decimal('-25.00')]]
    meta = dict(account_number='9090904128', currency='USD', period_start='2026-09-01', period_end='2026-09-04')
    return panel, text, rows, meta


class BalanceTests(unittest.TestCase):
    def test_exact_source_and_unchanged_transactions(self):
        source = Path(os.environ['COMERICA_4128_SOURCE'])
        expected = json.loads(Path(os.environ['COMERICA_4128_EXPECTED']).read_text())
        content = source.read_bytes()
        rows = parsing.parse_pdf(BytesIO(content))
        prior = baseline().parse_pdf(BytesIO(content))
        pd.testing.assert_frame_equal(rows, prior); self.assertEqual(rows.attrs, prior.attrs)
        self.assertEqual(rows.Date.tolist(), expected['dates'])
        self.assertEqual(rows.Amount.tolist(), [Decimal(v) for v in expected['amounts']])
        balance = parsing.extract_statement_balance(BytesIO(content), source.name)
        for key in ('account_number','period_start','period_end','currency'):
            self.assertEqual(balance[key], expected[key])
        for key in ('opening_balance','closing_balance','money_in','money_out'):
            self.assertEqual(balance[key], Decimal(expected[key]))
        self.assertEqual(balance['opening_balance']+balance['money_out']+balance['money_in'],balance['closing_balance'])

    def test_zero_money_in_valid_and_both_dated_close_orders(self):
        panel, text, rows, meta = fixture()
        for candidate in (panel, panel.replace('$975.00\nonSeptember4,2026', 'on September 4,2026 $975.00')):
            result = withdrawals_only_balance(candidate,text,rows,meta)
            self.assertEqual(result['money_in'], Decimal(0))
            self.assertEqual(result['money_out'], Decimal('-25.00'))
            self.assertEqual(result['closing_balance'], Decimal('975.00'))

    def test_inconsistent_evidence_rejected(self):
        panel,text,rows,meta = fixture()
        variants = [(panel.replace('$975.00','$976.00'),text,rows,meta),
                    (panel,text.replace('Withdrawals: 1','Withdrawals: 2'),rows,meta),
                    (panel,text.replace('Withdrawals: -$25.00','Withdrawals: -$24.00'),rows,meta),
                    (panel,text,[['2026-09-01','Synthetic fee',Decimal('25.00')]],meta),
                    (panel,text,[['2026-09-05','Synthetic fee',Decimal('-25.00')]],meta),
                    (panel,text+'\nAccount number 9090909999',rows,meta),
                    (panel,text,rows,dict(meta,period_end='2026-09-05'))]
        for args in variants:
            with self.subTest(args=args):
                with self.assertRaises(ComericaBalanceError): withdrawals_only_balance(*args)

    def test_other_summary_layout_is_not_assumed_zero(self):
        panel,text,rows,meta = fixture()
        self.assertIsNone(withdrawals_only_balance(panel.replace('Less withdrawals','Plus deposits $10.00\nLess withdrawals'),text,rows,meta))
        self.assertIsNone(withdrawals_only_balance(panel.replace('$975.00','Unknown'),text,rows,meta))

    def test_5055_boc_safra_outputs_identical(self):
        prior = baseline()
        for env in ('COMERICA_SOURCE','ARETI_SUMMARY_SOURCE','TIMUR_SOURCE'):
            source = Path(os.environ[env]); content=source.read_bytes()
            current=parsing.parse_pdf(BytesIO(content)); old=prior.parse_pdf(BytesIO(content))
            pd.testing.assert_frame_equal(current,old); self.assertEqual(current.attrs,old.attrs)
            self.assertEqual(parsing.extract_statement_balance(BytesIO(content),source.name),
                             prior.extract_statement_balance(BytesIO(content),source.name))

    def test_normal_upload_history_pending_rollback_duplicate(self):
        self.assertFalse(db.USING_POSTGRES)
        source=Path(os.environ['COMERICA_4128_SOURCE'])
        expected=json.loads(Path(os.environ['COMERICA_4128_EXPECTED']).read_text())
        uploaded=BytesIO(source.read_bytes()); uploaded.name=source.name
        with tempfile.TemporaryDirectory(dir=os.environ['TEMP']) as root, patch.object(db,'DB_PATH',str(Path(root)/'withdrawals.sqlite')):
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
                app.run(); self.assertFalse(app.exception); self.assertFalse(app.error)
                with closing(db.get_connection()) as c: before='\n'.join(c.iterdump())
                with patch.object(db,'save_statement_balance',side_effect=ValueError('Synthetic rollback')):
                    next(b for b in app.button if b.label=='Import statement').click().run()
                self.assertTrue(any('Synthetic rollback' in e.value for e in app.error))
                with closing(db.get_connection()) as c: self.assertEqual('\n'.join(c.iterdump()),before)
                next(b for b in app.button if b.label=='Import statement').click().run()
                self.assertFalse(app.exception); self.assertFalse(app.error)
                pending=db.get_pending_transactions()
                self.assertEqual(sorted(Decimal(str(v)) for v in pending.amount),sorted(Decimal(v) for v in expected['amounts']))
                self.assertEqual(set(pending.account_number),{expected['account_number']})
                history=db.get_import_history()
                self.assertEqual(len(history),1)
                self.assertEqual(history.transaction_count.iloc[0],len(expected['amounts']))
                self.assertEqual(history.reconciliation_status.iloc[0],'OK')
                self.assertEqual(Decimal(str(history.closing_balance.iloc[0])),Decimal(expected['closing_balance']))
                with closing(db.get_connection()) as c: before='\n'.join(c.iterdump())
                app.run()
                self.assertTrue(any('already exists' in e.value for e in app.warning))
                self.assertFalse(any(b.label=='Import statement' for b in app.button))
                with closing(db.get_connection()) as c: self.assertEqual('\n'.join(c.iterdump()),before)
            st.cache_data.clear(); st.cache_resource.clear()


if __name__ == '__main__': unittest.main(verbosity=2)
