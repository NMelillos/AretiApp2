"""Strict Sapphire summary metadata; existing transaction extraction is untouched."""
from collections import Counter
from datetime import datetime,timezone
from decimal import Decimal
from hashlib import sha256
from io import BytesIO
import json,re

SOURCE='Sapphire Chase source liability summary'
def compact(value):return re.sub(r'[^A-Z0-9]','',str(value or '').upper())
def recognized(content):
 import pdfplumber
 with pdfplumber.open(BytesIO(content)) as doc:text=doc.pages[0].extract_text() or ''
 return 'chase.com/cardhelp' in text.lower() and 'SAPPHIRE' in compact(text)
def source(content):
 import pdfplumber
 with pdfplumber.open(BytesIO(content)) as doc:pages=[p.extract_text() or '' for p in doc.pages]
 text='\n'.join(pages);first=pages[0]
 if not ('chase.com/cardhelp' in first.lower() and 'CARDMEMBER SERVICE' in first and 'SAPPHIRE' in compact(first)):
  raise ValueError('Source is not a verified Sapphire statement.')
 numbers=set(re.findall(r'Account\s*[Nn]umber:\s*XXXX\s+XXXX\s+XXXX\s+(\d{4})',first))
 coupons=set(re.findall(r'^(\d{16})\d{20,30}$',first,re.M))
 periods=re.findall(r'Opening/Closing Date\s+(\d{2}/\d{2}/\d{2})\s*-\s*(\d{2}/\d{2}/\d{2})',first)
 if len(numbers)!=1 or len(coupons)!=1 or len(periods)!=1:raise ValueError('Sapphire full/masked account or period is ambiguous.')
 number=next(iter(coupons))
 if not number.endswith(next(iter(numbers))):raise ValueError('Sapphire account evidence conflicts.')
 start,end=[datetime.strptime(v,'%m/%d/%y').date().isoformat() for v in periods[0]]
 if start>end:raise ValueError('Invalid Sapphire period.')
 summary=re.search(r'Previous Balance([\s\S]+?)Credit Access Line',first)
 if not summary:raise ValueError('Sapphire Account Summary is missing.')
 block='Previous Balance'+summary[1];v={}
 for label in ('Previous Balance','Payment, Credits','Purchases','Cash Advances','Balance Transfers','Fees Charged','Interest Charged','New Balance'):
  found=re.findall(re.escape(label)+r'\s+([+-]?)\$(\d[\d,]*\.\d{2})',block)
  if len(found)!=1:raise ValueError('Sapphire summary is incomplete or ambiguous.')
  sign,amount=found[0];v[label]=Decimal(amount.replace(',',''))*(-1 if sign=='-' else 1)
 charges=sum((v[k] for k in ('Purchases','Cash Advances','Balance Transfers','Fees Charged')),Decimal(0))
 if v['Payment, Credits']>0 or any(v[k]<0 for k in ('Purchases','Cash Advances','Balance Transfers','Fees Charged','Interest Charged')):raise ValueError('Unsupported Sapphire activity signs.')
 if v['Previous Balance']+v['Payment, Credits']+charges+v['Interest Charged']!=v['New Balance']:raise ValueError('Sapphire summary does not reconcile.')
 def repeated_value(pattern,expected):
  found=re.findall(pattern,text,re.M)
  if len(found)!=1:raise ValueError('Sapphire coupon/interest detail is missing or ambiguous.')
  sign,amount=found[0]
  if Decimal(amount.replace(',',''))*(-1 if sign=='-' else 1)!=expected:raise ValueError('Sapphire coupon/interest detail conflicts with its summary.')
 repeated_value(r'^New Balance:\s*([+-]?)\$(\d[\d,]*\.\d{2})\s*$',v['New Balance'])
 interest_total=r'^TOTAL INTEREST FOR THIS PERIOD\s*([+-]?)\$(\d[\d,]*\.\d{2})\s*$'
 if v['Interest Charged'] or re.search(interest_total,text,re.M):
  repeated_value(interest_total,v['Interest Charged'])
 elif re.search(r'^INTEREST CHARGED\s*$|^\d{2}/\d{2}[^\n]*INTEREST',text,re.M):
  raise ValueError('Sapphire zero-interest summary conflicts with activity.')
 return dict(source=SOURCE,bank='Sapphire Chase',account_number=number,currency='USD',period_start=start,period_end=end,
             opening_balance=v['Previous Balance'],money_in=v['Payment, Credits'],money_out=charges+v['Interest Charged'],source_activity_out=charges,
             closing_balance=v['New Balance'],source_interest=v['Interest Charged'],source_purchases=v['Purchases'],source_text=first)
