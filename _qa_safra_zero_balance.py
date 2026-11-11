"""Exact October Safra no-bookings source; local-only DB/UI validation."""
from contextlib import closing
from pathlib import Path
from io import BytesIO
from decimal import Decimal
import os,hashlib,unittest
from unittest.mock import patch
import pandas as pd
import db,parsing,safra_balance_only as action
import _qa_post_import_message as completion
from import_history import commit_statement

class SafraZeroTests(unittest.TestCase):
    setUp=completion.CompletionTests.setUp
    tearDown=completion.CompletionTests.tearDown
    csv=completion.CompletionTests.csv
    snapshot=completion.CompletionTests.snapshot
    app=completion.CompletionTests.app
    run_app=completion.CompletionTests.run_app

    def source(self):
        p=Path(os.environ['ARETI_FINAL_FIX_ROOT'])/'Timur 6.00670.2-2026-11-10-11217555-KAKO.pdf'
        raw=p.read_bytes()
        self.assertEqual(hashlib.sha256(raw).hexdigest(),'b7276db27a5c24c8e2faaf4008a6715600cbf4ac2c6396b6bf1766f9b3f33557')
        f=parsing.parse_pdf(BytesIO(raw))
        with closing(db.get_connection()) as c:
            for s in f.attrs['safra_sections']:
                c.execute('INSERT INTO account_list(account_name,bank,account_number,currency,rate_type) VALUES(?,?,?,?,?)',('Local Safra','Safra Sarasin',s['source_iban'],s['statement_currency'],s['statement_currency']+'/USD'))
            c.commit()
        return p,raw,f

    def test_exact_four_sections(self):
        _,raw,f=self.source();before=self.snapshot()
        self.assertTrue(action.recognized(raw));f,preview=action.prepare(db,raw)
        self.assertEqual(len(f),0);self.assertEqual(len(preview),4)
        self.assertEqual(preview.IBAN.tolist(),['CH2308750060067024000','CH9308750060067024001','CH6608750060067024002','CH3908750060067024003'])
        self.assertEqual(preview.Currency.tolist(),['EUR','USD','CHF','GBP'])
        self.assertEqual(preview['Closing balance'].tolist(),['196.20','56458.63','0.00','0.00'])
        self.assertTrue(preview['Period start'].eq('2026-10-01').all());self.assertTrue(preview['Period end'].eq('2026-10-31').all())
        self.assertEqual(before,self.snapshot())

    def test_atomic_repeat_history_balances_and_preservation(self):
        p,raw,f=self.source()
        with closing(db.get_connection()) as c:
            accounts=c.execute('SELECT * FROM account_list ORDER BY id').fetchall()
            transactions=c.execute('SELECT * FROM classified_transactions ORDER BY id').fetchall()
        self.assertEqual(action.record(db,raw,p.name,'local QA'),'recorded')
        with closing(db.get_connection()) as c:
            self.assertEqual(c.execute('SELECT * FROM account_list ORDER BY id').fetchall(),accounts)
            self.assertEqual(c.execute('SELECT * FROM classified_transactions ORDER BY id').fetchall(),transactions)
            self.assertEqual(c.execute('SELECT COUNT(*) FROM statement_balances').fetchone()[0],4)
            rows=c.execute('SELECT currency,closing_balance FROM statement_balances ORDER BY currency').fetchall()
            self.assertEqual({k:Decimal(str(v)) for k,v in rows},{'EUR':Decimal('196.20'),'USD':Decimal('56458.63'),'CHF':Decimal('0'),'GBP':Decimal('0')})
            self.assertEqual(c.execute('SELECT COUNT(*),SUM(transaction_count) FROM statement_imports').fetchone(),(4,0))
        saved=self.snapshot()
        self.assertEqual(action.record(db,raw,'renamed.pdf','local QA'),'already recorded')
        self.assertEqual(saved,self.snapshot())
        self.assertEqual(commit_statement(db,f,p.name,'document',{},{}),(0,True,0))
        self.assertEqual(saved,self.snapshot())

    def test_rollback_all_four(self):
        p,raw,_=self.source();before=self.snapshot();original=db.save_statement_balance;calls=[]
        def fail_third(*a,**kw):
            calls.append(1)
            if len(calls)==3:raise ValueError('forced rollback')
            return original(*a,**kw)
        with patch.object(db,'save_statement_balance',side_effect=fail_third):
            with self.assertRaisesRegex(ValueError,'forced rollback'):action.record(db,raw,p.name,'QA')
        self.assertEqual(before,self.snapshot())

    def test_wrong_currency_or_iban_rejected(self):
        _,raw,f=self.source()
        with closing(db.get_connection()) as c:
            c.execute("UPDATE account_list SET currency='USD' WHERE currency='EUR'");c.commit()
        before=self.snapshot()
        with self.assertRaisesRegex(ValueError,'exactly one'):action.prepare(db,raw)
        self.assertEqual(before,self.snapshot())

    def test_malformed_source_rejected(self):
        import pdfplumber
        _,raw,_=self.source()
        with pdfplumber.open(BytesIO(raw)) as pdf:pages=[p.extract_text() for p in pdf.pages]
        for old,new in [('196,20','196,2'),('No bookings were carried out during the period stated.',''),('Current account EUR','Current account USD'),('CH23 0875','CH24 0875')]:
            with self.subTest(old=old),self.assertRaises(ValueError):parsing._parse_safra_pages([p.replace(old,new) for p in pages])

    def test_existing_booked_safra_unchanged_and_balance_action_rejects(self):
        from _qa_safra_completion import fixture
        from _qa_safra_uat import labelled_accounts
        f=parsing._parse_safra_pages(fixture());self.assertEqual(len(f),9)
        accounts=labelled_accounts(f)
        with patch.object(db,'get_accounts',return_value=accounts):
            mapped=db.apply_account_and_rates(f,{})
            before=self.snapshot()
            with patch.object(parsing,'parse_pdf',return_value=f):
                with self.assertRaisesRegex(ValueError,'no bookings'):action.prepare(db,b'synthetic')
            self.assertEqual(before,self.snapshot())
            self.assertEqual(commit_statement(db,mapped,'synthetic.pdf','booked',{},{}),(9,False,0))
            saved=self.snapshot();self.assertEqual(commit_statement(db,mapped,'synthetic.pdf','booked',{},{}),(0,True,0));self.assertEqual(saved,self.snapshot())

    def test_actual_balance_only_ui(self):
        p,raw,_=self.source();self.file=BytesIO(raw);self.file.name=p.name
        before=self.snapshot();app=self.run_app(self.app());self.assertFalse(app.error,[e.value for e in app.error])
        with patch('streamlit.file_uploader',return_value=self.file):app.button(key='safra_balance_prepare').click().run()
        self.assertFalse(app.exception);self.assertFalse(app.error,[e.value for e in app.error]);self.assertEqual(before,self.snapshot())
        key='safra_balance_confirm_'+hashlib.sha256(raw).hexdigest()
        with patch('streamlit.file_uploader',return_value=self.file):app.checkbox(key=key).check().run();app.button(key='safra_balance_record').click().run()
        self.assertFalse(app.exception,[e.message for e in app.exception]);self.assertFalse(app.error,[e.value for e in app.error])
        self.assertTrue(any('balances recorded separately' in e.value for e in app.success))
        with closing(db.get_connection()) as c:
            self.assertEqual(c.execute('SELECT COUNT(*) FROM classified_transactions').fetchone()[0],0)
            self.assertEqual(c.execute('SELECT COUNT(*) FROM statement_balances').fetchone()[0],4)

if __name__=='__main__':unittest.main(verbosity=2)
