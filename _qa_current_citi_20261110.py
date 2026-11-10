"""Original current Citi source; source-labelled refund and atomic guards."""
from pathlib import Path
from io import BytesIO
from decimal import Decimal
from hashlib import sha256
from contextlib import closing
from unittest.mock import patch
import os,unittest,pdfplumber
import parsing,db,citi_refund
import streamlit as st
from _qa_post_import_message import CompletionTests
from import_history import commit_statement

class CurrentCiti(CompletionTests):
 def parsed(self):
  p=Path(os.environ['ARETI_TODAY_SOURCES'])/'citi-october.pdf';data=p.read_bytes();return p,data,parsing.parse_pdf(BytesIO(data))
 def test_exact_original_signs_period_and_signed_reconciliation(self):
  _,_,frame=self.parsed();balance=frame.attrs['statement_balance']
  self.assertEqual([(str(r.Date),Decimal(str(r.Amount))) for r in frame.itertuples()],[('2026-10-06',Decimal('536.50')),('2026-10-01',Decimal('-25.00'))])
  self.assertEqual((balance['account_number'],balance['period_start'],balance['period_end']),('5861','2026-09-16','2026-10-15'))
  self.assertEqual(balance['opening_balance'],Decimal('-25.00'));self.assertEqual(balance['closing_balance'],Decimal('486.50'))
  self.assertEqual(balance['opening_balance']+sum(frame.Amount),balance['closing_balance'])
 def test_account_tamper_duplicate_history_and_atomic_rollback(self):
  path,data,frame=self.parsed();b=frame.attrs['statement_balance']
  a=dict(account_name='Synthetic Citi',bank='Citi Bank Credit Card',account_number='TEST-CITI-'+b['account_number'],currency='USD',rate_type='USD/USD')
  mapped=db.apply_account_and_rates(frame,a);h=sha256(data).hexdigest();before=self.snapshot()
  with self.assertRaises(citi_refund.CitiSourceError):commit_statement(db,mapped,path.name,h,b,dict(a,account_number='9999'))
  bad=mapped.copy();bad.loc[0,'Amount']=Decimal('-536.50')
  with self.assertRaises(citi_refund.CitiSourceError):commit_statement(db,bad,path.name,h,b,a)
  with patch.object(db,'save_statement_balance',side_effect=RuntimeError('synthetic rollback')):
   with self.assertRaises(RuntimeError):commit_statement(db,mapped,path.name,h,b,a)
  self.assertEqual(before,self.snapshot())
  self.assertEqual(commit_statement(db,mapped,path.name,h,b,a)[0],2)
  frozen=self.snapshot();self.assertTrue(commit_statement(db,mapped,path.name,h,b,a)[1]);self.assertEqual(frozen,self.snapshot())
 def test_changed_credit_summary_or_hidden_extra_movement_rejected(self):
  _,data,frame=self.parsed()
  with pdfplumber.open(BytesIO(data)) as doc:text='\n'.join(p.extract_text() or '' for p in doc.pages)
  rows=[[str(r.Date),r.Description,r.Amount] for r in frame.itertuples()]
  for bad in (text.replace('Credits -$536.50','Credits -$536.51'),text.replace('New balance -$486.50','New balance -$486.51')):
   with self.assertRaises(citi_refund.CitiSourceError):citi_refund.verified_refund(bad,rows)
  with self.assertRaises(citi_refund.CitiSourceError):citi_refund.verified_refund(text,rows+[['2026-10-01','hidden',Decimal('-1')]])
 def test_original_completes_the_existing_import_page(self):
  path,data,frame=self.parsed();b=frame.attrs['statement_balance']
  with closing(db.get_connection()) as c:
   c.execute('INSERT INTO account_list(account_name,bank,account_number,currency,rate_type) VALUES(?,?,?,?,?)',('Synthetic Citi UI','Citi Bank Credit Card','TEST-CITI-'+b['account_number'],'USD','USD/USD'));c.commit()
  st.cache_data.clear();self.file=BytesIO(data);self.file.name=path.name
  before=self.snapshot();app=self.run_app(self.app());self.assertEqual(before,self.snapshot())
  self.import_file(app);self.complete(app,2)
  with closing(db.get_connection()) as c:
   rows=c.execute('SELECT amount FROM classified_transactions WHERE statement_hash=? AND reviewed=0 ORDER BY amount',(sha256(data).hexdigest(),)).fetchall()
   self.assertEqual([Decimal(str(row[0])) for row in rows],[Decimal('-25'),Decimal('536.5')])
  frozen=self.snapshot();self.run_app(app);self.assertEqual(frozen,self.snapshot())

if __name__=='__main__':
 raise SystemExit(not unittest.TextTestRunner(verbosity=2).run(unittest.TestSuite(CurrentCiti(n) for n in CurrentCiti.__dict__ if n.startswith('test_'))).wasSuccessful())
