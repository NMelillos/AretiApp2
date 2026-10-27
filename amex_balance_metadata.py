"""Screen-reader AMEX balances with imported-CSV identity proof; never parse PDF activity."""
import csv,json,re
from datetime import datetime,timedelta,timezone
from decimal import Decimal
from hashlib import sha256
from io import BytesIO,StringIO

SOURCE='AMEX screen-reader balance metadata'
ACTION='user-confirmed AMEX PDF balances only'
FIELDS=('period_start','period_end','opening_balance','money_in','money_out','closing_balance')

def compact(value):return re.sub(r'[^A-Z0-9]','',str(value or '').upper())
def base_number(value):return re.sub(r'\s*\[.*?\]\s*','',str(value)).strip()

def texts(content):
    import pdfplumber
    with pdfplumber.open(BytesIO(content)) as document:return [p.extract_text() or '' for p in document.pages]

def recognized(content):
    import pdfplumber
    with pdfplumber.open(BytesIO(content)) as document:
        first='\n'.join(p.extract_text() or '' for p in document.pages[:2])
    return 'Business Platinum Card' in first and 'Account Ending' in first and 'americanexpress.com' in first.lower()

def parse(content):
    pages=texts(content);text='\n'.join(pages)
    if ('Screen Reader Optimized version' not in pages[0] or 'Business Platinum Card' not in pages[0]
        or 'www.americanexpress.com' not in text):raise ValueError('Use the verified AMEX Screen Reader Optimized PDF for balances only.')
    def one(pattern):
        values=re.findall(pattern,text,re.MULTILINE)
        if len(values)!=1:raise ValueError('AMEX source identity/summary is missing or ambiguous.')
        return values[0]
    holder,company,ending=one(r'^Prepared for\s*\n([^\n]+)\n([^\n]+)\nAccount Ending (\d-\d{5})\s*$')
    closing=datetime.strptime(one(r'^Closing Date (\d{2}/\d{2}/\d{2})\s*$'),'%m/%d/%y').date()
    days=int(one(r'^Days in Billing Period: (\d+)\s*$'))
    if not 1<=days<=62:raise ValueError('AMEX billing-period length is invalid.')
    summary=one(r'^Account Total\s*\n([\s\S]+?)\nPay Over Time Limit ')
    def amount(label):
        found=re.findall(r'^'+re.escape(label)+r' \$(-?(?:\d{1,3}(?:,\d{3})+|\d+)\.\d{2})\s*$',summary,re.MULTILINE)
        if len(found)!=1:raise ValueError('AMEX Account Total is not deterministic.')
        return Decimal(found[0].replace(',',''))
    opening=amount('Previous Balance');payments=amount('Less Payments/Credits')
    charges=amount('Plus New Charges')+amount('Plus Fees')+amount('Plus Interest Charged')
    end=amount('Equals New Balance')
    payment_summary=re.findall(r'^New Balance \$(-?[\d,]+\.\d{2})\s*$',text,re.MULTILINE)
    owing = min(opening,payments,charges,end)>=0 and opening-payments+charges==end
    signed = max(opening,charges,end)<=0 and payments>=0 and opening+payments+charges==end
    if len(payment_summary)!=1 or Decimal(payment_summary[0].replace(',',''))!=end or not (owing or signed):
        raise ValueError('AMEX liability summary does not reconcile.')
    # Metadata only: negative debt, positive payments, negative new charges.
    # Validate the printed equation before converting its complete sign vector.
    # The CSV transaction amounts and original CSV history are never changed.
    return dict(source=SOURCE,currency='USD',period_start=(closing-timedelta(days=days-1)).isoformat(),period_end=closing.isoformat(),
        opening_balance=-abs(opening),money_in=payments,money_out=-abs(charges),closing_balance=-abs(end),source_payments_credits=payments,
        holder=holder.strip(),company=company.strip(),card_ending=ending,billing_days=days)

def csv_evidence(content,source):
    rows=list(csv.DictReader(StringIO(content.decode('utf-8-sig'))))
    if not rows or not {'Date','Card Member','Account #','Amount','Description'}<=set(rows[0]):raise ValueError('Provide the original already-imported AMEX CSV as identity evidence only.')
    identities=sorted({(str(r['Card Member']).strip(),str(r['Account #']).strip()) for r in rows})
    primary=[pair for pair in identities if compact(pair[0])==compact(source['holder']) and compact(pair[1])==compact(source['card_ending'])[-5:]]
    if len(primary)!=1:raise ValueError('CSV card/member does not prove this PDF identity.')
    # The CSV proves facility/member identity, not PDF transaction equivalence.
    # Transaction dates can precede the billing window because of posting dates;
    # statement dates and financial summaries come only from the verified PDF.
    for row in rows:datetime.strptime(row['Date'],'%m/%d/%Y')
    return dict(csv_hash=sha256(content).hexdigest(),csv_count=len(rows),members=[p[0] for p in identities])

