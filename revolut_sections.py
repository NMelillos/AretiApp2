"""Source-bound Greek wallet sections; no Setup writes or schema migration."""
from decimal import Decimal as D
from hashlib import sha256
import json
import re
import pandas as pd
from revolut_greek import parse, GreekRevolutError


def require(ok, reason):
    if not ok:
        raise GreekRevolutError('Greek Revolut wallet validation failed: ' + reason)


def compact(value):
    return re.sub(r'[^\w]', '', str(value or '')).upper()


def movements(wallet):
    return [(t.date.isoformat(), t.description, t.amount) for t in wallet.transactions]


def wallet_key(wallet):
    identity = ['Greek Revolut wallet v1', wallet.iban, wallet.owner, wallet.currency,
                wallet.start.isoformat(), wallet.end.isoformat(),
                *[str(x) for x in (wallet.opening, wallet.credits, wallet.debits, wallet.closing)],
                [(d, text, str(amount)) for d, text, amount in movements(wallet)]]
    return sha256(json.dumps(identity, ensure_ascii=True).encode()).hexdigest()


def from_document(pages, document_hash):
    wallets = parse(pages)  # all wallets must validate before any preview exists
    frames = []
    for wallet in wallets:
        rows = [dict(Date=d, Description=text, Amount=amount, statement_currency=wallet.currency,
                     currency=wallet.currency, statement_currency_source='Greek Revolut wallet',
                     revolut_wallet=wallet.currency) for d, text, amount in movements(wallet)]
        frames.append(pd.DataFrame(rows))
    frame = pd.concat(frames, ignore_index=True)
    frame.attrs.update(revolut_sections=wallets, revolut_document_hash=document_hash,
                       parse_diagnostics=dict(pending_rows=sum(w.pending_count for w in wallets),
                                              reverted_rows=sum(w.reverted_count for w in wallets)))
    return frame


def resolved(frame, accounts):
    wallets = frame.attrs.get('revolut_sections', ())
    require(bool(wallets) and len({w.currency for w in wallets}) == len(wallets), 'wallet population')
    require(len({(w.iban, w.owner, w.start, w.end) for w in wallets}) == 1, 'physical source identity')
    require(set(frame.revolut_wallet) == {w.currency for w in wallets}, 'wallet rows')
    result = {}
    for w in wallets:
        matches = [a for a in accounts.to_dict('records')
                   if compact(a.get('account_number')) == w.iban
                   and compact(a.get('account_name')) == compact(w.owner)
                   and compact(a.get('bank')) in ('REVOLUT', 'REVOLUTBANK', 'REVOLUTBANKUAB')]
        require(len(matches) == 1, 'exact unique physical account required')
        account = dict(matches[0])
        configured = str(account.get('currency') or '').strip().upper()
        require(not configured or configured == w.currency, 'Setup currency conflicts with source wallet')
        account.update(currency=w.currency, rate_type=w.currency+'/USD')
        rows = frame[frame.revolut_wallet.eq(w.currency)]
        actual = [(str(r.Date), str(r.Description), D(str(r.Amount))) for r in rows.itertuples()]
        require(actual == movements(w), 'completed wallet movements changed')
        require(all(str(r.statement_currency) == w.currency and str(r.currency) == w.currency
                    for r in rows.itertuples()), 'cross-wallet currency')
        for field in ('account_name', 'bank', 'account_number'):
            if field in rows:
                require(all(compact(v) == compact(account[field]) for v in rows[field]), 'effective account changed')
        require(w.opening+w.credits-w.debits == w.closing, 'summary reconciliation')
        require(sum((a for _,_,a in actual if a>0), D(0)) == w.credits
                and -sum((a for _,_,a in actual if a<0), D(0)) == w.debits, 'activity reconciliation')
        result[w.currency] = account
    return result


def apply_accounts(db, frame):
    accounts = resolved(frame, db.get_accounts())
    parts = []
    for wallet in frame.attrs['revolut_sections']:
        rows = frame[frame.revolut_wallet.eq(wallet.currency)].copy()
        rows.attrs.clear()
        parts.append(db.apply_account_and_rates(rows, accounts[wallet.currency]))
    result = pd.concat(parts, ignore_index=True)
    result.attrs.update(frame.attrs)
    return result


