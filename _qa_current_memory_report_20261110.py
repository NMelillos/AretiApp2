"""Source-backed suggestion-only and exact report presentation regressions."""
import ast,copy,importlib.util,json,os,re,unittest
from pathlib import Path
from decimal import Decimal
from unittest.mock import patch
import pandas as pd
import classification,db
from utils import normalize_description,extract_beneficiary,infer_transaction_type
from latest_import_balances import COLUMNS,snapshot,print_document,screen_document,workbook_bytes
from latest_balances_compact import compact_html
from balance_report_sections import section,totals

class MemoryTests(unittest.TestCase):
 def test_all_eight_examples_and_history_frames_are_unchanged(self):
  state=json.loads((Path(os.environ['ARETI_TODAY_ROOT'])/'memory-private.json').read_text(encoding='utf-8-sig'))
  memory=pd.DataFrame(state['memory']);before=memory.copy(deep=True)
  names=['REPLIT monthly subscription','swimming','pharmacy','Andreas Poupas','MYTHERESA online purchase','08/10/2026 TIPS OUT Transfer Fees 13608908 4.00 | Continue on next Page','1Bank - Credit Advice Transfer from shareholder','Little Kickers football club']
  rows=[]
  for text in names:
   amount=100 if 'shareholder' in text else -4
   rows.append(dict(Description=text,normalized_description=normalize_description(text),beneficiary=extract_beneficiary(text),transaction_type=infer_transaction_type(text,amount),Amount=amount))
  frame=pd.DataFrame(rows);original=frame.copy(deep=True)
  categories=sorted({r['category'] for r in state['categories']})
  with patch.object(classification,'get_categories',return_value=categories),patch.object(db,'get_connection',side_effect=AssertionError('Suggestion must not write/read DB in this pure test')):
   result=classification.classify_transactions(frame,memory)
  expected=[('Funding to Stanislavskiy Corp as shareholder','REPLIT'),('Childcare','Swimming'),('Medical, insurance and healthcare','Pharmacy'),('Transportation','Driver'),('Lifestyle','Clothing'),('Interest, fees and charges',''),('Own funds',''),('Childcare','Football training')]
  self.assertEqual(list(zip(result.suggested_category,result.suggested_subcategory)),expected)
  allowed={(r['category'],r['subcategory']) for r in state['categories']}
  self.assertTrue(all(pair in allowed for pair in expected))
  pd.testing.assert_frame_equal(before,memory);pd.testing.assert_frame_equal(original,frame)
  pd.testing.assert_frame_equal(original,result[original.columns])
 def test_ordinary_exact_matches_and_missing_driver_history_not_forced(self):
  memory=pd.DataFrame([dict(category='Ordinary',subcategory='Known',normalized_description='EXACT SAVED',original_description='Exact saved',transaction_type='expense',times_seen=3,last_seen='2026-11-01')])
  frame=pd.DataFrame([dict(normalized_description='EXACT SAVED',beneficiary='',transaction_type='expense',Amount=-1),dict(normalized_description='ANDREAS POUPAS',beneficiary='',transaction_type='expense',Amount=-1)])
  with patch.object(classification,'get_categories',return_value=['Ordinary','Transportation','UNIDENTIFIED EXPENSES']):result=classification.classify_transactions(frame,memory)
  self.assertEqual((result.iloc[0].suggested_category,result.iloc[0].suggested_subcategory,result.iloc[0].match_type),('Ordinary','Known','exact'))
  self.assertNotEqual(result.iloc[1].suggested_subcategory,'Driver')
 def test_conflicting_generic_history_does_not_invent_a_new_pair(self):
  memory=pd.DataFrame([dict(category=c,subcategory='Swimming',normalized_description='SWIMMING TRAINING',original_description='Swimming',transaction_type='expense',times_seen=3,last_seen='2026-11-01') for c in ('A','B')])
  index=classification._label_history_indexes(memory,['A','B'])
  self.assertIsNone(classification._specific_history_hint('swimming',index))

class ReportTests(unittest.TestCase):
 def row(self,bank,number,value,owner='Synthetic',complete=True):
  row={key:'' for key in COLUMNS};row.update({'Bank':bank,'Account number':number,'Account name':owner,'Currency':'USD','Closing balance':Decimal(value),'Closing balance converted to USD':Decimal(value) if complete else 'NOT AVAILABLE','Status':'IMPORTED' if complete else 'INCOMPLETE','Verification':'LIABILITY CONVENTION UNVERIFIED' if complete else 'INCOMPLETE METADATA','Statement end date':'2026-10-07'})
  if bank=='CNB':row['Verification']='SOURCE RECONCILIATION NOT VERIFIED'
  return row
 def test_only_the_exact_current_facility_moves_and_totals_do_not_change(self):
  rows=[self.row('CNB','D','100.25'),self.row('AMEX','A','-20.50'),self.row('Fifth Third (ex-Comerica)','055-9000-0991 Credit line  00548546519','-771915.46','Woking Way LLC',False)]
  path=Path(__file__).resolve().parent.parent/'areti-final-four-uat-20261026/balance_report_sections.py'
  spec=importlib.util.spec_from_file_location('accepted_sections',path);old=importlib.util.module_from_spec(spec);spec.loader.exec_module(old)
  frozen=copy.deepcopy(rows)
  self.assertEqual(old.section(rows[-1]),'Deposits');self.assertEqual(section(rows[-1]),'Debts')
  self.assertEqual(old.totals(rows),totals(rows));self.assertEqual(rows,frozen)
  for bank,number,owner in [('Fifth Third (ex-Comerica)','OTHER','Woking Way LLC'),('CNB',rows[-1]['Account number'],'Woking Way LLC'),('Fifth Third (ex-Comerica)',rows[-1]['Account number'],'Other owner')]:
   self.assertEqual(section(self.row(bank,number,'1',owner)),'Deposits')
 def test_both_views_share_column_widths_and_keep_all_cells_and_exports(self):
  rows=[self.row('CNB','SHORT','100.25'),self.row('AMEX','A VERY LONG ACCOUNT IDENTIFIER','-20.50')];frozen=copy.deepcopy(rows)
  html=compact_html(rows);doc=print_document(rows,Decimal('100.25'),[]);screen=screen_document(doc)
  for markup in (html,screen):
   groups=re.findall(r'<colgroup>(.*?)</colgroup>',markup)
   self.assertEqual(len(groups),2);self.assertEqual(groups[0],groups[1])
   for value in ('100.25','-20.50','Deposits','Debts'):self.assertIn(value,markup)
  self.assertEqual(rows,frozen)
  baseline=Path(__file__).resolve().parent.parent/'areti-final-four-uat-20261026/latest_import_balances.py'
  functions=lambda p:{n.name:ast.dump(n) for n in ast.parse(p.read_text(encoding='utf-8-sig')).body if isinstance(n,ast.FunctionDef)}
  before,after=functions(baseline),functions(Path('latest_import_balances.py'))
  for name in ('snapshot','workbook_bytes','print_document','_balance_import_date','fresh_closing'):self.assertEqual(before[name],after[name],name)
  root=Path(os.environ['ARETI_TODAY_ROOT']);(root/'report-after-compact.html').write_text(html);(root/'report-after-full.html').write_text(screen)

if __name__=='__main__':
 suite=unittest.TestSuite(unittest.defaultTestLoader.loadTestsFromTestCase(c) for c in (MemoryTests,ReportTests))
 raise SystemExit(not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful())
