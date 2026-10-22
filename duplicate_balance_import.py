"""Explicit source-verified balance completion; transaction duplicate guard stays authoritative."""
from collections import Counter
from datetime import datetime,timezone
from decimal import Decimal
from hashlib import sha256
from io import BytesIO
import json,re

FIELDS=('period_start','period_end','opening_balance','money_in','money_out','closing_balance')


class BalanceImportError(ValueError):pass


def prepare(db,content,filename):
    from parsing import parse_pdf
    frame=parse_pdf(BytesIO(content));balance=frame.attrs.get('statement_balance',{})
    if balance.get('source')=='BOC bank columns':
        from boc_summary_balances import prepare as prepare_boc
        frame,balance=prepare_boc(content)
        return frame,balance,None
    if frame.attrs.get('revolut_source_account') or balance.get('source')=='CNB Account Summary':
        from completed_balance_sources import prepare as prepare_completed
        return prepare_completed(db,frame)
    if balance.get('source')!='Russian Revolut Business':
        raise BalanceImportError('This separate action requires source-verified BOC or Revolut Business balance metadata. No data changed.')
    compact=lambda value:re.sub(r'[\s.-]','',str(value)).upper()
    candidates=db.get_accounts()
    candidates=candidates[candidates.bank.map(compact).isin(('REVOLUT','REVOLUTBANK'))
        & candidates.account_number.map(compact).eq(balance['account_number'])
        & candidates.currency.fillna('').str.upper().eq(balance['currency'])]
    if len(candidates)!=1:raise BalanceImportError('Exactly one matching existing Revolut account and source currency are required.')
    account=candidates.iloc[0].to_dict()
    frame=frame.copy();frame['account_number']=account['account_number'];frame['currency']=account['currency']
    from revolut_localized import validate_preview
    validate_preview(frame,balance,account)
    return frame,balance,account


def record(db,content,filename,actor):
    if not actor or not str(filename).strip():raise BalanceImportError('Authenticated source confirmation is required.')
    frame,balance,account=prepare(db,content,filename)
    if account is None:
        from boc_summary_balances import record as record_boc
        return record_boc(db,content,filename,actor)
    digest=sha256(content).hexdigest();conn=db.get_connection()
    try:
        cur=conn.cursor()
        if not db.USING_POSTGRES:cur.execute('BEGIN IMMEDIATE')
        lock=' FOR UPDATE' if db.USING_POSTGRES else ''
        cur.execute('SELECT id,transaction_count,imported_at FROM statement_imports WHERE statement_hash=?'+lock,(digest,));header=cur.fetchone()
        cur.execute('SELECT account_name,bank,account_number,currency FROM account_list WHERE id=?'+(' FOR SHARE' if db.USING_POSTGRES else ''),(account['id'],))
        current_account=cur.fetchone()
        if current_account is None or tuple(current_account)!=tuple(account.get('_setup_identity',tuple(account[key] for key in ('account_name','bank','account_number','currency')))):
            raise BalanceImportError('Setup account changed since source validation; reload before recording balances.')
        cur.execute('SELECT id,bank,account_number,currency,account_name,period_start,period_end,opening_balance,money_in,money_out,closing_balance,notes FROM statement_balances WHERE statement_hash=?'+lock,(digest,));prior=cur.fetchone()
        if header is None:
            if len(frame) or prior is not None:raise BalanceImportError('Import the source transactions normally first; no transaction import is bypassed.')
        else:
            cur.execute('SELECT txn_date,amount,original_description,bank,account_number,currency FROM classified_transactions WHERE statement_hash=? AND split_parent_id IS NULL'+(' FOR SHARE' if db.USING_POSTGRES else ''),(digest,))
            stored=cur.fetchall()
            expected=Counter((str(r['Date']),Decimal(str(r['Amount'])),r['Description'],account['bank'],account['account_number'],account['currency']) for r in frame.to_dict('records'))
            actual=Counter((str(r[0]),Decimal(str(r[1])),r[2],r[3],r[4],r[5]) for r in stored)
            if header[1]!=len(frame) or actual!=expected:raise BalanceImportError('Stored source transactions do not match the validated statement; no balance recorded.')
        changes={}
        if prior is not None:
            for index,key in ((1,'bank'),(2,'account_number'),(3,'currency'),(4,'account_name')):
                if prior[index] not in (None,'') and prior[index]!=account[key]:raise BalanceImportError('Stored balance account identity conflicts; no balance recorded.')
                if prior[index] in (None,''):changes[key]=account[key]
            for index,key in enumerate(FIELDS,5):
                value=prior[index]
                if value not in (None,''):
                    if (str(value)!=str(balance[key]) if key.startswith('period_') else Decimal(str(value))!=balance[key]):raise BalanceImportError('Existing balance metadata conflicts; no balance overwritten.')
                else:changes[key]=balance[key]
            if not changes:conn.rollback();return 'already recorded'
        now=datetime.now(timezone.utc).isoformat(timespec='seconds')
        action={'Russian Revolut Business':'user-confirmed Revolut balances only',
                'English Revolut Completed Source':'user-confirmed English Revolut balances only',
                'CNB Account Summary':'user-confirmed CNB balances only'}[balance['source']]
        audit=json.dumps(dict(action=action,actor=str(actor),source_hash=digest,confirmed_at=now,transaction_changes=0),separators=(',',':'))
        if prior is None:
            if header is None:
                cur.execute('INSERT INTO statement_imports(statement_hash,statement_name,imported_at,transaction_count) VALUES(?,?,?,0)',(digest,filename,now))
            if not db.save_statement_balance(digest,filename,dict(balance,notes=audit),account,_connection=conn):raise BalanceImportError('Balance record was not saved.')
        else:
            changes.update(source=balance['source'],notes=(str(prior[11] or '')+'\n'+audit).strip(),updated_at=now)
            cur.execute('UPDATE statement_balances SET '+','.join(key+'=?' for key in changes)+' WHERE id=? AND statement_hash=?',(*changes.values(),prior[0],digest))
            if cur.rowcount!=1:raise BalanceImportError('Balance metadata changed concurrently.')
        conn.commit();return 'recorded'
    except Exception:
        conn.rollback();raise
    finally:conn.close()


