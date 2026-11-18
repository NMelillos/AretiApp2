"""Exact private AMEX pair, future-format and disposable atomic lifecycle checks."""
import csv
from contextlib import closing
from decimal import Decimal as D
from io import BytesIO, StringIO
from unittest.mock import patch
import unittest
import db
import amex_statement as pair
from _qa_tuesday_20261117 import LifecycleTests, ROOT


class PairTests(LifecycleTests):
    # Loader selects only methods defined here; inherited tests are run separately.
    def setUp(self):
        super().setUp()
        self.setup_pair()

    def setup_pair(self):
        self.account('TENGRI INC','AMEX','3715-373275 [append the name on the .csv file]','USD')
        self.pdf=(ROOT/'amex.pdf').read_bytes();self.csv=(ROOT/'amex.csv').read_bytes()

    def change_csv(self, field=None, value=None, index=0, remove=False, extra=False, reverse=False):
        reader=csv.DictReader(StringIO(self.csv.decode('utf-8-sig')));fields=reader.fieldnames;rows=list(reader)
        if field:rows[index][field]=value
        if remove:rows.pop()
        if extra:rows.append(dict(rows[0]))
        if reverse:rows.reverse()
        out=StringIO(newline='');w=csv.DictWriter(out,fieldnames=fields);w.writeheader();w.writerows(rows)
        return out.getvalue().encode('utf-8')

    def prepared(self):return pair.prepare(db,self.pdf,self.csv)
    def commit(self,token=None,pdf=None,csv_bytes=None):
        return pair.record(db,pdf or self.pdf,'November.pdf',csv_bytes or self.csv,'activity.csv','Disposable reviewer',token or self.prepared()[-1])

    def test_exact_source_identity_summaries_and_dates(self):
        before=self.snapshot();s,a,f,identity,token=self.prepared()
        self.assertEqual(len(f),171)
        self.assertEqual((s['holder'],s['company'],s['card_ending']),('TIMUR BEKMAMBETOV','TENGRI INC','5-76007'))
        self.assertEqual(tuple(s[k] for k in pair.metadata.FIELDS[2:]),tuple(map(D,('-17759.34','24666.23','-33399.92','-26493.03'))))
        self.assertEqual((s['period_start'],s['period_end']),('2026-10-13','2026-11-11'))
        self.assertEqual(s['opening_balance']+sum(f.Amount,D(0)),s['closing_balance'])
        self.assertEqual(len(f[f.Date<s['period_start']]),2)
        self.assertEqual(before,self.snapshot())

    def test_atomic_import_pending_history_and_renamed_duplicate(self):
        before=self.snapshot();s,a,f,identity,token=self.prepared()
        self.assertEqual(self.commit(token),'imported');after=self.snapshot()
        self.assertEqual(len(db.get_pending_transactions()),171)
        for table in ('account_list','transaction_memory','category_list','rates'):self.assertEqual(before[table],after[table])
        with closing(db.get_connection()) as conn,closing(conn.cursor()) as c:
            c.execute('SELECT COUNT(*) FROM classified_transactions WHERE reviewed<>0');self.assertEqual(c.fetchone()[0],0)
            c.execute('SELECT transaction_count FROM statement_imports');self.assertEqual(c.fetchall(),[(171,)])
            c.execute('SELECT COUNT(*) FROM statement_balances');self.assertEqual(c.fetchone()[0],1)
        self.assertEqual(pair.record(db,self.pdf,'renamed.pdf',self.csv,'renamed.csv','Reviewer',token),'already imported')
        self.assertEqual(after,self.snapshot())
        from latest_import_balances import snapshot
        report,_,_=snapshot(db)
        shown=next(row for row in report if row['Bank']=='AMEX')
        self.assertEqual(shown['Closing balance'],D('-26493.03'))
        self.assertEqual(shown['Opening balance'],D('-17759.34'))
        self.assertEqual(after,self.snapshot())

    def test_altered_secondary_card_41_rows_rejected(self):
        reader=csv.DictReader(StringIO(self.csv.decode('utf-8-sig')));fields=reader.fieldnames;rows=list(reader);changed=0
        for row in rows:
            if row['Card Member']=='MAX REINER':row['Account #']='99999';changed+=1
        self.assertEqual(changed,41)
        out=StringIO();w=csv.DictWriter(out,fieldnames=fields);w.writeheader();w.writerows(rows)
        before=self.snapshot()
        with self.assertRaisesRegex(ValueError,'member/card'):pair.prepare(db,self.pdf,out.getvalue().encode())
        self.assertEqual(before,self.snapshot())

    def test_changed_missing_extra_and_identity_rejected(self):
        before=self.snapshot()
        for kwargs in (dict(field='Amount',value='140.04'),dict(field='Amount',value='garbage140.03'),dict(field='Date',value='12/30/2026'),
                       dict(field='Card Member',value='WRONG MEMBER'),dict(field='Account #',value='99999'),
                       dict(remove=True),dict(extra=True),dict(field='Description',value='')):
            with self.subTest(kwargs=kwargs),self.assertRaises(ValueError):pair.prepare(db,self.pdf,self.change_csv(**kwargs))
        self.assertEqual(before,self.snapshot())

    def test_reordered_serialization_valid_but_repeat_no_duplicates(self):
        reordered=self.change_csv(reverse=True)
        source,account,f,_,token=pair.prepare(db,self.pdf,reordered)
        self.assertEqual(len(f),171)
        self.commit();before=self.snapshot()
        with self.assertRaises(ValueError):self.commit(token,csv_bytes=reordered)
        self.assertEqual(before,self.snapshot())

    def test_balance_failure_rolls_back_transactions_and_history(self):
        token=self.prepared()[-1];before=self.snapshot()
        with patch.object(db,'save_statement_balance',side_effect=RuntimeError('balance injection')):
            with self.assertRaisesRegex(RuntimeError,'balance injection'):self.commit(token)
        self.assertEqual(before,self.snapshot())
        self.assertEqual(self.commit(token),'imported')

    def test_transaction_failure_rolls_back(self):
        token=self.prepared()[-1];before=self.snapshot()
        with patch.object(db,'save_pending_transactions',side_effect=RuntimeError('transaction injection')):
            with self.assertRaisesRegex(RuntimeError,'transaction injection'):self.commit(token)
        self.assertEqual(before,self.snapshot())

    def test_history_update_failure_rolls_back(self):
        token=self.prepared()[-1]
        with closing(db.get_connection()) as conn,closing(conn.cursor()) as c:
            if db.USING_POSTGRES:
                c.execute("CREATE FUNCTION reject_history() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'history injection'; END $$")
                c.execute('CREATE TRIGGER reject_history BEFORE UPDATE ON statement_imports FOR EACH ROW EXECUTE FUNCTION reject_history()')
            else:c.execute("CREATE TRIGGER reject_history BEFORE UPDATE ON statement_imports BEGIN SELECT RAISE(ABORT, 'history injection'); END")
            conn.commit()
        before=self.snapshot()
        with self.assertRaisesRegex(Exception,'history injection'):self.commit(token)
        self.assertEqual(before,self.snapshot())

    def test_setup_and_changed_upload_invalidate_preview(self):
        token=self.prepared()[-1];before=self.snapshot()
        with self.assertRaisesRegex(ValueError,'changed'):self.commit(token,csv_bytes=self.change_csv(reverse=True))
        with self.assertRaisesRegex(ValueError,'changed'):self.commit(token,pdf=self.pdf+b'\n')
        self.assertEqual(before,self.snapshot())
        with closing(db.get_connection()) as conn,closing(conn.cursor()) as c:
            c.execute("UPDATE account_list SET account_name='Wrong company' WHERE bank='AMEX'");conn.commit()
        before=self.snapshot()
        with self.assertRaises(ValueError):self.commit(token)
        self.assertEqual(before,self.snapshot())

    def test_partial_history_fails_closed(self):
        token=self.prepared()[-1]
        with closing(db.get_connection()) as conn,closing(conn.cursor()) as c:
            c.execute('INSERT INTO statement_imports(statement_hash,statement_name,transaction_count) VALUES(?,?,0)',(pair.sha256(self.csv).hexdigest(),'partial.csv'));conn.commit()
        before=self.snapshot()
        with self.assertRaisesRegex(ValueError,'partial'):self.commit(token)
        self.assertEqual(before,self.snapshot())

    def test_classification_failure_no_write(self):
        before=self.snapshot()
        with patch('classification.classify_transactions',side_effect=RuntimeError('prediction injection')):
            with self.assertRaisesRegex(RuntimeError,'prediction injection'):self.prepared()
        self.assertEqual(before,self.snapshot())

    def test_future_month_format_not_hardcoded(self):
        text='''Business Platinum Card
Screen Reader Optimized version
Prepared for
TIMUR BEKMAMBETOV
TENGRI INC
Account Ending 5-76007
Closing Date 12/11/26
www.americanexpress.com
New Balance $112.00
Days in Billing Period: 30
Account Total
Previous Balance $100.00
Less Payments/Credits $0.00
Plus New Charges $12.00
Plus Fees $0.00
Plus Interest Charged $0.00
Equals New Balance $112.00
Pay Over Time Limit $1000.00
Payments $0.00 $0.00 $0.00
Credits TIMUR BEKMAMBETOV 5-76007 $0.00 $0.00 $0.00
New Charges TIMUR BEKMAMBETOV 5-76007 $0.00 $12.00 $12.00
Payments Details
*Indicates posting date
TIMUR BEKMAMBETOV
Card Ending 5-76007
New Charges Details
TIMUR BEKMAMBETOV
Card Ending 5-76007
12/05/26 Swimming lessons Pay Over Time $12.00
Fees
'''
        raw=b'Date,Description,Amount,Card Member,Account #\n12/05/2026,Swimming lessons,12.00,TIMUR BEKMAMBETOV,-76007\n'
        with patch.object(pair.metadata,'texts',return_value=[text]):
            s,a,f,i,token=pair.prepare(db,b'future source fixture',raw)
            self.assertEqual(len(f),1);self.assertEqual(s['closing_balance'],D('-112'))
            self.assertEqual(f.iloc[0].suggested_subcategory,'Swimming')
            self.assertEqual(pair.record(db,b'future source fixture','future.pdf',raw,'future.csv','Reviewer',token),'imported')

    def test_real_streamlit_new_import_and_duplicate(self):
        import streamlit as st
        from streamlit.testing.v1 import AppTest
        pdf=BytesIO(self.pdf);pdf.name='2026-11-11.pdf';csv_file=BytesIO(self.csv);csv_file.name='activity.csv'
        st.cache_data.clear();st.cache_resource.clear()
        self.addCleanup(st.cache_data.clear);self.addCleanup(st.cache_resource.clear)
        app=AppTest.from_file('app.py',default_timeout=60)
        app.session_state['authenticated']=True;app.session_state['login_user']='Disposable reviewer';app.query_params['page']='Import'
        uploader=lambda label,**kw:pdf if label=='Upload statement' else csv_file
        before=self.snapshot()
        with patch.object(st,'file_uploader',side_effect=uploader):app.run()
        self.assertFalse(app.exception);self.assertFalse(app.error,[e.value for e in app.error])
        rendered=' '.join(x.value.to_string() for x in app.table)
        for value in ('171','-26493.03','-17759.34','24666.23','-33399.92','5-76007'):self.assertIn(value,rendered)
        self.assertEqual(before,self.snapshot())
        self.assertTrue(next(b for b in app.button if b.label=='Import AMEX statement').disabled)
        with patch.object(st,'file_uploader',side_effect=uploader):
            next(c for c in app.checkbox if c.label=='Confirm new AMEX statement import').check().run()
            next(b for b in app.button if b.label=='Import AMEX statement').click().run()
        self.assertFalse(app.exception);self.assertFalse(app.error,[e.value for e in app.error])
        self.assertEqual(len(db.get_pending_transactions()),171);after=self.snapshot()
        with patch.object(st,'file_uploader',side_effect=uploader):next(b for b in app.button if b.label=='Import AMEX statement').click().run()
        self.assertFalse(app.error,[e.value for e in app.error]);self.assertEqual(after,self.snapshot())


if __name__=='__main__':
    result=unittest.TextTestRunner(verbosity=2).run(unittest.TestSuite(PairTests(n) for n in PairTests.__dict__ if n.startswith('test_')))
    raise SystemExit(not result.wasSuccessful())
