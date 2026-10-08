"""Explicit user-confirmed BOC metadata action; never inserts transaction rows."""
from datetime import datetime, timezone
from decimal import Decimal
from hashlib import sha256
from io import BytesIO
import json

class BalanceOnlyError(ValueError):
    pass


FIELDS=('period_start','period_end','opening_balance','money_in','money_out','closing_balance')


def prepare(content):
    """Use the established source-column proof, not unchecked preview/session values."""
    from statement_summary import extract_pdf
    summary=extract_pdf(content)
    if summary.bank != 'Bank of Cyprus' or not all(getattr(summary,k).verified for k in ('currency','period_start','period_end','opening_balance','closing_balance')):
        raise BalanceOnlyError('A complete source-verified BOC summary is required.')
    from parsing import parse_pdf
    frame=parse_pdf(BytesIO(content))
    balance=frame.attrs.get('statement_balance',{})
    if balance.get('source') != 'BOC bank columns':
        raise BalanceOnlyError('BOC bank-column reconciliation is required.')
    for key in ('period_start','period_end'):
        if balance[key] != getattr(summary,key).value: raise BalanceOnlyError('Source fields conflict.')
    for key in ('opening_balance','closing_balance'):
        if balance[key] != Decimal(getattr(summary,key).value): raise BalanceOnlyError('Source balances conflict.')
    if balance['currency'] != summary.currency.value or balance['opening_balance']+balance['money_in']-balance['money_out'] != balance['closing_balance']:
        raise BalanceOnlyError('Statement balance summary does not reconcile.')
    return frame,balance


def record(db, content, filename, actor):
    """Fill only source-proved missing metadata after explicit UI confirmation.

    Existing exact-file transaction import stays blocked. No historical repair is
    executed at startup or deployment; this is a deliberate user's balance action.
    """
    if not actor or not str(filename).strip(): raise BalanceOnlyError('Authenticated confirmation is required.')
    frame,balance=prepare(content)
    digest=sha256(content).hexdigest()
    accounts=db.get_accounts()
    candidates=accounts[accounts.bank.fillna('').str.replace(r'[\s.-]','',regex=True).str.upper().isin(['BOC','BANKOFCYPRUS'])
        & accounts.account_number.astype(str).str.strip().eq(balance['account_number'])
        & accounts.currency.astype(str).str.upper().eq(balance['currency'])]
    if len(candidates)!=1: raise BalanceOnlyError('Exactly one matching existing BOC Setup account is required.')
    account=candidates.iloc[0].to_dict()
    frame=db.apply_account_and_rates(frame,account)
    from boc_import import validate_preview
    validate_preview(frame,balance,account)
    conn=db.get_connection()
    try:
        cur=conn.cursor()
        if not db.USING_POSTGRES: cur.execute('BEGIN IMMEDIATE')
        lock=' FOR UPDATE' if db.USING_POSTGRES else ''
        cur.execute('SELECT id, transaction_count, imported_at FROM statement_imports WHERE statement_hash=?'+lock,(digest,))
        header=cur.fetchone()
        cur.execute('SELECT id,bank,account_number,currency,account_name,period_start,period_end,opening_balance,money_in,money_out,closing_balance,notes FROM statement_balances WHERE statement_hash=?'+lock,(digest,))
        prior=cur.fetchone()
        if header is None and not frame.empty:
            raise BalanceOnlyError('Source transactions must already be imported. Use normal Upload for transaction imports.')
        if header is not None:
            cur.execute('SELECT COUNT(*) FROM classified_transactions WHERE statement_hash=? AND split_parent_id IS NULL',(digest,))
            if cur.fetchone()[0] != header[1] or header[1] != len(frame):
                raise BalanceOnlyError('Stored source transaction count does not match; no metadata recorded.')
            if not frame.empty:
                # Restrict the proof to this exact import, rather than other sources.
                cur.execute('SELECT txn_date,amount,original_description,bank,account_number,currency FROM classified_transactions WHERE statement_hash=? AND split_parent_id IS NULL'+(' FOR SHARE' if db.USING_POSTGRES else ''),(digest,))
                stored=list(cur.fetchall())
                for row in frame.to_dict('records'):
                    match=next((item for item in stored if str(item[0])==str(row['Date']) and Decimal(str(item[1]))==Decimal(str(row['Amount'])) and item[2]==row['Description'] and item[3]==account['bank'] and item[4]==account['account_number'] and item[5]==account['currency']),None)
                    if match is None: raise BalanceOnlyError('Stored source rows do not match the verified PDF; no metadata recorded.')
                    stored.remove(match)
        elif prior is not None:
            raise BalanceOnlyError('Orphan balance metadata requires separate review.')
        if prior is not None:
            if any(str(prior[index] or '') not in ('',str(account[key])) for index,key in ((1,'bank'),(2,'account_number'),(3,'currency'),(4,'account_name'))):
                raise BalanceOnlyError('Existing balance account identity conflicts; no metadata recorded.')
            existing=dict(zip(FIELDS,prior[5:11]))
            for key,value in existing.items():
                if value not in (None,''):
                    expected=balance[key]
                    if (str(value)!=str(expected) if key.startswith('period_') else Decimal(str(value))!=expected):
                        raise BalanceOnlyError('Existing balance metadata conflicts; no metadata recorded.')
            if all(value not in (None,'') for value in existing.values()) and all(prior[index] not in (None,'') for index in (1,2,3,4)):
                conn.rollback();return 'already recorded'
        now=datetime.now(timezone.utc).isoformat(timespec='seconds')
        audit=json.dumps(dict(action='user-confirmed BOC balances only',actor=str(actor),source_hash=digest,confirmed_at=now,transaction_changes=0),separators=(',',':'))
        if prior is None:
            if header is None:
                cur.execute('INSERT INTO statement_imports(statement_hash,statement_name,imported_at,transaction_count) VALUES(?,?,?,0)',(digest,filename,now))
            balance=dict(balance,notes=audit)
            if not db.save_statement_balance(digest,filename,balance,account,_connection=conn):raise BalanceOnlyError('Balance record was not saved.')
        else:
            # Keep import ID/count/hash/timestamp and old audit evidence unchanged.
            notes=(str(prior[11] or '')+'\n'+audit).strip()
            assignments=','.join(k+'=?' for k in FIELDS)
            cur.execute('UPDATE statement_balances SET '+assignments+', bank=?, account_number=?, currency=?, account_name=?, source=?, notes=?, updated_at=? WHERE id=? AND statement_hash=?',tuple(balance[k] for k in FIELDS)+(account['bank'],account['account_number'],account['currency'],account['account_name'],'BOC bank columns',notes,now,prior[0],digest))
            if cur.rowcount!=1:raise BalanceOnlyError('Balance record changed concurrently.')
        conn.commit();return 'recorded'
    except Exception:
        conn.rollback();raise
    finally:conn.close()
