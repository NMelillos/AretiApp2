"""Exact original PDF fixtures are private; expected facts are source-reviewed."""
from contextlib import closing
from decimal import Decimal
from io import BytesIO
import json, os
from pathlib import Path
from unittest.mock import patch
import unittest
import db, parsing
from citi_refund import verified_refund, CitiSourceError
from cnb_import import parse_cnb, CNBParseError
from import_history import commit_statement
from _qa_post_import_message import CompletionTests

ROOT=Path(os.environ['ARETI_PACKAGE_SOURCES'])
FACTS=json.loads((ROOT/'autonomous-sources-private.json').read_text())

class SourceTests(CompletionTests):
    def test_citi_exact_sign_and_liability_reconciliation(self):
        expected=FACTS['citi']
        frame=parsing.parse_pdf(BytesIO(Path(os.environ['CITI_SOURCE']).read_bytes()))
        self.assertEqual([Decimal(str(v)) for v in frame.Amount],list(map(Decimal,expected['amounts'])))
        self.assertEqual(frame.transaction_type.tolist(),['expense','expense'])
        balance=frame.attrs['statement_balance']
        for name,key in (('opening_balance','opening'),('money_in','credits'),('money_out','debits'),('closing_balance','closing')):
            self.assertEqual(balance[name],Decimal(expected[key]))
        self.assertEqual(balance['opening_balance']+balance['money_in']-balance['money_out'],balance['closing_balance'])
        self.assertEqual(balance['opening_balance']-sum(frame.Amount,Decimal(0)),balance['closing_balance'])
        self.assertEqual(balance['account_number'],expected['account'])
        self.assertEqual(balance['period_start'],expected['period_start'])
        self.assertEqual(balance['period_end'],expected['period_end'])

    def test_citi_summary_conflict_rejected_and_unrelated_cards_unchanged(self):
        import pdfplumber
        with pdfplumber.open(os.environ['CITI_SOURCE']) as doc:
            text='\n'.join(page.extract_text() or '' for page in doc.pages)
        rows=parsing._parse_credit_card_pdf_text(text)
        self.assertGreater(Decimal(str(rows[0][2])),0)
        broken=text.replace('Purchases +$'+FACTS['citi']['credits'],'Purchases +$1.00')
        with self.assertRaises(CitiSourceError): verified_refund(broken,rows)
        self.assertIsNone(verified_refund('American Express\nCREDIT REFUND AS REQUESTED',rows))

    def test_all_three_cnb_originals_atomic_history_pending_duplicate(self):
        for expected in FACTS['cnb']:
            with self.subTest(source=expected['file']):
                before=self.snapshot()
                path=ROOT/expected['file']
                frame=parsing.parse_pdf(BytesIO(path.read_bytes()))
                self.assertEqual(self.snapshot(),before)
                self.assertEqual(frame.Date.tolist(),expected['dates'])
                self.assertEqual([Decimal(str(v)) for v in frame.Amount],list(map(Decimal,expected['amounts'])))
                balance=frame.attrs['statement_balance']
                for name,key in (('opening_balance','opening'),('money_in','credits'),('money_out','debits'),('closing_balance','closing')):
                    self.assertEqual(balance[name],Decimal(expected[key]))
                self.assertEqual(balance['period_start'],expected['period_start'])
                self.assertEqual(balance['period_end'],expected['period_end'])
                self.assertEqual(balance['opening_balance']+balance['money_in']-balance['money_out'],balance['closing_balance'])
                self.assertEqual(balance['transaction_count'],len(expected['amounts']))
                account=dict(account_name=expected['owner'],bank='CNB',account_number=expected['account'],currency='USD',rate_type='USD/USD')
                with closing(db.get_connection()) as conn:
                    conn.execute('INSERT INTO account_list(account_name,bank,account_number,currency,rate_type) VALUES(?,?,?,?,?)',tuple(account.values()));conn.commit()
                frame=db.apply_account_and_rates(frame,account)
                before=self.snapshot()
                with patch.object(db,'save_statement_balance',side_effect=ValueError('atomic failure')):
                    with self.assertRaisesRegex(ValueError,'atomic failure'):commit_statement(db,frame,path.name,path.stem,balance,account)
                self.assertEqual(self.snapshot(),before)
                self.assertEqual(commit_statement(db,frame,path.name,path.stem,balance,account),(len(frame),False,0))
                history=db.get_import_history(); row=history[history.statement_hash==path.stem].iloc[0]
                self.assertEqual(row.account_name,expected['owner']);self.assertEqual(row.account_number,expected['account'])
                self.assertEqual(row.reconciliation_status,'OK');self.assertEqual(row.balance_status,'OK')
                self.assertEqual(row.transaction_count,len(frame))
                stored=db.get_pending_transactions();stored=stored[stored.statement_hash==path.stem]
                self.assertEqual(len(stored),len(frame))
                after=self.snapshot();self.assertEqual(commit_statement(db,frame,path.name,path.stem,balance,account),(0,True,0));self.assertEqual(self.snapshot(),after)

    def test_cnb_missing_or_conflicting_sections_fail_closed(self):
        import pdfplumber
        for expected in FACTS['cnb']:
            with pdfplumber.open(ROOT/expected['file']) as doc:
                pages=[page.extract_text() or '' for page in doc.pages]; metadata=doc.metadata
            with self.assertRaises(CNBParseError):parse_cnb([p.replace('DAILY BALANCES','UNLABELLED') for p in pages],metadata)
            with self.assertRaises(CNBParseError):parse_cnb([p.replace('Total credits +$','Total credits +$1') for p in pages],metadata)

    def test_citi_normal_upload_atomic_history_pending_and_duplicate(self):
        expected=FACTS['citi'];path=Path(os.environ['CITI_SOURCE']);frame=parsing.parse_pdf(BytesIO(path.read_bytes()));balance=frame.attrs['statement_balance']
        account=dict(account_name='Synthetic Citi card',bank='Citi',account_number=expected['account'],currency='USD',rate_type='USD/USD')
        with closing(db.get_connection()) as conn:
            conn.execute('INSERT INTO account_list(account_name,bank,account_number,currency,rate_type) VALUES(?,?,?,?,?)',tuple(account.values()));conn.commit()
        frame=db.apply_account_and_rates(frame,account)
        before=self.snapshot()
        with patch.object(db,'save_statement_balance',side_effect=ValueError('Citi atomic failure')):
            with self.assertRaisesRegex(ValueError,'Citi atomic failure'):commit_statement(db,frame,path.name,'citi-source',balance,account)
        self.assertEqual(self.snapshot(),before)
        self.assertEqual(commit_statement(db,frame,path.name,'citi-source',balance,account),(2,False,0))
        pending=db.get_pending_transactions();self.assertEqual(sorted(Decimal(str(v)) for v in pending.amount),sorted(map(Decimal,expected['amounts'])))
        history=db.get_import_history().iloc[0];self.assertEqual(history.reconciliation_status,'OK');self.assertEqual(history.balance_status,'OK')
        saved=self.snapshot();self.assertEqual(commit_statement(db,frame,path.name,'citi-source',balance,account),(0,True,0));self.assertEqual(self.snapshot(),saved)

if __name__=='__main__':unittest.main()
