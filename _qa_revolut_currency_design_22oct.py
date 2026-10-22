"""Demonstrate current EUR/USD source guards and the existing Setup/report limitation."""
from pathlib import Path
from io import BytesIO
from contextlib import closing
from unittest.mock import patch
import json,os,sqlite3,unittest
import pdfplumber
import db,parsing
from _qa_post_import_message import CompletionTests
from revolut_account_context import source_context,effective_account
from import_history import commit_statement

class CurrencyDesignTests(CompletionTests):
    def cases(self):
        root=Path(os.environ['ARETI_ISOLATION_ROOT'])
        info=next(s for s in json.loads((root/'source-probe-private.json').read_text(encoding='utf-8')) if s['key']=='natalia')
        content=(Path(r'C:/Users/Student/Downloads')/info['name']).read_bytes()
        original=parsing.parse_pdf(BytesIO(content))
        with pdfplumber.open(BytesIO(content)) as document:pages=[p.extract_text() or '' for p in document.pages]
        usd=original.copy();usd['statement_currency']='USD'
        usd.attrs['revolut_source_account']=source_context([p.replace('EUR Statement','USD Statement').replace('€','$') for p in pages],usd)
        live=json.loads((root/'import-state-private.json').read_text(encoding='utf-8'))
        account=next(a for a in live['accounts'] if a['id']==900)
        def balance(frame):
            evidence=frame.attrs['revolut_source_account']
            return {**{k:evidence[k] for k in ('account_number','currency','period_start','period_end','opening_balance','money_in','money_out','closing_balance')},'bank':'Revolut','source':'English Revolut Completed Source'}
        return account,[(original,balance(original)),(usd,balance(usd))]

    def test_same_iban_source_currency_isolated_and_explicit_wrong_currency_rejected(self):
        account,cases=self.cases();before=dict(account)
        for frame,balance in cases:
            effective=effective_account(frame,balance,account)
            self.assertEqual(effective['currency'],balance['currency'])
            self.assertEqual(account,before)
            wrong=dict(account,currency='USD' if balance['currency']=='EUR' else 'EUR')
            with self.assertRaises(ValueError):effective_account(frame,balance,wrong)

    def test_existing_ledger_distinguishes_currencies_but_duplicate_setup_key_is_blocked(self):
        account,cases=self.cases()
        with closing(db.get_connection()) as c:
            c.execute('INSERT INTO account_list(id,account_name,bank,account_number,currency,rate_type) VALUES(?,?,?,?,?,?)',tuple(account[k] for k in ('id','account_name','bank','account_number','currency','rate_type')))
            c.execute("INSERT INTO rates(rate_month,rate_type,rate_value) VALUES('2026-09-01','EUR/USD',1.1)");c.commit()
        for frame,balance in cases:
            effective=effective_account(frame,balance,account);mapped=db.apply_account_and_rates(frame,effective)
            result=commit_statement(db,mapped,'synthetic-currency.pdf','synthetic-'+balance['currency'],balance,effective)
            self.assertEqual(result[0],len(frame))
        with closing(db.get_connection()) as c:
            counts=dict(c.execute('SELECT currency,COUNT(*) FROM classified_transactions GROUP BY currency').fetchall())
            self.assertEqual(counts,{balance['currency']:len(frame) for frame,balance in cases})
            self.assertEqual(c.execute('SELECT COUNT(*) FROM statement_balances').fetchone()[0],2)
            with self.assertRaises(sqlite3.IntegrityError):c.execute('INSERT INTO account_list(account_name,bank,account_number,currency) VALUES(?,?,?,?)',(account['account_name'],account['bank'],account['account_number'],'USD'))
            c.rollback()
        self.assertEqual(db.get_accounts().loc[lambda d:d.id==account['id'],'currency'].iloc[0],'')

if __name__=='__main__':
    suite=unittest.TestSuite(CurrencyDesignTests(n) for n in CurrencyDesignTests.__dict__ if n.startswith('test_'))
    raise SystemExit(not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful())
