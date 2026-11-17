"""Tuesday exact-source and suggestion safety checks; disposable databases only."""
from contextlib import closing
from decimal import Decimal as D
from io import BytesIO
from pathlib import Path
from unittest.mock import patch
import os, tempfile, unittest
import pandas as pd
import db, parsing, classification
from import_history import commit_statement

ROOT=Path(os.environ.get('ARETI_TUESDAY_ROOT',Path(__file__).resolve().parent.parent/'qa-tuesday-20261117'))


class ClassificationTests(unittest.TestCase):
    def classify(self,text,memory=(),taxonomy=None):
        taxonomy=taxonomy if taxonomy is not None else [('Utilities','Gas'),('Utilities','Water'),('Children','Swimming'),('Other','Other'),('UNIDENTIFIED EXPENSES','')]
        with patch.object(classification,'get_categories',return_value=pd.DataFrame(taxonomy,columns=['category','subcategory'])):
            return classification.classify_transactions(pd.DataFrame([dict(Description=text,Amount=D('-10'))]),pd.DataFrame(memory),taxonomy=pd.DataFrame(taxonomy,columns=['category','subcategory'])).iloc[0]

    def memory(self,text,category='Utilities',subcategory='Gas'):
        return dict(normalized_description=text,original_description=text,category=category,subcategory=subcategory,times_seen=1)

    def test_distinctive_merchant_tokens(self):
        for token,sub in [('SOCALGAS','Gas'),('LADWP','Water'),('DISTINCTMERCHANT','Gas')]:
            with self.subTest(token=token):
                row=self.classify('AUTOPAY '+token+' WEB PMT 00987',[self.memory(token+' PAYMENT LOS ANGELES 123456',subcategory=sub)])
                self.assertEqual((row.suggested_category,row.suggested_subcategory),('Utilities',sub))

    def test_ambiguous_and_generic_history(self):
        for text,memory in [('DISTINCTMERCHANT ONLINE',[self.memory('DISTINCTMERCHANT'),self.memory('DISTINCTMERCHANT','Other','Other')]),('ONLINE PAYMENT',[self.memory('ONLINE PAYMENT SERVICE')])]:
            self.assertEqual(self.classify(text,memory).suggested_category,'UNIDENTIFIED EXPENSES')

    def test_taxonomy_without_memory(self):
        for text in ('Swimming lessons October','SWIMMING lessons','(swimming): lessons'):
            row=self.classify(text)
            self.assertEqual((row.suggested_category,row.suggested_subcategory),('Children','Swimming'))

    def test_taxonomy_ambiguity_and_boundary(self):
        for text,tax in [('swimming',[('Children','Swimming'),('Other','Swimming'),('UNIDENTIFIED EXPENSES','')]),('SCARLET',[('Other','CAR'),('UNIDENTIFIED EXPENSES','')])]:
            self.assertEqual(self.classify(text,taxonomy=tax).suggested_category,'UNIDENTIFIED EXPENSES')

    def test_exact_wins_and_input_unchanged(self):
        row=self.classify('Swimming',[self.memory('SWIMMING','Other','Other')])
        self.assertEqual(row.suggested_category,'Other');self.assertEqual(row.match_type,'exact')