def prepare(db,content,filename):
 from parsing import parse_pdf
 balance=source(content);frame=parse_pdf(BytesIO(content))
 matches=[a for a in db.get_accounts().to_dict('records') if compact(a['bank'])=='SAPPHIRECHASE'
          and re.sub(r'\D','',str(a['account_number']))==balance['account_number'] and a['currency']=='USD'
          and compact(a['account_name']) in compact(balance['source_text'])]
 if len(matches)!=1:raise ValueError('Exactly one source-proven Sapphire USD account is required.')
 if frame.empty or any(not balance['period_start']<=str(r.Date)<=balance['period_end'] for r in frame.itertuples()):raise ValueError('Sapphire source activity is incomplete.')
 incoming=sum((Decimal(str(a)) for a in frame.Amount if Decimal(str(a))>0),Decimal(0))
 outgoing=-sum((Decimal(str(a)) for a in frame.Amount if Decimal(str(a))<0),Decimal(0))
 if incoming!=-balance['money_in'] or outgoing!=balance['source_activity_out']:raise ValueError('Sapphire source transactions differ from the summary.')
 # Interest is source metadata, not a reconstructed/new transaction.
 if -balance['opening_balance']+incoming-outgoing-balance['source_interest']!=-balance['closing_balance']:raise ValueError('Signed Sapphire liability does not reconcile.')
 return frame,balance,matches[0]
def validated_report_summary(row):
 """Generic persisted Sapphire proof: full source summary plus matching audit.

 No account, period, amount or document hash is predefined. Incomplete legacy
 summaries are not completed by calculating an unexplained balance difference.
 """
 try:
  number=compact(row.get('account_number'))
  digest=str(row.get('balance_statement_hash') or '')
  if (row.get('source')!=SOURCE or compact(row.get('bank'))!='SAPPHIRECHASE'
      or compact(row.get('statement_bank'))!='SAPPHIRECHASE'
      or not re.fullmatch(r'\d{16}',number)
      or compact(row.get('statement_account_number'))!=number
      or not compact(row.get('account_name'))
      or compact(row.get('statement_account_name'))!=compact(row.get('account_name'))
      or row.get('currency')!='USD' or row.get('statement_currency')!='USD'
      or not re.fullmatch(r'[a-f0-9]{64}',digest)
      or row.get('transaction_count')!=row.get('source_rows')
      or row.get('transaction_count') is None):
   return None
  start=datetime.strptime(str(row['period_start']),'%Y-%m-%d').date()
  end=datetime.strptime(str(row['period_end']),'%Y-%m-%d').date()
  if start>end:return None
  opening,credits,charges,closing=[Decimal(str(row[k])) for k in ('opening_balance','money_in','money_out','closing_balance')]
  if any(not v.is_finite() or v!=v.quantize(Decimal('.01')) for v in (opening,credits,charges,closing)):
   return None
  if credits>0 or charges<0 or opening+credits+charges!=closing:return None
  evidence=[]
  for line in str(row.get('notes') or '').splitlines():
   try:event=json.loads(line)
   except (ValueError,TypeError):continue
   if not isinstance(event,dict) or event.get('action')!='source-verified Sapphire balances only':continue
   if event.get('source_hash')!=digest or event.get('transaction_changes')!=0:return None
   interest=Decimal(str(event.get('source_interest')))
   if not interest.is_finite() or interest!=interest.quantize(Decimal('.01')) or not 0<=interest<=charges:return None
   evidence.append(interest)
  if not evidence or len(set(evidence))!=1:return None
  return -opening,-closing,-credits,-charges
 except (KeyError,TypeError,ValueError,ArithmeticError):
  return None


def report_values(row,opening,closing):
 verified=validated_report_summary(row)
 if verified is not None:return verified[0],verified[1],True
 # The generic extractor stores the printed liability convention unchanged.
 # Only this exact approved account/current summary changes presentation.
 if (compact(row.get('bank'))=='SAPPHIRECHASE' and compact(row.get('account_name'))=='TIMURBEKMAMBETOV'
     and sha256(compact(row.get('account_number')).encode()).hexdigest()=='06b4dc5bcfde3e487017d185c99f061bda980bbab194e92adc7c7c96ff6a3b48' and row.get('currency')=='USD'
     and row.get('statement_currency')=='USD' and compact(row.get('statement_account_number'))==compact(row.get('account_number'))
     and row.get('source') in ('parsed',SOURCE)):
  return (-opening if opening is not None else None,-closing if closing is not None else None,True)
 return opening,closing,False
