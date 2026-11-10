"""Exact current BOC sources and strict zero-interest annex guards, local only."""
from pathlib import Path
from io import BytesIO
from decimal import Decimal
from hashlib import sha256
from contextlib import closing
from types import SimpleNamespace
from unittest.mock import patch
import os,unittest,pdfplumber
import parsing,boc_import,db
import streamlit as st
from import_history import commit_statement
from _qa_post_import_message import CompletionTests

import json
FACTS=json.loads((Path(os.environ['ARETI_TODAY_ROOT'])/'boc-expected-private.json').read_text())

class CurrentBOC(CompletionTests):
 def source(self,key):
  p=Path(os.environ['ARETI_TODAY_SOURCES'])/(key+'.pdf');return p,p.read_bytes()
 def test_both_originals_exact_rows_signs_dates_and_reconciliation(self):
  for key,(number,opening,credits,debits,closing,rows) in FACTS.items():
   with self.subTest(source=key):
    _,data=self.source(key);frame=parsing.parse_pdf(BytesIO(data));b=frame.attrs['statement_balance']
    self.assertEqual([(str(r.Date),Decimal(str(r.Amount))) for r in frame.itertuples()],[(d,Decimal(a)) for d,a in rows])
    self.assertEqual(b['account_number'],number);self.assertEqual(b['bank'],'Bank of Cyprus');self.assertEqual(b['currency'],'USD')
    self.assertEqual((b['period_start'],b['period_end']),('2026-10-01','2026-10-30'))
    for field,value in zip(('opening_balance','money_in','money_out','closing_balance'),(opening,credits,debits,closing)):self.assertEqual(b[field],Decimal(value))
    self.assertEqual(b['opening_balance']+sum(frame.Amount),b['closing_balance'])
 def test_normal_atomic_import_pending_history_duplicate_and_rollback(self):
  for key,(number,*_) in FACTS.items():
   path,data=self.source(key);raw=parsing.parse_pdf(BytesIO(data));balance=raw.attrs['statement_balance']
   account=dict(account_name='Synthetic current BOC',bank='BOC',account_number=number,currency='USD',rate_type='USD/USD')
   mapped=db.apply_account_and_rates(raw,account);digest=sha256(data).hexdigest();before=self.snapshot()
   with patch.object(db,'save_statement_balance',side_effect=RuntimeError('synthetic rollback')):
    with self.assertRaises(RuntimeError):commit_statement(db,mapped,path.name,digest,balance,account)
   self.assertEqual(before,self.snapshot())
   self.assertEqual(commit_statement(db,mapped,path.name,digest,balance,account)[0],14)
   with closing(db.get_connection()) as c:
    self.assertEqual(c.execute('SELECT transaction_count FROM statement_imports WHERE statement_hash=?',(digest,)).fetchone()[0],14)
    self.assertEqual(c.execute('SELECT COUNT(*) FROM classified_transactions WHERE statement_hash=? AND reviewed=0',(digest,)).fetchone()[0],14)
   frozen=self.snapshot();self.assertTrue(commit_statement(db,mapped,path.name,digest,balance,account)[1]);self.assertEqual(frozen,self.snapshot())
 def test_annex_cannot_hide_activity_mismatch_or_nonzero_interest(self):
  for key in FACTS:
   _,data=self.source(key)
   with pdfplumber.open(BytesIO(data)) as doc:
    pages=list(doc.pages);texts=[p.extract_text() or '' for p in pages]
    for bad in (texts[1].replace('0.00 0.00','0.01 0.00'),texts[1]+'\n01/10/2026 01/10/2026 hidden 1.00',texts[1]+'\n1.00',texts[1].replace(FACTS[key][0],'999999999999'),texts[1].replace('Page 2 / 2','Page 1 / 2')):
     with self.assertRaises(boc_import.BOCParseError):boc_import.parse_pages(pages,[texts[0],bad])
    words=pages[1].extract_words()+[dict(text='1.00',top=900,x0=200,x1=230,upright=True)]
    with self.assertRaises(boc_import.BOCParseError):boc_import.parse_pages([pages[0],SimpleNamespace(extract_words=lambda:words)],texts)
    with self.assertRaises(boc_import.BOCParseError):boc_import.parse_document(pages[:-1],texts[:-1])
 def test_both_sources_complete_the_existing_import_page(self):
  for key,(number,*_) in FACTS.items():
   path,data=self.source(key)
   with closing(db.get_connection()) as c:
    c.execute('INSERT INTO account_list(account_name,bank,account_number,currency,rate_type) VALUES(?,?,?,?,?)',('Synthetic BOC UI','Bank of Cyprus',number,'USD','USD/USD'));c.commit()
   st.cache_data.clear();self.file=BytesIO(data);self.file.name=path.name
   before=self.snapshot();app=self.run_app(self.app());self.assertEqual(before,self.snapshot())
   self.import_file(app);self.complete(app,14)
   digest=sha256(data).hexdigest()
   with closing(db.get_connection()) as c:
    self.assertEqual(c.execute('SELECT transaction_count FROM statement_imports WHERE statement_hash=?',(digest,)).fetchone()[0],14)
    self.assertEqual(c.execute('SELECT COUNT(*) FROM classified_transactions WHERE statement_hash=? AND reviewed=0',(digest,)).fetchone()[0],14)
   frozen=self.snapshot();self.run_app(app);self.assertEqual(frozen,self.snapshot())

if __name__=='__main__':
 suite=unittest.TestSuite(CurrentBOC(n) for n in CurrentBOC.__dict__ if n.startswith('test_'))
 raise SystemExit(not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful())