class SourceTests(unittest.TestCase):
    def test_bprojects_exact_summary(self):
        f=parsing.parse_pdf(BytesIO((ROOT/'bprojects.pdf').read_bytes()));b=f.attrs['statement_balance']
        self.assertEqual(len(f),7);self.assertEqual(b['account_number'],'LT503250070356815945')
        self.assertEqual(b['account_name'],'B-PROJECTS LIMITED');self.assertEqual(b['currency'],'EUR')
        self.assertEqual((b['period_start'],b['period_end']),('2026-10-01','2026-10-31'))
        self.assertEqual(tuple(b[k] for k in ('opening_balance','money_in','money_out','closing_balance')),tuple(map(D,('997.23','22','282.09','737.14'))))
        self.assertEqual(b['opening_balance']+sum(f.Amount,D(0)),b['closing_balance'])

    def test_citi_exact_summary(self):
        f=parsing.parse_pdf(BytesIO((ROOT/'citi.pdf').read_bytes()));b=f.attrs['statement_balance']
        self.assertEqual(len(f),1);self.assertEqual(f.Amount.tolist(),[D('-25')])
        self.assertEqual(tuple(b[k] for k in ('opening_balance','money_in','money_out','closing_balance')),tuple(map(D,('486.50','0','25','461.50'))))
        self.assertEqual((b['period_start'],b['period_end']),('2026-10-16','2026-11-16'))
        self.assertEqual(b['opening_balance']+sum(f.Amount,D(0)),b['closing_balance'])

    def test_citi_debt_and_zero_crossing(self):
        from citi_card_summary import verified
        text=(ROOT/'citi.txt').read_text(encoding='utf-8')
        rows=[['2026-11-01','Purchase',D('-25')]]
        for old,new,opening,closing in [('-$486.50','-$461.50','486.50','461.50'),('$100.00','$125.00','-100','-125'),('-$10.00','$15.00','10','-15')]:
            source=text.replace('-$486.50',old).replace('-$461.50',new)
            _,b=verified(source,rows)
            self.assertEqual((b['opening_balance'],b['closing_balance']),(D(opening),D(closing)))

    def test_fifth_native_unchanged(self):
        from fifth_third_scanned import parse_ocr
        with patch('fifth_third_scanned.parse_ocr',side_effect=AssertionError('Native source entered OCR')):
            f=parsing.parse_pdf(BytesIO((ROOT.parent/'qa-fifth-native-20261116/#5055.pdf').read_bytes()))
        b=f.attrs['statement_balance'];self.assertEqual(len(f),2)
        self.assertEqual(b['account_number'],'1892775055')
        self.assertEqual((b['opening_balance'],b['closing_balance']),(D('40575.60'),D('9564.60')))
        self.assertEqual(f.Amount.tolist(),[D('-31000'),D('-11')])


