"""CNB wrapped activity and explicit zero activity; originals remain private."""
import hashlib
import os
from pathlib import Path
from decimal import Decimal as D
from io import BytesIO
from contextlib import closing
import unittest
from unittest.mock import patch
import pandas as pd
import parsing, db
from cnb_import import parse_cnb, CNBParseError
from _qa_cnb_import import TEXT, parse
import _qa_post_import_message as completion
from import_history import commit_statement

class CNBLayoutTests(unittest.TestCase):
    setUp = completion.CompletionTests.setUp
    tearDown = completion.CompletionTests.tearDown
    csv = completion.CompletionTests.csv
    snapshot = completion.CompletionTests.snapshot

    def original(self, name):
        root=Path(os.environ['ARETI_CNB_REVIEW_ROOT'])
        expected={'woking':'fc2f281807090fe83a2965cfc79a6797794809fbddc93f7260016b50d01c3b44', 'timur':'7015f90ba479dd0b1ac4370a164ffa555cadd0d9d32d2314047fb76a146b2f5b'}
        raw=(root/(name+'-original.pdf')).read_bytes()
        self.assertEqual(hashlib.sha256(raw).hexdigest(),expected[name])
        return parsing.parse_pdf(BytesIO(raw))

    def test_woking_original(self):
        f=self.original('woking');b=f.attrs['statement_balance']
        self.assertEqual(f.attrs['cnb_account'],'129036486')
        self.assertEqual(f.Date.tolist(),['2026-10-13','2026-10-23','2026-10-09','2026-10-30','2026-10-01','2026-10-13','2026-10-20','2026-10-22','2026-10-30'])
        self.assertEqual([D(str(x)) for x in f.Amount],list(map(D,['31000','14080','5000','5000','-800','-1725','-30600.96','-1500','-2300'])))
        self.assertIn('PROPERTY RENTAL FE E FOR: SEPTEMBER 2026',f.Description.iloc[2])
        self.assertEqual([b[k] for k in ('opening_balance','money_in','money_out','closing_balance')],list(map(D,['5000','55080','36925.96','23154.04'])))
        self.assertEqual((b['period_start'],b['period_end']),('2026-10-01','2026-10-30'))

    def test_timur_original(self):
        f=self.original('timur');b=f.attrs['statement_balance']
        self.assertEqual(len(f),0);self.assertEqual(f.attrs['cnb_account'],'129036281')
        self.assertEqual([b[k] for k in ('opening_balance','money_in','money_out','closing_balance')],list(map(D,['3287.17','0','0','3287.17'])))
        self.assertEqual((b['period_start'],b['period_end']),('2026-10-01','2026-10-30'))

    def test_known_good_normal(self):
        f=parse();self.assertEqual(f.Amount.tolist(),[1000,-600,-150])
        self.assertEqual(f.attrs['cnb_account'],'987654321')
        self.assertEqual(f.attrs['statement_balance']['period_start'],'2026-08-01')

    def test_malformed_wrapping_rejected(self):
        for replacement in ('8-21 Synthetic debit\n8-22 New row 600.00','8-21 Synthetic debit\nDAILY BALANCES','7-21 Synthetic debit\nALPHA 600.00','8-21 Synthetic debit\nALPHA -600.00','8-21 Synthetic debit 600.000'):
            with self.subTest(replacement=replacement),self.assertRaises((CNBParseError,ValueError)):
                parse(TEXT.replace('8-21 Synthetic debit reference ALPHA 600.00',replacement))

    def test_reconciliation_rejected(self):
        with self.assertRaises(CNBParseError):parse(TEXT.replace('$250.00','$251.00'))

    def test_zero_activity_guards(self):
        import pdfplumber
        root=Path(os.environ['ARETI_CNB_REVIEW_ROOT'])
        with pdfplumber.open(root/'timur-original.pdf') as pdf:
            pages=[p.extract_text() or '' for p in pdf.pages];meta=pdf.metadata
        for old,new in [('No activity this statement period','Unknown activity'),('Credits + $0.00','Credits + $1.00'),('Debits - $0.00','Debits - $1.00'),('Ending balance (10/30/2026) $3,287.17','Ending balance (10/30/2026) $3,287.18'),('Account number 129036281','Account number 129036282'),('** No activity this statement period **','** No activity this statement period **\n10-1 Hidden movement 1.00')]:
            with self.subTest(old=old),self.assertRaises((CNBParseError,ValueError)):
                parse_cnb([p.replace(old,new) for p in pages],meta)

    def test_atomic_import_duplicate_preservation_rollback(self):
        for name,count in [('woking',9),('timur',0)]:
            with self.subTest(source=name):
                f=self.original(name);b=f.attrs['statement_balance']
                account=dict(account_name='Local CNB',bank='CNB',account_number=f.attrs['cnb_account'],currency='USD',rate_type='USD/USD')
                with closing(db.get_connection()) as c:
                    c.execute('INSERT INTO account_list(account_name,bank,account_number,currency,rate_type) VALUES(?,?,?,?,?)',tuple(account.values()));c.commit()
                f=db.apply_account_and_rates(f,account)
                before=self.snapshot()
                with patch.object(db,'save_statement_balance',side_effect=ValueError('forced rollback')):
                    with self.assertRaisesRegex(ValueError,'forced rollback'):
                        commit_statement(db,f,name+'.pdf',name,b,account)
                self.assertEqual(self.snapshot(),before)
                self.assertEqual(commit_statement(db,f,name+'.pdf',name,b,account),(count,False,0))
                saved=self.snapshot()
                self.assertEqual(commit_statement(db,f,name+'.pdf',name,b,account),(0,True,0))
                self.assertEqual(self.snapshot(),saved)
                history=db.get_import_history();row=history[history.statement_hash==name].iloc[0]
                self.assertEqual(row.transaction_count,count);self.assertEqual(row.reconciliation_status,'OK')
        self.assertEqual(len(db.get_all_transactions()),9)

if __name__=='__main__':unittest.main(verbosity=2)
