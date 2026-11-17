"""Exact original Greek PDF; disposable section lifecycle and preservation."""
from contextlib import closing
from decimal import Decimal as D
from hashlib import sha256
from io import BytesIO
from pathlib import Path
from unittest.mock import patch
import os
import tempfile
import unittest
import db
import parsing
import revolut_sections as sections
from import_history import commit_statement

TABLES=('account_list','classified_transactions','statement_imports','statement_balances',
        'transaction_memory','category_list','rates')

class SectionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.raw=(Path(os.environ['ARETI_GREEK_SOURCE_ROOT'])/'Natalia Revolut 1.10.2026-31.10.2026.pdf').read_bytes()
        cls.digest=sha256(cls.raw).hexdigest()
        assert cls.digest=='ecb014a65d873f118c7888da2a2866e034ff9d4d1a09b3ceef8759f3d8bebf2c'
        cls.source=parsing.parse_pdf(BytesIO(cls.raw))

    def setUp(self):
        self.assertFalse(db.USING_POSTGRES)
        temp=tempfile.TemporaryDirectory(dir=os.environ['TEMP'])
        self.addCleanup(temp.cleanup)
        p=patch.object(db,'DB_PATH',str(Path(temp.name)/'sections.sqlite'))
        p.start();self.addCleanup(p.stop)
        db.init_db();self.seed()

    def seed(self):
        with closing(db.get_connection()) as connection, closing(connection.cursor()) as c:
            c.execute("INSERT INTO category_list(category,subcategory,report_group) VALUES('Keep','Keep','Keep')")
            c.execute("INSERT INTO account_list(account_name,bank,account_number,currency,rate_type) VALUES('Natalia Fishman','Revolut','LT453250028012133298','','')")
            c.execute("INSERT INTO rates(rate_month,rate_type,rate_value) VALUES('2026-10-01','EUR/USD',1.12)")
            c.execute("INSERT INTO rates(rate_month,rate_type,rate_value) VALUES('2026-10-01','USD/USD',1)")
            c.execute("INSERT INTO statement_imports(statement_hash,statement_name,transaction_count,imported_at) VALUES('legacy','old.pdf',1,'old')")
            c.execute("INSERT INTO statement_balances(statement_hash,currency,closing_balance,imported_at,updated_at) VALUES('legacy','EUR',12.34,'old','old')")
            c.execute("INSERT INTO classified_transactions(statement_hash,txn_date,amount,currency,category,reviewed,status,split_group_id,created_at) VALUES('legacy','2026-01-01',12.34,'EUR','Keep',1,'reviewed','split-keep','old')")
            connection.commit()

    def snapshot(self):
        with closing(db.get_connection()) as connection, closing(connection.cursor()) as c:
            return {t:c.execute('SELECT * FROM '+t+' ORDER BY id').fetchall() for t in TABLES}

    def preview(self):
        return db.apply_account_and_rates(self.source.copy(),{})

    def save(self,frame=None,name='original.pdf'):
        return commit_statement(db,self.preview() if frame is None else frame,name,self.digest,{}, {})

    def test_source_and_preview(self):
        before=self.snapshot();f=self.preview();w=f.attrs['revolut_sections']
        self.assertEqual([len(x.transactions) for x in w],[115,7])
        self.assertEqual([x.iban for x in w],['LT453250028012133298']*2)
        for x,expected in zip(w,[('6.90','33136.73','22650.96','10492.67'),('1073.78','0','549.47','524.31')]):
            self.assertEqual((x.opening,x.credits,x.debits,x.closing),tuple(map(D,expected)))
            self.assertEqual(x.opening+x.credits-x.debits,x.closing)
            self.assertTrue(all(isinstance(t.amount,D) and t.currency==x.currency for t in x.transactions))
        self.assertEqual(sum(x.pending_count for x in w),2)
        self.assertEqual(sum(x.reverted_count for x in w),11)
        self.assertEqual(f.groupby('currency').size().to_dict(),{'EUR':115,'USD':7})
        self.assertEqual(len(set(map(sections.wallet_key,w))),2)
        self.assertEqual(sections.preview_sections(f).Currency.tolist(),['EUR','USD'])
        self.assertEqual(before,self.snapshot())

    def test_atomic_import_duplicates_and_preservation(self):
        before=self.snapshot();self.assertEqual(self.save(),(122,False,0));after=self.snapshot()
        for table,old in before.items():
            self.assertEqual([r for r in after[table] if r[0] in {o[0] for o in old}],old)
        for table in ('account_list','transaction_memory','category_list','rates'):
            self.assertEqual(before[table],after[table])
        with closing(db.get_connection()) as connection, closing(connection.cursor()) as c:
            rows=c.execute("SELECT currency,closing_balance,source,notes FROM statement_balances WHERE statement_hash<>'legacy' ORDER BY currency").fetchall()
            self.assertEqual([(r[0],D(str(r[1]))) for r in rows],[('EUR',D('10492.67')),('USD',D('524.31'))])
            self.assertTrue(all(self.digest in r[2] and self.digest in r[3] for r in rows))
            counts=c.execute("SELECT currency,COUNT(*) FROM classified_transactions WHERE statement_hash<>'legacy' GROUP BY currency ORDER BY currency").fetchall()
            self.assertEqual(counts,[('EUR',115),('USD',7)])
        self.assertEqual(self.save(),(0,True,0));self.assertEqual(after,self.snapshot())
        self.assertEqual(self.save(name='renamed.pdf'),(0,True,0));self.assertEqual(after,self.snapshot())

    def test_second_wallet_failure_rolls_back_and_retry(self):
        before=self.snapshot();original=db.save_statement_balance
        def fail(key,name,balance,account,**kw):
            if balance['currency']=='USD':raise RuntimeError('forced second wallet failure')
            return original(key,name,balance,account,**kw)
        with patch.object(db,'save_statement_balance',side_effect=fail):
            with self.assertRaisesRegex(RuntimeError,'forced second'):self.save()
        self.assertEqual(before,self.snapshot())
        self.assertEqual(self.save(),(122,False,0))

    def test_partial_existing_wallet_fails_closed(self):
        self.save()
        key=sections.wallet_key(self.source.attrs['revolut_sections'][1])
        with closing(db.get_connection()) as connection, closing(connection.cursor()) as c:
            for table in ('classified_transactions','statement_balances','statement_imports'):
                c.execute('DELETE FROM '+table+' WHERE statement_hash=?',(key,))
            connection.commit()
        before=self.snapshot()
        with self.assertRaisesRegex(ValueError,'partial existing'):self.save()
        self.assertEqual(before,self.snapshot())

    def test_invalid_preview_and_identity_fail_without_writes(self):
        before=self.snapshot()
        for column,value in [('currency','GBP'),('Amount',D('999')),('account_number','wrong')]:
            f=self.preview();f.loc[0,column]=value
            with self.subTest(column=column),self.assertRaises(ValueError):self.save(f)
            self.assertEqual(before,self.snapshot())
        f=self.preview();f.attrs['revolut_document_hash']='0'*64
        with self.assertRaises(ValueError):self.save(f)
        self.assertEqual(before,self.snapshot())

    def test_single_balance_path_rejected(self):
        with self.assertRaisesRegex(ValueError,'independent wallet'):
            parsing.extract_statement_balance(BytesIO(self.raw),'source.pdf')

    def test_existing_fx_lookup_and_missing_rate(self):
        f=self.preview()
        for r in f.itertuples():
            expected=D('1.12') if r.currency=='EUR' else D('1')
            self.assertEqual(D(str(r.fx_rate)),expected)
            self.assertEqual(D(str(r.amount_usd)),(r.Amount*expected).quantize(D('0.01')))
        with closing(db.get_connection()) as connection, closing(connection.cursor()) as c:
            c.execute("DELETE FROM rates WHERE rate_type='EUR/USD'");connection.commit()
        f=self.preview()
        self.assertTrue(f[f.currency.eq('EUR')].amount_usd.isna().all())
        self.assertTrue(f[f.currency.eq('USD')].fx_rate.eq(D('1')).all())

    def test_real_ui_readonly_preview_has_two_wallets(self):
        import streamlit as st
        from streamlit.testing.v1 import AppTest
        st.cache_data.clear();st.cache_resource.clear()
        self.addCleanup(st.cache_data.clear);self.addCleanup(st.cache_resource.clear)
        before=self.snapshot()
        uploaded=BytesIO(self.raw);uploaded.name='original.pdf'
        app=AppTest.from_file('app.py',default_timeout=90)
        app.session_state['authenticated']=True
        app.session_state['login_user']='Disposable QA'
        app.query_params['page']='Import'
        with patch.object(st,'file_uploader',return_value=uploaded):app.run()
        self.assertFalse(app.exception,[e.message for e in app.exception])
        previews=[e.value for e in app.dataframe if 'Currency' in e.value and 'Transactions' in e.value]
        self.assertEqual(len(previews),1,{'errors':[e.value for e in app.error], 'warnings':[e.value for e in app.warning], 'info':[e.value for e in app.info]})
        self.assertEqual(previews[0].Currency.tolist(),['EUR','USD'])
        self.assertEqual(previews[0].Transactions.tolist(),[115,7])
        self.assertTrue(any(b.label=='Import statement' for b in app.button))
        self.assertEqual(before,self.snapshot())
        button=next(b for b in app.button if b.label=='Import statement')
        with patch.object(st,'file_uploader',return_value=uploaded):button.click().run()
        self.assertFalse(app.exception,[e.message for e in app.exception])
        self.assertFalse(app.error,[e.value for e in app.error])
        with closing(db.get_connection()) as connection, closing(connection.cursor()) as c:
            self.assertEqual(c.execute("SELECT COUNT(*) FROM classified_transactions WHERE statement_hash<>'legacy'").fetchone()[0],122)
            self.assertEqual(c.execute("SELECT COUNT(*) FROM statement_imports WHERE statement_hash<>'legacy'").fetchone()[0],2)
        saved=self.snapshot()
        with patch.object(st,'file_uploader',return_value=uploaded):app.run()
        self.assertEqual(saved,self.snapshot())

if __name__=='__main__':unittest.main()
