"""Exact source linkage, read-only report delta and fail-closed ambiguity."""
import copy,hashlib,importlib.util,json,os,sqlite3,subprocess,unittest
from contextlib import closing
from decimal import Decimal
from io import BytesIO
from pathlib import Path
from unittest.mock import patch
import db,parsing
import bprojects_report_context as context
import latest_import_balances as report
from balance_report_sections import totals,grand_total

ROOT=Path(os.environ['ARETI_FINAL_FIX_ROOT'])
EVIDENCE=Path(os.environ['ARETI_BALANCE_UAT_ROOT'])
NUMBER='LT50 3250 0703 5681 5945'


class LinkageTests(unittest.TestCase):
 def setUp(self):
  spec=importlib.util.spec_from_file_location('private_fixture',EVIDENCE/'report_fixture.py')
  m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);m.create()
 def frozen(self):
  with sqlite3.connect(db.DB_PATH) as c:
   tables=[r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
   return {t:hashlib.sha256(repr(c.execute('SELECT * FROM '+t+' ORDER BY id').fetchall()).encode()).hexdigest() for t in tables}
 def links(self):
  with sqlite3.connect(db.DB_PATH) as c:
   c.execute('PRAGMA query_only=ON');return context.report_links(c)
 def test_exact_original_source_and_stored_movement_evidence(self):
  data=Path(os.environ['ARETI_REVOLUT_SOURCE']).read_bytes();f=parsing.parse_pdf(BytesIO(data));b=f.attrs['statement_balance']
  self.assertEqual(hashlib.sha256(data).hexdigest(),context.SOURCE_HASH)
  self.assertEqual((len(f),b['currency'],b['closing_balance']),(8,'EUR',Decimal('997.23')))
  self.assertEqual(b['opening_balance']+sum(f.Amount),b['closing_balance'])
  self.assertEqual(self.links(),[(968,'EUR')])
 def test_actual_report_only_target_row_changes_readonly_fx_and_totals(self):
  before=self.frozen();baseline=json.loads((EVIDENCE/'report-after-private.json').read_text());old={r['Account number']:r for r in baseline['rows']}
  original=db.get_connection
  def readonly():
   c=original();c.execute('PRAGMA query_only=ON');return c
  with patch.object(db,'get_connection',side_effect=readonly):rows,total,warnings=report.snapshot(db)
  self.assertEqual(before,self.frozen());self.assertEqual(len(rows),43)
  new={r['Account number']:r for r in rows}
  for n in old:
   if n!=NUMBER:self.assertEqual(json.loads(json.dumps(new[n],default=str)),old[n],n)
  r=new[NUMBER];self.assertEqual(r['Closing balance'],Decimal('997.23'));self.assertEqual(r['Statement end date'],'2026-09-30')
  self.assertEqual(r['Currency'],'EUR');self.assertEqual(r['Status'],'IMPORTED')
  self.assertIsInstance(r['Closing balance converted to USD'],Decimal)
  self.assertTrue(r['Applied FX']);self.assertIsNone(grand_total(rows))
  section_totals=totals(rows)
  self.assertEqual(section_totals['Debts'],Decimal(baseline['totals']['Debts']))
  self.assertEqual(section_totals['Deposits'],Decimal(baseline['totals']['Deposits'])+r['Closing balance converted to USD'])
  self.assertEqual(sum(x['Status']=='NO IMPORT' for x in rows),4)
  (ROOT/'report-result.json').write_text(json.dumps({'rows':43,'only_changed_account':NUMBER,'target':r,'totals':section_totals,'grand_total':grand_total(rows),'missing':4,'read_only_preservation':True},default=str,indent=2))
 def test_second_balance_currency_rejected(self):
  with sqlite3.connect(db.DB_PATH) as c:c.execute("UPDATE statement_balances SET currency='USD' WHERE id=109");c.commit()
  self.assertEqual(self.links(),[])
 def test_second_transaction_currency_rejected(self):
  with sqlite3.connect(db.DB_PATH) as c:c.execute("UPDATE classified_transactions SET currency='USD' WHERE account_number=? AND statement_hash<>?",(NUMBER,context.SOURCE_HASH));c.commit()
  self.assertEqual(self.links(),[])
 def test_duplicate_setup_wrong_owner_and_wrong_number_rejected(self):
  for field,value in [('account_name','Other'),('account_number','LT453250028012133298'),('bank','Other'),('currency','USD')]:
   self.setUp()
   with sqlite3.connect(db.DB_PATH) as c:c.execute('UPDATE account_list SET '+field+'=? WHERE id=968',(value,));c.commit()
   self.assertEqual(self.links(),[],field)
  self.setUp()
  with sqlite3.connect(db.DB_PATH) as c:c.execute('INSERT INTO account_list(id,account_name,bank,account_number,currency,rate_type,created_at) SELECT 9999,account_name,bank,account_number,currency,rate_type,created_at FROM account_list WHERE id=968');c.commit()
  self.assertEqual(self.links(),[])
 def test_source_summary_hash_activity_split_and_history_changes_reject(self):
  statements=[("UPDATE statement_balances SET closing_balance='998.23' WHERE id=216",()),
   ("UPDATE statement_balances SET statement_hash='different' WHERE id=216",()),
   ("UPDATE classified_transactions SET amount='0' WHERE statement_hash=?",(context.SOURCE_HASH,)),
   ("UPDATE classified_transactions SET split_parent_id=123 WHERE statement_hash=?",(context.SOURCE_HASH,)),
   ("UPDATE statement_imports SET transaction_count=9 WHERE statement_hash=?",(context.SOURCE_HASH,))]
  for sql,args in statements:
   self.setUp()
   with sqlite3.connect(db.DB_PATH) as c:c.execute(sql,args);c.commit()
   self.assertEqual(self.links(),[],sql)
 def test_readonly_repeats_preserve_all_tables_and_timestamps(self):
  before=self.frozen();self.assertEqual(self.links(),self.links());self.assertEqual(before,self.frozen())


if __name__=='__main__':
 result=unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(LinkageTests))
 (ROOT/'targeted-counts.json').write_text(json.dumps({'tests':result.testsRun,'failures':len(result.failures),'errors':len(result.errors),'skips':len(result.skipped)},indent=2))
 raise SystemExit(not result.wasSuccessful())
