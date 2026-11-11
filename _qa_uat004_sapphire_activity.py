"""UAT-004: source-bound activity display only; all stored data preserved."""
import os,json,hashlib,re,unittest
from pathlib import Path
from io import BytesIO
from decimal import Decimal as D
from unittest.mock import patch
import pandas as pd
import parsing,db,pdfplumber
import sapphire_balance_metadata as sapphire
import latest_import_balances as report
import _qa_balance_sapphire_20261111 as old

class ActivityTests(old.SapphireTests):
 def test_exact_source_eleven_lines_existing_ten_transactions_preserved(self):
  data,digest=self.seed();before=self.snapshot();f,b,a=sapphire.prepare(db,data,old.SOURCE.name)
  with pdfplumber.open(BytesIO(data)) as pdf:text='\n'.join(p.extract_text() or '' for p in pdf.pages)
  lines=re.findall(r'^\d{2}/\d{2} .+? (-?\d+\.\d{2})$',text,re.M)
  self.assertEqual(len(lines),11);self.assertEqual(len(f),10)
  self.assertEqual((b['source_purchases'],b['source_interest'],b['money_out']),(D('507.96'),D('7.18'),D('515.14')))
  original=parsing.parse_pdf(BytesIO(data));pd.testing.assert_frame_equal(f,original)
  row=next(r for r in report.snapshot(db)[0] if r['Account number']=='4147202716389513')
  self.assertEqual(tuple(row[k] for k in ('Opening balance','Credits / money in','Debits / money out','Closing balance')),(D('-387.55'),D('375'),D('-515.14'),D('-527.69')))
  self.assertEqual(row['Opening balance']+row['Credits / money in']+row['Debits / money out'],row['Closing balance'])
  self.assertEqual(before,self.snapshot())
  self.assertEqual(sapphire.record(db,data,old.SOURCE.name,'QA'),'already recorded');self.assertEqual(before,self.snapshot())

 def test_report_only_two_activity_cells_change_all_other_rows_fx_totals_preserved(self):
  old.fixture();before=self.snapshot()
  with patch.object(sapphire,'report_activity',side_effect=lambda row,c,d:(c,d)):
   prior=report.snapshot(db)
  after=report.snapshot(db);self.assertEqual(before,self.snapshot())
  deltas=[]
  for a,b in zip(prior[0],after[0]):
   for k in a:
    if a[k]!=b[k]:deltas.append((a['Account number'],k,a[k],b[k]))
  self.assertEqual(deltas,[('4147202716389513','Credits / money in',D('-375'),D('375')),('4147202716389513','Debits / money out',D('507.96'),D('-515.14'))])
  self.assertEqual(prior[1:],after[1:])
  from balance_report_sections import totals,grand_total
  self.assertEqual(totals(prior[0]),totals(after[0]));self.assertEqual(grand_total(prior[0]),grand_total(after[0]))

 def test_source_boundary_and_no_double_interest(self):
  row=dict(bank='Sapphire Chase',account_name='Timur Bekmambetov',account_number='4147202716389513',currency='USD',statement_currency='USD',statement_account_number='4147202716389513',statement_bank='Sapphire Chase',statement_account_name='Timur Bekmambetov',source='parsed',balance_statement_hash='9d11c8568b93a5557e94efcf621b35f2c0ccf23b6c10f862f4038225b23aa358',period_start='2026-09-28',period_end='2026-10-27',opening_balance='387.55',closing_balance='527.69')
  self.assertEqual(sapphire.report_activity(row,D('-375'),D('515.14')),(D('375'),D('-515.14')))
  for key,value in [('bank','AMEX'),('bank','Citi'),('bank','Fifth Third (ex-Comerica)'),('balance_statement_hash','other'),('period_end','2026-09-27'),('opening_balance','388.55'),('statement_account_number','other')]:
   with self.subTest(key=key,value=value):self.assertEqual(sapphire.report_activity(dict(row,**{key:value}),D('-375'),D('507.96')),(D('-375'),D('507.96')))

if __name__=='__main__':
 suite=unittest.TestSuite(ActivityTests(n) for n in ActivityTests.__dict__ if n.startswith('test_'))
 result=unittest.TextTestRunner(verbosity=2).run(suite)
 raise SystemExit(not result.wasSuccessful())