def preview_sections(frame):
    return pd.DataFrame([dict(Owner=w.owner, IBAN=w.iban, Currency=w.currency,
        **{'Period start':w.start.isoformat(), 'Period end':w.end.isoformat(),
           'Opening balance':w.opening, 'Money in':w.credits, 'Money out':w.debits,
           'Closing balance':w.closing, 'Transactions':len(w.transactions)})
        for w in frame.attrs['revolut_sections']])


def save_sections(db, frame, name, document_hash):
    require(bool(name) and re.fullmatch(r'[0-9a-f]{64}', document_hash), 'document identity')
    require(frame.attrs.get('revolut_document_hash') == document_hash, 'source provenance changed')
    accounts = resolved(frame, db.get_accounts())
    prepared = []
    for w in frame.attrs['revolut_sections']:
        rows = frame[frame.revolut_wallet.eq(w.currency)].copy()
        rows.attrs.clear()
        balance = dict(period_start=w.start.isoformat(), period_end=w.end.isoformat(),
            opening_balance=w.opening, money_in=w.credits, money_out=w.debits,
            closing_balance=w.closing, currency=w.currency,
            source='Greek Revolut document '+document_hash,
            notes=json.dumps(dict(document_sha256=document_hash, wallet_currency=w.currency,
                                  iban=w.iban, owner=w.owner, pending_excluded=w.pending_count,
                                  reversed_excluded=w.reverted_count)))
        prepared.append((wallet_key(w), rows, balance, accounts[w.currency]))
    conn = db.get_connection()
    cur = conn.cursor()
    try:
        if db.USING_POSTGRES:
            lock = int(sha256('|'.join(sorted(p[0] for p in prepared)).encode()).hexdigest()[:15],16)
            cur.execute('SELECT pg_advisory_xact_lock(?)', (lock,))
        else:
            cur.execute('BEGIN IMMEDIATE')
        existing = []
        for key,rows,balance,account in prepared:
            counts=[]
            for table in ('statement_imports','statement_balances','classified_transactions'):
                cur.execute('SELECT COUNT(*) FROM '+table+' WHERE statement_hash = ?', (key,))
                counts.append(cur.fetchone()[0])
            existing.append(counts)
        if any(any(counts) for counts in existing):
            require(all(counts == [1,1,len(item[1])] for counts,item in zip(existing,prepared)),
                    'partial existing wallet state; no changes saved')
            for key,rows,balance,account in prepared:
                cur.execute('SELECT transaction_count FROM statement_imports WHERE statement_hash = ?', (key,))
                require(cur.fetchone()[0] == len(rows), 'existing wallet history incomplete')
                cur.execute('SELECT txn_date, original_description, amount, currency, account_number FROM classified_transactions WHERE statement_hash = ? ORDER BY id', (key,))
                expected=[(str(r.Date),str(r.Description),D(str(r.Amount)),balance['currency'],account['account_number']) for r in rows.itertuples()]
                actual=[(str(d),text,D(str(a)),currency,iban) for d,text,a,currency,iban in cur.fetchall()]
                require(actual == expected, 'existing wallet transactions differ')
                cur.execute('SELECT opening_balance,money_in,money_out,closing_balance,currency,period_start,period_end,account_number FROM statement_balances WHERE statement_hash = ?', (key,))
                b=cur.fetchone()
                require(tuple(D(str(v)) for v in b[:4]) == tuple(balance[k] for k in ('opening_balance','money_in','money_out','closing_balance'))
                        and tuple(b[4:]) == (balance['currency'],balance['period_start'],balance['period_end'],account['account_number']), 'existing wallet balance differs')
            conn.rollback()
            return 0, True, 0
        cur.execute('SELECT COUNT(*) FROM statement_imports WHERE statement_hash = ?', (document_hash,))
        require(cur.fetchone()[0] == 0, 'legacy document history overlaps wallets')
        inserted=0
        for key,rows,balance,account in prepared:
            count,duplicate,skipped=db.save_pending_transactions(rows,name,key,_connection=conn)
            require(not duplicate and not skipped and count == len(rows), 'transaction overlap; no partial import')
            require(db.save_statement_balance(key,name,balance,account,_connection=conn), 'wallet balance was not saved')
            inserted+=count
        conn.commit()
        return inserted, False, 0
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
        conn.close()
