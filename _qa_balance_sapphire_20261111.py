"""Current report and Sapphire originals only; private evidence remains external."""
import ast,copy,importlib.util,json,os,re,sqlite3,unittest
from decimal import Decimal
from pathlib import Path
from io import BytesIO
from contextlib import closing
from hashlib import sha256
from unittest.mock import patch
import db,parsing,sapphire_balance_metadata as sapphire
import latest_import_balances as report
from balance_report_sections import totals,section
from latest_balances_compact import compact_html
from import_history import commit_statement
from _qa_post_import_message import CompletionTests
ROOT=Path(os.environ['ARETI_BALANCE_UAT_ROOT'])
EXPECTED=json.loads((ROOT/'expected-private.json').read_text())
SOURCE=Path(r'C:/Users/Student/Downloads/20261027-statements-9513-.pdf')

def fixture():
 spec=importlib.util.spec_from_file_location('private_report_fixture',ROOT/'report_fixture.py')
 m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);m.create()

class ReportTests(unittest.TestCase):
 def test_actual_report_rows_values_totals_and_only_two_expected_changes(self):
  fixture();before=json.loads((ROOT/'report-before-private.json').read_text());rows,_,warnings=report.snapshot(db)
  old={r['Account number']:r for r in before['rows']};new={r['Account number']:r for r in rows}
  self.assertEqual(set(old),set(new));changes={}
  for number,row in new.items():
   changed=[k for k,v in row.items() if str(v)!=str(old[number][k])]
   if changed:changes[number]=changed
  self.assertEqual(set(changes),{EXPECTED['loc_number'],EXPECTED['number']})
  loc=new[EXPECTED['loc_number']];chase=new[EXPECTED['number']]
  self.assertEqual((loc['Closing balance'],loc['Closing balance converted to USD']),(Decimal(EXPECTED['loc_closing']),Decimal(EXPECTED['loc_closing'])))
  self.assertIsNone(loc['Opening balance']);self.assertEqual(loc['Statement start date'],'');self.assertEqual(loc['Status'],'CLOSING ONLY')
  self.assertEqual((chase['Opening balance'],chase['Closing balance'],chase['Closing balance converted to USD']),(-Decimal(EXPECTED['opening']),-Decimal(EXPECTED['closing']),-Decimal(EXPECTED['closing'])))
  amounts=totals(rows);previous=before['totals']
  self.assertEqual(amounts['Deposits'],Decimal(previous['Deposits']))
  self.assertEqual(amounts['Debts'],Decimal(previous['Debts'])--Decimal(EXPECTED['loc_closing'])-2*Decimal(EXPECTED['closing']))
  (ROOT/'report-after-private.json').write_text(json.dumps({'rows':rows,'totals':amounts,'grand':sum(amounts.values()),'warnings':warnings},default=str,indent=2))
 def test_html_excel_total_column_and_one_signed_grand_total(self):
  fixture();rows,total,warnings=report.snapshot(db);amounts=totals(rows);grand=sum(amounts.values())
  full=report.print_document(rows,total,warnings);compact=compact_html(rows)
  self.assertEqual(full.count('>Grand Total<'),1);self.assertIn(format(grand,',.2f'),full)
  self.assertIn('colspan="9" style="text-align:right">Debts USD Total',full)
  for html in (report.screen_document(full),compact):
   groups=re.findall(r'<colgroup>(.*?)</colgroup>',html);self.assertEqual(len(groups),2);self.assertEqual(groups[0],groups[1])
   self.assertEqual(html.count('>Grand Total<'),1)
  from openpyxl import load_workbook
  book=load_workbook(BytesIO(report.workbook_bytes(rows,total,warnings)))
  for name in ('Deposits','Debts'):
   row=next(r for r in book[name].iter_rows(values_only=True) if r[0]==name+' USD Total')
   self.assertEqual(Decimal(row[9]),amounts[name])
  self.assertEqual(Decimal(book['USD Totals']['B4'].value),grand)
 def test_closing_only_proof_tamper_rejected_and_other_account_not_promoted(self):
  fixture()
  with closing(db.get_connection()) as c:
   c.execute("UPDATE statement_balances SET notes='{}' WHERE source='Fifth Third Flexline closing metadata only'");c.commit()
  rows,_,_=report.snapshot(db);loc=next(r for r in rows if '00548546519' in r['Account number'])
  self.assertEqual(loc['Closing balance converted to USD'],'NOT AVAILABLE');self.assertEqual(loc['Status'],'INCOMPLETE')