def render_controls(st,content,filename):
    if not filename.lower().endswith('.pdf'):return
    digest=sha256(content).hexdigest()
    if st.session_state.get('balance_amex_gate_hash')!=digest:
        from amex_balance_metadata import recognized
        try:is_amex=recognized(content)
        except Exception:is_amex=False
        st.session_state['balance_amex_gate_hash']=digest;st.session_state['balance_amex_gate']=is_amex
    if st.session_state.get('balance_amex_gate'):
        from amex_balance_metadata import render as render_amex
        return render_amex(st,content,filename)
    if st.session_state.get('upload_balance_hash')!=digest:
        st.session_state['upload_balance_hash']=digest
        st.session_state.pop('upload_balance_preview',None);st.session_state.pop('upload_balance_result',None)
    if st.session_state.get('upload_balance_result'):
        st.success(st.session_state['upload_balance_result']);return
    if st.button('Import Balances Only',key='upload_balance_prepare'):
        try:
            import db
            _,balance,_=prepare(db,content,filename)
            st.session_state['upload_balance_preview']={key:str(balance[key]) for key in ('account_number','currency',*FIELDS)}
        except Exception:
            st.session_state.pop('upload_balance_preview',None)
            st.error('Balance-only source/account validation failed. No data changed. A reconciled supported statement is required.')
    preview=st.session_state.get('upload_balance_preview')
    if not preview:return
    st.table([{'Field':key,'Source value':value} for key,value in preview.items()])
    st.caption('Balance metadata only. Exact existing source transactions must match; duplicate transactions remain blocked. Conflicting stored balances cannot be overwritten.')
    confirmed=st.checkbox('I confirm these balances only; do not import transactions.',key='upload_balance_confirm_'+digest)
    if st.button('Confirm Balance-Only Import',key='upload_balance_record',disabled=not confirmed):
        if not st.session_state.get('authenticated'):st.error('Sign in before confirming.');return
        try:
            import db
            from auth import get_login_user
            outcome=record(db,content,filename,get_login_user())
        except BalanceImportError as error:st.error(str(error));return
        except Exception:st.error('Balance-only import failed. No operation was committed.');return
        st.session_state.pop('upload_balance_preview',None)
        st.session_state['upload_balance_result']='Balances already recorded; no changes made. No new balance-import event was created; Balance Import Date remains the existing recorded import date.' if outcome=='already recorded' else 'Balances recorded. No transactions or classifications changed.'
        st.cache_data.clear();st.rerun()