def checked_link(conn,source,evidence,accounts,lock=''):
    cur=conn.cursor()
    try:
        cur.execute('SELECT id,imported_at,transaction_count FROM statement_imports WHERE statement_hash=?'+lock,(evidence['csv_hash'],));header=cur.fetchone()
        if header is None or header[2]!=evidence['csv_count'] or header[2]<=0:raise ValueError('CSV identity evidence must already have a completed transaction import.')
        cur.execute('SELECT account_name,bank,account_number,currency FROM classified_transactions WHERE statement_hash=? AND split_parent_id IS NULL'+lock,(evidence['csv_hash'],))
        rows=cur.fetchall()
        if len(rows)!=header[2]:raise ValueError('CSV history/source count does not match.')
        candidates=[]
        for account in accounts:
            if compact(account['bank']) not in ('AMEX','AMERICANEXPRESS') or account['currency']!='USD' or compact(account['account_name'])!=compact(source['company']):continue
            base=base_number(account['account_number'])
            digits=re.sub(r'\D','',base);ending=re.sub(r'\D','',source['card_ending'])
            if not ((len(digits)==10 and digits[-1]==ending[0]) or (len(digits)==15 and digits.endswith(ending))):continue
            allowed={compact(base+' '+member) for member in evidence['members']}
            if (all(compact(r[0])==compact(source['company']) and compact(r[1]) in ('AMEX','AMERICANEXPRESS') and r[3]=='USD' and compact(r[2]) in allowed for r in rows)
                and any(compact(r[2])==compact(base+' '+source['holder']) for r in rows)):
                candidates.append(account)
        if len(candidates)!=1:raise ValueError('PDF/card/imported CSV do not prove exactly one current Setup account.')
        return dict(candidates[0]),dict(import_id=header[0],imported_at=header[1])
    finally:cur.close()

def prepare(db,content,csv_content):
    source=parse(content);evidence=csv_evidence(csv_content,source)
    conn=db.get_connection()
    try:account,event=checked_link(conn,source,evidence,db.get_accounts().to_dict('records'))
    finally:conn.close()
    return source,evidence,account,event

def verified_record(conn,notes,bank,number,currency,opening,credits,debits,closing,source_hash,period_start,period_end):
    """Only an explicit zero-transaction PDF ledger tied to completed CSV evidence qualifies."""
    try:
        event=json.loads(notes)
        if (event.get('action')!=ACTION or not event.get('actor') or event.get('transaction_changes')!=0
            or event.get('source_hash')!=source_hash or currency!='USD'):return None
        source=event['source'];evidence=event['csv_evidence'];account_id=event['setup_id']
        if source['source']!=SOURCE or source['period_start']!=str(period_start) or source['period_end']!=str(period_end):return None
        for key,value in zip(FIELDS[2:],(opening,credits,debits,closing)):
            if Decimal(str(value))!=Decimal(source[key]):return None
        if Decimal(source['opening_balance'])+Decimal(source['money_in'])+Decimal(source['money_out'])!=Decimal(source['closing_balance']):return None
        cur=conn.cursor()
        try:
            cur.execute('SELECT id,account_name,bank,account_number,currency FROM account_list WHERE id=?',(account_id,));row=cur.fetchone()
            if row is None:return None
            account=dict(zip(('id','account_name','bank','account_number','currency'),row))
            if account['bank']!=bank or account['account_number']!=number or account['currency']!=currency:return None
            cur.execute('SELECT transaction_count FROM statement_imports WHERE statement_hash=?',(source_hash,));header=cur.fetchone()
            cur.execute('SELECT COUNT(*) FROM classified_transactions WHERE statement_hash=?',(source_hash,));count=cur.fetchone()[0]
            if header is None or header[0]!=0 or count!=0:return None
        finally:cur.close()
        _,history=checked_link(conn,source,evidence,[account])
        return history
    except (ValueError,TypeError,KeyError,ArithmeticError):return None