class SapphireTests(CompletionTests):
 def seed(self,balances=True):
  data=SOURCE.read_bytes();frame=parsing.parse_pdf(BytesIO(data));b=sapphire.source(data)
  a=dict(account_name='Timur Bekmambetov',bank='Sapphire Chase',account_number=b['account_number'],currency='USD',rate_type='USD/USD')
  with closing(db.get_connection()) as c:
   c.execute('INSERT INTO account_list(account_name,bank,account_number,currency,rate_type) VALUES(?,?,?,?,?)',tuple(a.values()));c.commit()
  digest=sha256(data).hexdigest();mapped=db.apply_account_and_rates(frame,a)
  commit_statement(db,mapped,SOURCE.name,digest,parsing.extract_statement_balance(BytesIO(data),SOURCE.name),a)
  if not balances:
   with closing(db.get_connection()) as c:c.execute('DELETE FROM statement_balances WHERE statement_hash=?',(digest,));c.commit()
  return data,digest
 def test_exact_source_account_summary_and_unchanged_ten_transactions(self):
  data,_=self.seed();before=self.snapshot();frame,b,a=sapphire.prepare(db,data,SOURCE.name)
  expected=json.loads((ROOT/'source-baseline-private.json').read_text())
  self.assertEqual(json.loads(json.dumps(frame.to_dict('records'),default=str)),expected['rows']);self.assertEqual(len(frame),10)
  self.assertEqual(-b['opening_balance']+sum(frame.Amount)-b['source_interest'],-b['closing_balance'])
  self.assertEqual((b['opening_balance'],b['closing_balance'],b['source_interest']),(Decimal(EXPECTED['opening']),Decimal(EXPECTED['closing']),Decimal(EXPECTED['interest'])))
  self.assertEqual(sapphire.record(db,data,SOURCE.name,'Synthetic'),'already recorded');self.assertEqual(before,self.snapshot())
 def test_missing_balance_only_zero_transactions_repeat_and_rollback(self):
  data,digest=self.seed(False);before=self.snapshot()
  with patch.object(db,'save_statement_balance',side_effect=RuntimeError('synthetic rollback')):
   with self.assertRaises(RuntimeError):sapphire.record(db,data,SOURCE.name,'Synthetic')
  self.assertEqual(self.snapshot(),before)
  with closing(db.get_connection()) as c:transactions=c.execute('SELECT * FROM classified_transactions ORDER BY id').fetchall();history=c.execute('SELECT * FROM statement_imports ORDER BY id').fetchall()
  self.assertEqual(sapphire.record(db,data,SOURCE.name,'Synthetic'),'recorded');saved=self.snapshot()
  self.assertEqual(sapphire.record(db,data,SOURCE.name,'Synthetic'),'already recorded');self.assertEqual(saved,self.snapshot())
  with closing(db.get_connection()) as c:
   self.assertEqual(transactions,c.execute('SELECT * FROM classified_transactions ORDER BY id').fetchall());self.assertEqual(history,c.execute('SELECT * FROM statement_imports ORDER BY id').fetchall())
  self.assertEqual(db.get_import_history().iloc[0].reconciliation_status,'OK')
 def test_source_identity_transaction_tamper_and_conflicting_balance_rejected(self):
  data,digest=self.seed();before=self.snapshot()
  with closing(db.get_connection()) as c:c.execute("UPDATE account_list SET account_number='9999' WHERE bank='Sapphire Chase'");c.commit()
  with self.assertRaises(ValueError):sapphire.prepare(db,data,SOURCE.name)
  with closing(db.get_connection()) as c:c.execute('UPDATE account_list SET account_number=? WHERE bank=?',(sapphire.source(data)['account_number'],'Sapphire Chase'));c.commit()
  self.assertEqual(before,self.snapshot())
  with closing(db.get_connection()) as c:c.execute('UPDATE classified_transactions SET amount=amount+1 WHERE statement_hash=?',(digest,));c.commit()
  bad=self.snapshot()
  with self.assertRaisesRegex(ValueError,'transaction identity'):sapphire.record(db,data,SOURCE.name,'Synthetic')
  self.assertEqual(bad,self.snapshot())
 def test_existing_duplicate_ui_shows_negative_balances_and_no_write(self):
  data,_=self.seed();self.file=BytesIO(data);self.file.name=SOURCE.name
  before=self.snapshot();app=self.run_app(self.app())
  button=next(b for b in app.button if b.label=='Import Balances Only')
  import streamlit as st
  with patch.object(st,'file_uploader',return_value=self.file):button.click().run()
  self.assertFalse(app.exception);self.assertFalse(app.error,[e.value for e in app.error])
  preview=app.session_state['upload_balance_preview'];self.assertEqual(Decimal(preview['opening_balance']),-Decimal(EXPECTED['opening']));self.assertEqual(Decimal(preview['closing_balance']),-Decimal(EXPECTED['closing']))
  self.assertEqual(before,self.snapshot())
 def test_correct_credit_balance_not_double_negated_and_unrelated_rows_unchanged(self):
  row=dict(bank='Sapphire Chase',account_name='Timur Bekmambetov',account_number=EXPECTED['number'],currency='USD',statement_currency='USD',statement_account_number=EXPECTED['number'],source='parsed')
  self.assertEqual(sapphire.report_values(row,Decimal('-10'),Decimal('-20')),(Decimal('10'),Decimal('20'),True))
  row['bank']='Citi';self.assertEqual(sapphire.report_values(row,Decimal('10'),Decimal('20')),(Decimal('10'),Decimal('20'),False))
 def test_conflicting_historical_balance_is_rejected_without_rewrite(self):
  data,digest=self.seed()
  with closing(db.get_connection()) as c:c.execute('UPDATE statement_balances SET closing_balance=closing_balance+1 WHERE statement_hash=?',(digest,));c.commit()
  before=self.snapshot()
  with self.assertRaisesRegex(ValueError,'balance conflicts'):sapphire.record(db,data,SOURCE.name,'Synthetic')
  self.assertEqual(before,self.snapshot())
 def test_inconsistent_source_coupon_and_interest_details_rejected(self):
  import pdfplumber
  from unittest.mock import MagicMock
  data=SOURCE.read_bytes()
  with pdfplumber.open(BytesIO(data)) as doc:texts=[p.extract_text() or '' for p in doc.pages]
  for mode in ('coupon','interest'):
   changed=list(texts)
   if mode=='coupon':changed[0]=changed[0].replace('New Balance: $'+EXPECTED['closing'],'New Balance: $'+str(Decimal(EXPECTED['closing'])+Decimal('.01')))
   else:changed[0]=changed[0].replace('Interest Charged +$'+EXPECTED['interest'],'Interest Charged +$'+str(Decimal(EXPECTED['interest'])+Decimal('.01'))).replace('New Balance $'+EXPECTED['closing'],'New Balance $'+str(Decimal(EXPECTED['closing'])+Decimal('.01'))).replace('New Balance: $'+EXPECTED['closing'],'New Balance: $'+str(Decimal(EXPECTED['closing'])+Decimal('.01')))
   doc=MagicMock();doc.__enter__.return_value.pages=[MagicMock(extract_text=lambda t=t:t) for t in changed]
   with patch.object(pdfplumber,'open',return_value=doc),self.assertRaises(ValueError):sapphire.source(data)

if __name__=='__main__':
 suite=unittest.TestSuite()
 for c in (ReportTests,SapphireTests):suite.addTests(c(n) for n in c.__dict__ if n.startswith('test_'))
 raise SystemExit(not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful())
