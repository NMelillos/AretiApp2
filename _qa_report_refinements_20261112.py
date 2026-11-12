"""Report-only refinements; source snapshot and disposable DB remain unchanged."""
import copy,json,os,re,subprocess,types,unittest
from contextlib import closing
from datetime import datetime
from decimal import Decimal
from pathlib import Path
import db
import latest_import_balances as report
from balance_report_sections import grand_total,totals,partition
from _qa_post_import_message import CompletionTests
from _qa_balance_sapphire_20261111 import fixture

class ReportRefinements(CompletionTests):
 def test_report_data_exports_and_database_unchanged(self):
  fixture()
  with closing(db.get_connection()) as c:before_db='\n'.join(c.iterdump())
  base=types.ModuleType('baseline_report')
  exec(subprocess.check_output(['git','show','d2d2c82e0396904e087cb4c60d2e1ea3dd54929a:latest_import_balances.py'],text=True,encoding='utf-8'),base.__dict__)
  prior=base.snapshot(db);current=report.snapshot(db)
  self.assertEqual(prior,current)
  self.assertEqual(totals(prior[0]),totals(current[0]));self.assertEqual(partition(prior[0]),partition(current[0]))
  when=datetime.fromisoformat('2026-11-12T10:00:00+02:00')
  before=base.print_document(*prior,generated_at=when);after=report.print_document(*current,generated_at=when)
  self.assertEqual(before,after)
  # Export implementation is unchanged, including its exact date source.
  import ast,inspect
  source=subprocess.check_output(['git','show','d2d2c82e0396904e087cb4c60d2e1ea3dd54929a:latest_import_balances.py'],text=True,encoding='utf-8')
  old=next(n for n in ast.parse(source).body if isinstance(n,ast.FunctionDef) and n.name=='workbook_bytes')
  self.assertEqual(ast.dump(old),ast.dump(ast.parse(inspect.getsource(report.workbook_bytes)).body[0]))
  screen=report.screen_document(after)
  for raw,shown in zip(re.findall(r'<tbody>(.*?)</tbody>',after,re.S),re.findall(r'<tbody>(.*?)</tbody>',screen,re.S)):
   for oldrow,newrow in zip(re.findall(r'<tr>(.*?)</tr>',raw,re.S),re.findall(r'<tr>(.*?)</tr>',shown,re.S)):
    cells=re.findall(r'<td\b[^>]*>.*?</td>',oldrow,re.S);cells.insert(4,cells.pop(15))
    self.assertEqual(''.join(cells),newrow)
  self.assertIn('Grand Total: NOT AVAILABLE',screen)
  self.assertIn('Balances Import<br>Date',screen)
  root=Path(os.environ['ARETI_FINAL_FIX_ROOT'])
  (root/'before.html').write_text(base.screen_document(before),encoding='utf-8')
  (root/'after.html').write_text(screen,encoding='utf-8')
  (root/'financial-comparison.json').write_text(json.dumps({'rows':len(current[0]),'totals':totals(current[0]),'grand':grand_total(current[0]),'equal':prior==current},default=str))
  with closing(db.get_connection()) as c:self.assertEqual(before_db,'\n'.join(c.iterdump()))

 def test_complete_grand_total_and_missing_coverage_guard(self):
  rows=[]
  for bank,value in [('Test bank','100'),('AMEX','-25')]:
   row={key:'' for key in report.COLUMNS}
   row.update({'Bank':bank,'Currency':'USD','Status':'IMPORTED','Verification':'SOURCE RECONCILIATION NOT VERIFIED','Closing balance converted to USD':Decimal(value)})
   rows.append(row)
  self.assertEqual(grand_total(rows),Decimal('75'))
  html=report.screen_document(report.print_document(rows,Decimal('100'),[]))
  self.assertIn('Grand Total: 75.00 USD',html)
  rows.append(dict(rows[0],Status='NO IMPORT'))
  self.assertIsNone(grand_total(rows))
  self.assertIn('Grand Total: NOT AVAILABLE',report.screen_document(report.print_document(rows,Decimal('100'),[])))

if __name__=='__main__':
 r=unittest.TextTestRunner(verbosity=2).run(unittest.TestSuite(ReportRefinements(n) for n in ReportRefinements.__dict__ if n.startswith('test_')))
 raise SystemExit(not r.wasSuccessful())
