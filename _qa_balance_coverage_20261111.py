"""No partial final totals; two source-proven Sapphire periods, local only."""
import copy,importlib.util,json,os,unittest
from collections import Counter
from contextlib import closing
from decimal import Decimal
from hashlib import sha256
from io import BytesIO
from pathlib import Path
from unittest.mock import patch,MagicMock
import db,parsing,pdfplumber,sapphire_balance_metadata as sapphire
from balance_report_sections import grand_total,totals
from latest_import_balances import COLUMNS,snapshot,print_document,workbook_bytes
from latest_balances_compact import compact_html
from _qa_post_import_message import CompletionTests
ROOT=Path(os.environ['ARETI_BALANCE_UAT_ROOT']);OUT=Path(os.environ['ARETI_COVERAGE_ROOT'])

class TotalCoverageTests(unittest.TestCase):
 def row(self,bank,amount):
  r={c:'' for c in COLUMNS};r.update({'Bank':bank,'Account number':'Synthetic','Account name':'Synthetic','Currency':'USD','Status':'IMPORTED','Verification':'LIABILITY CONVENTION UNVERIFIED' if bank=='AMEX' else 'SOURCE RECONCILIATION NOT VERIFIED','Closing balance':Decimal(amount),'Closing balance converted to USD':Decimal(amount)})
  return r
 def test_complete_signed_values_once_and_credit_balance_not_forced_negative(self):
  rows=[self.row('CNB','100'),self.row('AMEX','-20')];frozen=copy.deepcopy(rows)
  self.assertEqual(grand_total(rows),Decimal('80'));self.assertEqual(rows,frozen)
  rows[-1]=self.row('AMEX','20');self.assertEqual(grand_total(rows),Decimal('120'))
  rows=[self.row('CNB','0'),self.row('AMEX','0')];self.assertEqual(grand_total(rows),Decimal('0'))
 def test_unknown_missing_fx_ambiguous_and_empty_coverage_never_finalized(self):
  for field,value in [('Status','NO IMPORT'),('Closing balance converted to USD','NOT AVAILABLE'),('Verification','AMBIGUOUS SETUP IDENTITY')]:
   rows=[self.row('CNB','100'),self.row('AMEX','-20')];rows[-1][field]=value
   self.assertIsNone(grand_total(rows))
  self.assertIsNone(grand_total([]))
 def test_actual_report_blocks_final_total_without_altering_rows_or_section_sums(self):
  spec=importlib.util.spec_from_file_location('private_fixture',ROOT/'report_fixture.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);m.create()
  rows,total,warnings=snapshot(db);expected=json.loads((ROOT/'report-after-private.json').read_text())
  normalized=json.loads(json.dumps(rows,default=str));self.assertEqual(normalized,expected['rows'])
  self.assertEqual({k:str(v) for k,v in totals(rows).items()},expected['totals']);self.assertIsNone(grand_total(rows))
  missing=[r for r in rows if r['Status']=='NO IMPORT'];self.assertEqual(len(missing),5)
  full=print_document(rows,total,warnings);compact=compact_html(rows)
  for html in (full,compact):
   self.assertIn('Grand Total withheld',html);self.assertIn('NOT AVAILABLE',html)
   self.assertNotIn(format(Decimal(expected['grand']),',.2f'),html)
  from openpyxl import load_workbook
  book=load_workbook(BytesIO(workbook_bytes(rows,total,warnings)))
  self.assertEqual(book['USD Totals']['B4'].value,'NOT AVAILABLE')
  (OUT/'coverage-result.json').write_text(json.dumps({'rows':43,'missing_current_setup_rows':5,'grand_total':None,'native_usd_dates_rows_section_totals_unchanged':True},indent=2))
  (OUT/'report-withheld-full.html').write_text(full,encoding='utf-8');(OUT/'report-withheld-compact.html').write_text('<html><head><meta charset="utf-8"></head><body>'+compact+'</body></html>',encoding='utf-8')

class SapphirePeriodTests(CompletionTests):
 def source(self,month):
  p=ROOT/'sapphire-september-original.pdf' if month=='September' else Path(r'C:/Users/Student/Downloads/20261027-statements-9513-.pdf')
  data=p.read_bytes();b=sapphire.source(data);frame=parsing.parse_pdf(BytesIO(data))
  a=dict(account_name='Timur Bekmambetov',bank='Sapphire Chase',account_number=b['account_number'],currency='USD',rate_type='USD/USD')
  return p,data,b,frame,a
 def seed(self,month,missing=False):
  p,data,b,frame,a=self.source(month);digest=sha256(data).hexdigest()
  with closing(db.get_connection()) as c:
   existing=c.execute('SELECT id FROM account_list WHERE account_name=? AND bank=? AND account_number=?',(a['account_name'],a['bank'],a['account_number'])).fetchone()
   if existing is None:c.execute('INSERT INTO account_list(account_name,bank,account_number,currency,rate_type) VALUES(?,?,?,?,?)',tuple(a.values()))
   c.commit()
  from import_history import commit_statement
  commit_statement(db,db.apply_account_and_rates(frame,a),p.name,digest,parsing.extract_statement_balance(BytesIO(data),p.name),a)
  if missing:
   with closing(db.get_connection()) as c:c.execute('DELETE FROM statement_balances WHERE statement_hash=?',(digest,));c.commit()
  return p,data,b,frame,a
 def test_two_originals_source_summary_signed_reconciliation_and_september_stored_rows(self):
  results=[]
  for month in ('September','October'):
   p,data,b,frame,a=self.seed(month);before=self.snapshot();parsed,proof,matched=sapphire.prepare(db,data,p.name)
   self.assertEqual(parsed.to_dict('records'),frame.to_dict('records'))
   self.assertEqual(-b['opening_balance']+sum(frame.Amount)-b['source_interest'],-b['closing_balance'])
   self.assertEqual(sapphire.record(db,data,p.name,'Synthetic'),'already recorded');self.assertEqual(before,self.snapshot())
   results.append({'month':month,'rows':len(frame),'summary_reconciled':True,'legacy_noop':True})
   if month=='September':
    stored=json.loads((ROOT/'september-stored-private.json').read_text())
    expected=Counter((str(r['txn_date']),Decimal(r['amount']),r['original_description']) for r in stored)
    self.assertEqual(expected,Counter((str(r.Date),Decimal(str(r.Amount)),r.Description) for r in frame.itertuples()))
  (OUT/'two-originals-result.json').write_text(json.dumps(results,indent=2))
 def test_september_missing_metadata_rollback_completion_and_repeat_preserve_transactions_history(self):
  p,data,b,frame,a=self.seed('September',True);before=self.snapshot()
  with patch.object(db,'save_statement_balance',side_effect=RuntimeError('synthetic rollback')):
   with self.assertRaises(RuntimeError):sapphire.record(db,data,p.name,'Synthetic')
  self.assertEqual(before,self.snapshot())
  with closing(db.get_connection()) as c:tx=c.execute('SELECT * FROM classified_transactions ORDER BY id').fetchall();history=c.execute('SELECT * FROM statement_imports ORDER BY id').fetchall()
  self.assertEqual(sapphire.record(db,data,p.name,'Synthetic'),'recorded');frozen=self.snapshot()
  self.assertEqual(sapphire.record(db,data,p.name,'Synthetic'),'already recorded');self.assertEqual(frozen,self.snapshot())
  with closing(db.get_connection()) as c:
   self.assertEqual(tx,c.execute('SELECT * FROM classified_transactions ORDER BY id').fetchall());self.assertEqual(history,c.execute('SELECT * FROM statement_imports ORDER BY id').fetchall())
  self.assertEqual(db.get_import_history().iloc[0].reconciliation_status,'OK')
 def test_positive_interest_missing_total_and_zero_interest_hidden_activity_rejected(self):
  for month in ('September','October'):
   _,data,_,_,_=self.source(month)
   with pdfplumber.open(BytesIO(data)) as doc:texts=[p.extract_text() or '' for p in doc.pages]
   if month=='September':texts[2]+='\n09/27 HIDDEN INTEREST CHARGE 1.00'
   else:texts=[t.replace('TOTAL INTEREST FOR THIS PERIOD','UNVERIFIED INTEREST DETAIL') for t in texts]
   mock=MagicMock();mock.__enter__.return_value.pages=[MagicMock(extract_text=lambda t=t:t) for t in texts]
   with patch.object(pdfplumber,'open',return_value=mock),self.assertRaises(ValueError):sapphire.source(data)

if __name__=='__main__':
 suite=unittest.TestSuite()
 for c in (TotalCoverageTests,SapphirePeriodTests):suite.addTests(c(n) for n in c.__dict__ if n.startswith('test_'))
 r=unittest.TextTestRunner(verbosity=2).run(suite)
 (OUT/'targeted-counts.json').write_text(json.dumps({'tests':r.testsRun,'failures':len(r.failures),'errors':len(r.errors),'skips':len(r.skipped)},indent=2))
 raise SystemExit(not r.wasSuccessful())