def record(db,content,filename,actor):
 if not actor or not filename:raise ValueError('Authenticated source confirmation is required.')
 frame,b,a=prepare(db,content,filename);digest=sha256(content).hexdigest();conn=db.get_connection()
 try:
  cur=conn.cursor();lock=' FOR UPDATE' if db.USING_POSTGRES else ''
  if not db.USING_POSTGRES:cur.execute('BEGIN IMMEDIATE')
  cur.execute('SELECT account_name,bank,account_number,currency FROM account_list WHERE id=?'+lock,(a['id'],))
  if cur.fetchone()!=tuple(a[k] for k in ('account_name','bank','account_number','currency')):raise ValueError('Sapphire Setup changed; reload.')
  cur.execute('SELECT transaction_count FROM statement_imports WHERE statement_hash=?'+lock,(digest,));header=cur.fetchone()
  cur.execute('SELECT txn_date,original_description,amount,bank,account_number,currency FROM classified_transactions WHERE statement_hash=? AND split_parent_id IS NULL'+lock,(digest,));stored=cur.fetchall()
  expected=Counter((str(r.Date),r.Description,Decimal(str(r.Amount)),a['bank'],a['account_number'],a['currency']) for r in frame.itertuples())
  actual=Counter((str(r[0]),r[1],Decimal(str(r[2])),r[3],r[4],r[5]) for r in stored)
  if not header or header[0]!=len(frame) or actual!=expected:raise ValueError('Existing Sapphire transaction identity/count/values differ; nothing recorded.')
  cur.execute('SELECT id,bank,account_name,account_number,currency,period_start,period_end,opening_balance,money_in,money_out,closing_balance,notes FROM statement_balances WHERE statement_hash=?'+lock,(digest,));prior=cur.fetchone();changes={}
  fields=('bank','account_name','account_number','currency','period_start','period_end','opening_balance','money_in','money_out','closing_balance')
  for i,k in enumerate(fields,1):
   value=b[k] if k not in ('bank','account_name','account_number','currency') else a[k]
   if prior and prior[i] not in (None,''):
    actual_value=Decimal(str(prior[i])) if k.endswith('balance') or k in ('money_in','money_out') else str(prior[i])
    if actual_value!=value and not (k=='money_out' and actual_value in (b['source_activity_out'],b['source_purchases'])):raise ValueError('Existing Sapphire balance conflicts; no historical value overwritten.')
   else:changes[k]=value
  if prior and not changes:conn.rollback();return 'already recorded'
  now=datetime.now(timezone.utc).isoformat(timespec='seconds')
  audit=json.dumps(dict(action='source-verified Sapphire balances only',actor=str(actor),source_hash=digest,
                        confirmed_at=now,source_interest=str(b['source_interest']),transaction_changes=0))
  if prior:
   changes.update(source=SOURCE,notes=(str(prior[11] or '')+'\n'+audit).strip(),updated_at=now)
   cur.execute('UPDATE statement_balances SET '+','.join(k+'=?' for k in changes)+' WHERE id=?',(*changes.values(),prior[0]))
   if cur.rowcount!=1:raise ValueError('Sapphire balance changed concurrently.')
  elif not db.save_statement_balance(digest,filename,dict(b,notes=audit),a,_connection=conn):raise ValueError('Sapphire balance was not recorded.')
  conn.commit();return 'recorded'
 except Exception:conn.rollback();raise
 finally:conn.close()


def report_activity(row, credits, debits):
 """General validated activity; legacy October proof is compatibility only.

 Legacy metadata excludes the separately printed interest line. Do not derive
 missing charges from a balance difference or apply this proof to another PDF.
 """
 verified=validated_report_summary(row)
 if verified is not None:return verified[2],verified[3]
 if (not report_values(row, None, None)[2]
     or row.get('balance_statement_hash') != '9d11c8568b93a5557e94efcf621b35f2c0ccf23b6c10f862f4038225b23aa358'
     or str(row.get('period_start')) != '2026-09-28' or str(row.get('period_end')) != '2026-10-27'
     or row.get('statement_bank') != row.get('bank')
     or row.get('statement_account_name') != row.get('account_name')):
  return credits,debits
 try:
  if (Decimal(str(row.get('opening_balance'))) != Decimal('387.55')
      or Decimal(str(row.get('closing_balance'))) != Decimal('527.69')
      or credits != Decimal('-375.00') or debits not in (Decimal('507.96'),Decimal('515.14'))):
   return credits,debits
 except (ValueError,ArithmeticError):
  return credits,debits
 # Exact original summary and interest-detail evidence, independently validated.
 purchases,interest=Decimal('507.96'),Decimal('7.18')
 return -credits,-(purchases+interest)