def record(db,content,filename,csv_content,actor):
    if not actor:raise ValueError('Authenticated confirmation is required.')
    source,evidence,account,_=prepare(db,content,csv_content)
    digest=sha256(content).hexdigest();conn=db.get_connection()
    try:
        cur=conn.cursor()
        if not db.USING_POSTGRES:cur.execute('BEGIN IMMEDIATE')
        lock=' FOR SHARE' if db.USING_POSTGRES else ''
        cur.execute('SELECT id,account_name,bank,account_number,currency FROM account_list WHERE id=?'+lock,(account['id'],));row=cur.fetchone()
        if row is None or tuple(row)!=tuple(account[k] for k in ('id','account_name','bank','account_number','currency')):raise ValueError('AMEX Setup changed; reload.')
        checked_link(conn,source,evidence,[account],lock)
        cur.execute('SELECT id,transaction_count FROM statement_imports WHERE statement_hash=?'+(' FOR UPDATE' if db.USING_POSTGRES else ''),(digest,));header=cur.fetchone()
        cur.execute('SELECT COUNT(*) FROM classified_transactions WHERE statement_hash=?',(digest,))
        if cur.fetchone()[0] or (header is not None and header[1]!=0):raise ValueError('This PDF already has transaction history; no automatic rewrite permitted.')
        cur.execute('SELECT bank,account_number,currency,period_start,period_end,opening_balance,money_in,money_out,closing_balance FROM statement_balances WHERE statement_hash=? OR (source=? AND bank=? AND account_number=? AND currency=? AND period_start=? AND period_end=?)'+(' FOR UPDATE' if db.USING_POSTGRES else ''),(digest,SOURCE,account['bank'],account['account_number'],'USD',source['period_start'],source['period_end']))
        existing=cur.fetchall()
        if len(existing)>1:raise ValueError('Multiple AMEX balance identities already exist; no automatic rewrite permitted.')
        prior=existing[0] if existing else None
        if prior is not None:
            expected=(account['bank'],account['account_number'],'USD',*(source[k] for k in FIELDS))
            if any((Decimal(str(a))!=b if isinstance(b,Decimal) else str(a)!=str(b)) for a,b in zip(prior,expected)):raise ValueError('Existing AMEX balance conflicts; nothing overwritten.')
            conn.rollback();return 'already recorded'
        now=datetime.now(timezone.utc).isoformat(timespec='seconds')
        audit=json.dumps(dict(action=ACTION,actor=str(actor),source_hash=digest,confirmed_at=now,transaction_changes=0,setup_id=account['id'],source=source,csv_evidence=evidence),default=str,separators=(',',':'))
        if header is None:cur.execute('INSERT INTO statement_imports(statement_hash,statement_name,imported_at,transaction_count) VALUES(?,?,?,0)',(digest,filename,now))
        if not db.save_statement_balance(digest,filename,dict(source,notes=audit),account,_connection=conn):raise ValueError('AMEX balance metadata was not saved.')
        if not verified_record(conn,audit,account['bank'],account['account_number'],'USD',*(source[k] for k in FIELDS[2:]),digest,source['period_start'],source['period_end']):raise ValueError('AMEX metadata proof failed; transaction rolled back.')
        conn.commit();return 'recorded'
    except Exception:conn.rollback();raise
    finally:conn.close()

def render(st,content,filename):
    digest=sha256(content).hexdigest()
    st.info('Use the Screen Reader Optimized PDF. AMEX PDF: balance/date metadata only. Continue using CSV for transactions. Upload the corresponding already-imported original CSV as account identity evidence only.')
    evidence=st.file_uploader('Already-imported AMEX CSV — identity evidence only; no transactions imported',type=['csv'],key='amex_balance_csv_'+digest)
    if evidence is None:
        st.button('Import AMEX Balance Metadata',key='amex_balance_record',disabled=True)
        st.caption('Upload the corresponding already-imported CSV to validate the account and enable this action. No CSV transactions are imported here.')
        return True
    csv_content=evidence.getvalue();identity=digest+sha256(csv_content).hexdigest()
    if st.session_state.get('amex_balance_identity')!=identity:
        st.session_state['amex_balance_identity']=identity;st.session_state.pop('amex_balance_preview',None);st.session_state.pop('amex_balance_result',None)
    if st.session_state.get('amex_balance_result'):st.success(st.session_state['amex_balance_result']);return True
    if not st.session_state.get('amex_balance_preview'):
        try:
            import db
            source,_,account,_=prepare(db,content,csv_content)
            st.session_state['amex_balance_preview']={'Account':account['account_name'],'Setup ID':str(account['id']),'Card ending':source['card_ending'],**{key:str(source[key]) for key in FIELDS}}
        except ValueError as error:st.error(str(error));return True
        except Exception:st.error('AMEX source/history verification unavailable. No data changed.');return True
    preview=st.session_state.get('amex_balance_preview')
    if preview:
        st.table([preview]);confirmed=st.checkbox('Confirm PDF balances only — no PDF or CSV transactions will be imported.',key='amex_balance_confirm_'+identity)
        if st.button('Import AMEX Balance Metadata',key='amex_balance_record',disabled=not confirmed):
            try:
                import db
                from auth import get_login_user
                if not st.session_state.get('authenticated'):raise ValueError('Sign in before confirming.')
                outcome=record(db,content,filename,csv_content,get_login_user())
                st.session_state['amex_balance_result']='Balances already recorded; no changes made.' if outcome=='already recorded' else 'AMEX PDF balances recorded. Zero PDF/CSV transactions imported; existing transactions unchanged.'
                st.cache_data.clear();st.rerun()
            except ValueError as error:st.error(str(error))
            except Exception:st.error('AMEX balance-only operation failed; no operation committed.')
    return True