class LifecycleTests(unittest.TestCase):
    def setUp(self):
        self.assertFalse(db.USING_POSTGRES)
        temp=tempfile.TemporaryDirectory(dir=os.environ['TEMP']);self.addCleanup(temp.cleanup)
        p=patch.object(db,'DB_PATH',str(Path(temp.name)/'tuesday.sqlite'));p.start();self.addCleanup(p.stop)
        db.init_db()
        self.seed()

    def seed(self):
        with closing(db.get_connection()) as connection, closing(connection.cursor()) as c:
            for cat,sub in [('Transport','Taxi'),('Children','Swimming'),('UNIDENTIFIED EXPENSES','')]:
                c.execute('INSERT INTO category_list(category,subcategory) VALUES(?,?)',(cat,sub))
            c.execute("INSERT INTO account_list(account_name,bank,account_number,currency,rate_type) VALUES('Natalia Fishman','Revolut','LT453250028012133298','','')")
            c.execute("INSERT INTO rates(rate_month,rate_type,rate_value) VALUES('2026-10-01','EUR/USD',1.12)")
            c.execute("INSERT INTO transaction_memory(original_description,normalized_description,category,subcategory,times_seen) VALUES('Uber','UBER','Transport','Taxi',3)")
            connection.commit()

    def snapshot(self):
        with closing(db.get_connection()) as connection, closing(connection.cursor()) as c:
            return {t:c.execute('SELECT * FROM '+t+' ORDER BY id').fetchall() for t in ('account_list','transaction_memory','category_list','rates','classified_transactions','statement_imports','statement_balances')}

    def natalia(self):
        raw=(Path(os.environ['ARETI_GREEK_SOURCE_ROOT'])/'Natalia Revolut 1.10.2026-31.10.2026.pdf').read_bytes()
        f=parsing.parse_pdf(BytesIO(raw));f=db.apply_account_and_rates(f,{})
        return classification.classify_transactions(f,db.get_memory(),taxonomy=db.get_categories(include_subcategories=True))

    def test_natalia_suggestions_persist_manual_review(self):
        before=self.snapshot();f=self.natalia()
        self.assertEqual(len(f),122);self.assertEqual(f.groupby('currency').size().to_dict(),{'EUR':115,'USD':7})
        uber=f[f.Description.str.startswith('Uber')];self.assertGreater(len(uber),0)
        self.assertTrue(uber.suggested_category.eq('Transport').all())
        self.assertEqual(commit_statement(db,f,'Natalia.pdf',f.attrs['revolut_document_hash'],{}, {})[0],122)
        pending=db.get_pending_transactions();self.assertEqual(len(pending),122)
        self.assertTrue(pending[pending.original_description.str.startswith('Uber')].suggested_category.eq('Transport').all())
        after=self.snapshot()
        for t in ('account_list','transaction_memory','category_list','rates'):self.assertEqual(before[t],after[t])
        with closing(db.get_connection()) as connection, closing(connection.cursor()) as c:self.assertEqual(c.execute('SELECT COUNT(*) FROM classified_transactions WHERE reviewed=1').fetchone()[0],0)
        saved=self.snapshot();self.assertTrue(commit_statement(db,f,'renamed.pdf',f.attrs['revolut_document_hash'],{}, {})[1]);self.assertEqual(saved,self.snapshot())

    def test_natalia_failure_atomic(self):
        f=self.natalia();before=self.snapshot()
        with patch.object(db,'save_statement_balance',side_effect=RuntimeError('injected')):
            with self.assertRaisesRegex(RuntimeError,'injected'):commit_statement(db,f,'Natalia.pdf',f.attrs['revolut_document_hash'],{}, {})
        self.assertEqual(before,self.snapshot())

    def account(self,name,bank,number,currency):
        with closing(db.get_connection()) as connection, closing(connection.cursor()) as c:
            c.execute('INSERT INTO account_list(account_name,bank,account_number,currency,rate_type) VALUES(?,?,?,?,?)',(name,bank,number,currency,currency+'/USD'));connection.commit()
        return next(a for a in db.get_accounts().to_dict('records') if a['account_number']==number)

    def current(self,name):
        if name=='bprojects':a=self.account('B-PROJECTS LIMITED','Revolut','LT503250070356815945','EUR')
        else:a=self.account('Timur','Citi Bank Credit Card','5861','USD')
        raw=(ROOT/(name+'.pdf')).read_bytes();f=parsing.parse_pdf(BytesIO(raw));b=f.attrs['statement_balance']
        if name=='bprojects':
            from revolut_account_context import effective_account
            a=effective_account(f,b,a)
        return db.mark_duplicate_transactions(db.apply_account_and_rates(f,a)),b,a,db.build_statement_hash(raw)

    def test_business_lifecycle_rollback_duplicate(self):
        f,b,a,h=self.current('bprojects');before=self.snapshot()
        with patch.object(db,'save_statement_balance',side_effect=RuntimeError('injected')):
            with self.assertRaisesRegex(RuntimeError,'injected'):commit_statement(db,f,'source.pdf',h,b,a)
        self.assertEqual(before,self.snapshot())
        self.assertEqual(commit_statement(db,f,'source.pdf',h,b,a)[0],7)
        saved=self.snapshot();self.assertTrue(commit_statement(db,f,'rename.pdf',h,b,a)[1]);self.assertEqual(saved,self.snapshot())

    def test_citi_continuity_missing_matching_conflicting_and_identity(self):
        from citi_card_summary import validate,CitiSourceError
        f,b,a,h=self.current('citi')
        with closing(db.get_connection()) as connection, closing(connection.cursor()) as c:
            validate(f,b,a,connection)
            c.execute('INSERT INTO statement_balances(statement_hash,account_name,bank,account_number,currency,period_end,closing_balance) VALUES(?,?,?,?,?,?,?)',('prior',a['account_name'],a['bank'],a['account_number'],'USD','2026-10-15','486.50'));connection.commit()
            validate(f,b,a,connection)
        before=self.snapshot()
        with patch.object(db,'save_statement_balance',side_effect=RuntimeError('injected')):
            with self.assertRaisesRegex(RuntimeError,'injected'):commit_statement(db,f,'citi.pdf',h,b,a)
        self.assertEqual(before,self.snapshot())
        with closing(db.get_connection()) as connection, closing(connection.cursor()) as c:
            c.execute("UPDATE statement_balances SET closing_balance=1 WHERE statement_hash='prior'");connection.commit()
        before=self.snapshot()
        with self.assertRaisesRegex(CitiSourceError,'conflicts'):commit_statement(db,f,'citi.pdf',h,b,a)
        self.assertEqual(before,self.snapshot())
        with closing(db.get_connection()) as connection, closing(connection.cursor()) as c:
            c.execute("UPDATE statement_balances SET closing_balance=486.50 WHERE statement_hash='prior'")
            c.execute('UPDATE account_list SET account_number=? WHERE id=?',('wrong',a['id']));connection.commit()
        before=self.snapshot()
        with self.assertRaisesRegex(CitiSourceError,'Setup identity'):commit_statement(db,f,'citi.pdf',h,b,a)
        self.assertEqual(before,self.snapshot())

    def ui(self,raw,name):
        import streamlit as st
        from streamlit.testing.v1 import AppTest
        st.cache_data.clear();st.cache_resource.clear()
        self.addCleanup(st.cache_data.clear);self.addCleanup(st.cache_resource.clear)
        uploaded=BytesIO(raw);uploaded.name=name
        app=AppTest.from_file('app.py',default_timeout=45)
        app.session_state['authenticated']=True;app.session_state['login_user']='Disposable reviewer';app.query_params['page']='Import'
        with patch.object(st,'file_uploader',return_value=uploaded):app.run()
        self.assertFalse(app.exception,[e.message for e in app.exception])
        self.assertFalse(app.error,[e.value for e in app.error])
        return app,uploaded

    def test_ui_business_and_citi_actual_summary(self):
        for name,expected in [('bprojects',('EUR 997.23','EUR 22.00','EUR 282.09','EUR 737.14')),('citi',('USD 486.50','USD 0.00','USD 25.00','USD 461.50'))]:
            self.current(name);before=self.snapshot()
            app,_=self.ui((ROOT/(name+'.pdf')).read_bytes(),name+'.pdf')
            rendered='\n'.join(str(x.value) for x in app.markdown)
            for value in expected:self.assertIn(value,rendered)
            self.assertEqual(before,self.snapshot())

    def test_ui_native_fifth(self):
        self.account('TENGRI INC MANAGEMENT ACCOUNT','Fifth Third Bank','1892775055','USD')
        before=self.snapshot()
        app,_=self.ui((ROOT.parent/'qa-fifth-native-20261116/#5055.pdf').read_bytes(),'#5055.pdf')
        rendered='\n'.join(str(x.value) for x in app.markdown)
        self.assertIn('40,575.60',rendered);self.assertIn('9,564.60',rendered)
        self.assertEqual(before,self.snapshot())

    def test_ui_natalia_prediction_commit(self):
        import streamlit as st
        raw=(Path(os.environ['ARETI_GREEK_SOURCE_ROOT'])/'Natalia Revolut 1.10.2026-31.10.2026.pdf').read_bytes()
        app,uploaded=self.ui(raw,'Natalia.pdf')
        frames=[x.value for x in app.dataframe if 'suggested_category' in x.value]
        self.assertTrue(frames,'No classification preview rendered')
        self.assertTrue(any(f.suggested_category.eq('Transport').any() for f in frames))
        button=next(b for b in app.button if b.label=='Import statement')
        with patch.object(st,'file_uploader',return_value=uploaded):button.click().run()
        self.assertFalse(app.exception,[e.message for e in app.exception]);self.assertFalse(app.error,[e.value for e in app.error])
        pending=db.get_pending_transactions();self.assertEqual(len(pending),122)
        self.assertTrue(pending[pending.original_description.str.startswith('Uber')].suggested_category.eq('Transport').all())

    def test_ui_amex_metadata_preview_readonly(self):
        import streamlit as st
        from streamlit.testing.v1 import AppTest
        a=self.account('TENGRI INC','AMEX','3715-373275 [append the name on the .csv file]','USD')
        raw=(ROOT/'amex.csv').read_bytes()
        frame=db.apply_account_and_rates(parsing.parse_csv(BytesIO(raw)),a)
        commit_statement(db,frame,'original.csv',db.build_statement_hash(raw),{},a)
        before=self.snapshot()
        pdf=BytesIO((ROOT/'amex.pdf').read_bytes());pdf.name='2026-11-11.pdf'
        csv=BytesIO(raw);csv.name='renamed.csv'
        st.cache_data.clear();st.cache_resource.clear()
        self.addCleanup(st.cache_data.clear);self.addCleanup(st.cache_resource.clear)
        app=AppTest.from_file('app.py',default_timeout=45)
        app.session_state['authenticated']=True;app.session_state['login_user']='Disposable reviewer';app.query_params['page']='Import'
        with patch.object(st,'file_uploader',side_effect=lambda label,**kw: pdf if label=='Upload statement' else csv):app.run()
        self.assertFalse(app.exception,[e.message for e in app.exception]);self.assertFalse(app.error,[e.value for e in app.error])
        tables=[x.value for x in app.table]
        self.assertTrue(tables)
        rendered=' '.join(t.to_string() for t in tables)
        for value in ('5-76007','-17759.34','24666.23','-33399.92','-26493.03'):self.assertIn(value,rendered)
        self.assertTrue(next(b for b in app.button if b.label=='Import AMEX Balance Metadata').disabled)
        self.assertEqual(before,self.snapshot())

    def test_amex_exact_csv_metadata_only_and_unsupported_equivalence(self):
        import amex_balance_metadata as amex
        a=self.account('TENGRI INC','AMEX','3715-373275 [append the name on the .csv file]','USD')
        raw=(ROOT/'amex.csv').read_bytes();pdf=(ROOT/'amex.pdf').read_bytes()
        source=amex.parse(pdf)
        self.assertEqual((source['holder'],source['company'],source['card_ending']),('TIMUR BEKMAMBETOV','TENGRI INC','5-76007'))
        self.assertEqual(tuple(source[k] for k in ('opening_balance','money_in','money_out','closing_balance')),tuple(map(D,('-17759.34','24666.23','-33399.92','-26493.03'))))
        before=self.snapshot()
        with self.assertRaisesRegex(ValueError,'completed transaction import'):amex.prepare(db,pdf,raw)
        self.assertEqual(before,self.snapshot())
        frame=db.apply_account_and_rates(parsing.parse_csv(BytesIO(raw)),a)
        self.assertEqual(len(frame),171)
        self.assertEqual(commit_statement(db,frame,'original.csv',db.build_statement_hash(raw),{},a)[0],171)
        _,_,linked,_=amex.prepare(db,pdf,raw);self.assertEqual(linked['id'],a['id'])
        imported=self.snapshot()
        equivalent=raw.replace(b'\r\n',b'\n') if b'\r\n' in raw else raw.replace(b'\n',b'\r\n')
        with self.assertRaisesRegex(ValueError,'completed transaction import'):amex.prepare(db,pdf,equivalent)
        self.assertEqual(imported,self.snapshot())
        self.assertEqual(amex.record(db,pdf,'statement.pdf',raw,'Disposable reviewer'),'recorded')
        after=self.snapshot()
        for t in ('account_list','transaction_memory','category_list','rates','classified_transactions'):self.assertEqual(imported[t],after[t])
        self.assertEqual(amex.record(db,pdf,'renamed.pdf',raw,'Disposable reviewer'),'already recorded');self.assertEqual(after,self.snapshot())

if __name__=='__main__':unittest.main()
