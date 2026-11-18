"""Atomic AMEX CSV transactions plus source-proven screen-reader metadata."""
import csv
import json
import re
from collections import Counter
from contextlib import closing
from datetime import datetime
from decimal import Decimal
from hashlib import sha256
from io import BytesIO, StringIO

import amex_balance_metadata as metadata

SOURCE = 'AMEX paired statement'
ACCOUNT_FIELDS = ('id', 'account_name', 'bank', 'account_number', 'currency', 'rate_type')


def require(condition, message):
    if not condition:
        raise ValueError('AMEX source-pair validation: ' + message)


def source_pair(pdf, csv_bytes):
    """PDF member summaries/date coverage validate CSV; PDF creates no movements."""
    from parsing import parse_csv
    pages = metadata.texts(pdf)
    source = metadata.parse(pdf, pages=pages)
    evidence = metadata.csv_evidence(csv_bytes, source)
    reader = csv.DictReader(StringIO(csv_bytes.decode('utf-8-sig')))
    require(len(reader.fieldnames) == len(set(reader.fieldnames)), 'duplicate CSV columns.')
    original = list(reader)
    require(all(None not in r and all(str(r.get(k) or '').strip() for k in
                ('Date', 'Description', 'Amount', 'Card Member', 'Account #')) for r in original),
            'incomplete CSV rows.')
    number = r'(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d{1,2})?'
    require(all(re.fullmatch(r'(?:[+-]?\$?'+number+r'|\(\$?'+number+r'\))', r['Amount'].strip())
                for r in original), 'malformed CSV amount.')
    frame = parse_csv(BytesIO(csv_bytes))
    require(len(frame) == len(original) and not frame.empty, 'CSV row count changed while parsing.')
    require(all(isinstance(v, Decimal) and v.is_finite() for v in frame.Amount), 'invalid amounts.')
    require('statement_currency' not in frame or frame.statement_currency.eq('USD').all(), 'USD required.')
    text = '\n'.join(pages)
    summaries = {}
    money = r'(-?\$[\d,]+\.\d{2})'
    for kind, member, card, pif, pot, total in re.findall(
            r'(?m)^(Credits|New Charges) (.+?) (\d-\d{5}) '+money+' '+money+' '+money+r'\s*$', text):
        key = (metadata.compact(member), metadata.compact(card)[-5:])
        values = [Decimal(v.replace('$', '').replace(',', '')) for v in (pif, pot, total)]
        require(values[0] + values[1] == values[2], 'member summary does not reconcile.')
        require(kind not in summaries.setdefault(key, {}), 'ambiguous member summary.')
        summaries[key][kind] = values[2]
    require(summaries and all(set(v) == {'Credits', 'New Charges'} for v in summaries.values()),
            'missing member/card summary.')
    primary = (metadata.compact(source['holder']), metadata.compact(source['card_ending'])[-5:])
    require(primary in summaries, 'primary card absent from summary.')
    payments = re.findall(r'(?m)^Payments '+money+' '+money+' '+money+r'\s*$', text)
    require(len(payments) == 1, 'payment summary missing or ambiguous.')
    payment_values = [Decimal(v.replace('$', '').replace(',', '')) for v in payments[0]]
    require(payment_values[0] + payment_values[1] == payment_values[2] <= 0, 'payment summary invalid.')
    require(re.search(r'Payments Details\s+\*Indicates posting date\s+' +
                     re.escape(source['holder']) + r'\s+Card Ending ' + re.escape(source['card_ending']), text),
            'payment member identity missing.')
    # Dates here are coverage evidence only, never an alternate PDF transaction parser.
    dates = {}; member = None; card = None
    details = text.split('Payments Details', 1)[1].split('\nFees\n', 1)[0]
    lines = details.splitlines()
    for i, line in enumerate(lines):
        match = re.fullmatch(r'Card Ending (\d-\d{5})', line.strip())
        if match and i:
            member = metadata.compact(lines[i-1]); card = metadata.compact(match[1])[-5:]
        date = re.match(r'^(\d{2}/\d{2}/\d{2})(?:\*|\s)', line)
        if date:
            require((member, card) in summaries, 'dated activity has unknown member/card.')
            dates.setdefault((member, card), set()).add(datetime.strptime(date[1], '%m/%d/%y').date().isoformat())
    movements = {}
    for row in frame.itertuples():
        key = (metadata.compact(row.card_member), metadata.compact(row.source_account_number))
        require(key in summaries, 'CSV member/card not proven by PDF.')
        require(row.Date in dates.get(key, set()) and row.Date <= source['period_end'],
                'CSV transaction date not covered by this statement.')
        incoming, outgoing = movements.setdefault(key, [Decimal(0), Decimal(0)])
        movements[key] = [incoming + max(row.Amount, Decimal(0)), outgoing - min(row.Amount, Decimal(0))]
    for key, summary in summaries.items():
        credits = -summary['Credits'] - (payment_values[2] if key == primary else 0)
        require(summary['Credits'] <= 0 and summary['New Charges'] >= 0, 'member signs invalid.')
        require(movements.get(key, [Decimal(0), Decimal(0)]) == [credits, summary['New Charges']],
                'CSV member activity differs from PDF summary.')
    require(sum((v[0] for v in movements.values()), Decimal(0)) == source['money_in'] and
            -sum((v[1] for v in movements.values()), Decimal(0)) == source['money_out'],
            'CSV activity differs from Account Total (including fees/interest).')
    require(source['opening_balance'] + sum(frame.Amount, Decimal(0)) == source['closing_balance'],
            'internal reconciliation failed.')
    return source, evidence, frame


def prepare(db, pdf, csv_bytes):
    from classification import classify_transactions
    source, evidence, frame = source_pair(pdf, csv_bytes)
    accounts = db.get_accounts().to_dict('records')
    candidates = []
    ending = re.sub(r'\D', '', source['card_ending'])
    for account in accounts:
        digits = re.sub(r'\D', '', metadata.base_number(account['account_number']))
        if (metadata.compact(account['bank']) in ('AMEX', 'AMERICANEXPRESS') and
                metadata.compact(account['account_name']) == metadata.compact(source['company']) and
                account['currency'] == 'USD' and
                ((len(digits) == 10 and digits[-1] == ending[0]) or
                 (len(digits) == 15 and digits.endswith(ending)))):
            candidates.append(account)
    require(len(candidates) == 1, 'exactly one Setup facility required.')
    account = candidates[0]
    mapped = db.apply_account_and_rates(frame, account)
    base = metadata.base_number(account['account_number'])
    for row in mapped.itertuples():
        require(row.currency == 'USD' and row.account_name == account['account_name'] and
                row.bank == account['bank'] and
                metadata.compact(row.account_number) == metadata.compact(base + ' ' + row.card_member),
                'Setup member resolution conflicts with source facility.')
    mapped = classify_transactions(mapped, db.get_memory(), taxonomy=db.get_categories(include_subcategories=True))
    identity = dict(pdf_hash=sha256(pdf).hexdigest(), csv_hash=evidence['csv_hash'],
                    account={k: account[k] for k in ACCOUNT_FIELDS})
    token = sha256(json.dumps(identity, sort_keys=True, default=str).encode()).hexdigest()
    return source, account, mapped, identity, token


def history_rows(frame):
    return Counter((str(r.Date), str(r.Description), Decimal(str(r.Amount)), r.currency,
                    r.account_name, r.bank, r.account_number) for r in frame.itertuples())


def record(db, pdf, pdf_name, csv_bytes, csv_name, actor, preview_token):
    require(bool(actor), 'authenticated confirmation required.')
    source, account, frame, identity, token = prepare(db, pdf, csv_bytes)
    require(token == preview_token, 'sources or Setup changed; preview again.')
    digest = identity['csv_hash']
    conn = db.get_connection()
    try:
        with closing(conn.cursor()) as cur:
            if not db.USING_POSTGRES:
                cur.execute('BEGIN IMMEDIATE')
            lock = ' FOR UPDATE' if db.USING_POSTGRES else ''
            cur.execute('SELECT '+','.join(ACCOUNT_FIELDS)+' FROM account_list WHERE id=?'+lock, (account['id'],))
            require(tuple(cur.fetchone() or ()) == tuple(account[k] for k in ACCOUNT_FIELDS), 'Setup changed; preview again.')
            cur.execute('SELECT transaction_count FROM statement_imports WHERE statement_hash=?', (digest,))
            header = cur.fetchone()
            cur.execute('SELECT txn_date,original_description,amount,currency,account_name,bank,account_number '
                        'FROM classified_transactions WHERE statement_hash=? AND split_parent_id IS NULL', (digest,))
            rows = cur.fetchall()
            cur.execute('SELECT statement_hash,notes,opening_balance,money_in,money_out,closing_balance '
                        'FROM statement_balances WHERE statement_hash IN (?,?) OR '
                        '(bank=? AND account_number=? AND currency=? AND period_start=? AND period_end=?)',
                        (digest, identity['pdf_hash'], account['bank'], account['account_number'], 'USD',
                         source['period_start'], source['period_end']))
            balances = cur.fetchall()
            if header or rows or balances:
                require(header is not None and header[0] == len(frame) == len(rows) and len(balances) == 1,
                        'partial or conflicting history; no records changed. Use verified recovery for prior CSV imports.')
                balance = balances[0]
                try:
                    audit = json.loads(balance[1])
                except (ValueError, TypeError):
                    audit = {}
                require(balance[0] == digest and audit.get('identity') == identity and
                        audit.get('action') == SOURCE and
                        tuple(Decimal(str(v)) for v in balance[2:]) == tuple(source[k] for k in metadata.FIELDS[2:]) and
                        Counter(tuple(Decimal(str(v)) if i == 2 else str(v) for i,v in enumerate(row)) for row in rows) == history_rows(frame),
                        'source pair differs from completed history.')
                conn.rollback()
                return 'already imported'
            audit = dict(action=SOURCE, identity=identity, actor=str(actor), pdf_filename=pdf_name,
                         csv_filename=csv_name, transaction_count=len(frame), source=source)
            result = db.save_pending_transactions(frame, csv_name, digest, _connection=conn, _batch=True)
            require(result == (len(frame), False, 0), 'overlapping or duplicate transaction history; nothing imported.')
            balance = dict(source, source=SOURCE, notes=json.dumps(audit, default=str, separators=(',', ':')))
            require(db.save_statement_balance(digest, pdf_name, balance, account, _connection=conn), 'balance save failed.')
            cur.execute('SELECT transaction_count FROM statement_imports WHERE statement_hash=?', (digest,))
            require(cur.fetchone()[0] == len(frame), 'Import History incomplete.')
        conn.commit()
        return 'imported'
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def render(st, pdf, filename):
    import db
    st.info('AMEX: CSV supplies transactions; Screen Reader Optimized PDF supplies statement identity and balances.')
    csv_file = st.file_uploader('Corresponding AMEX CSV', type=['csv'], key='amex_pair_csv_'+sha256(pdf).hexdigest())
    if csv_file is None:
        return True
    raw = csv_file.getvalue()
    # Preserve the separate exact-history recovery workflow, without making it a prerequisite.
    with closing(db.get_connection()) as conn, closing(conn.cursor()) as cur:
        cur.execute('SELECT transaction_count FROM statement_imports WHERE statement_hash=?', (sha256(raw).hexdigest(),))
        prior = cur.fetchone()
        cur.execute('SELECT source FROM statement_balances WHERE statement_hash=?', (sha256(raw).hexdigest(),))
        paired = any(row[0] == SOURCE for row in cur.fetchall())
    if prior and not paired:
        st.caption('Completed CSV found: historical balance recovery only; no transactions will be imported.')
        return metadata.render_recovery(st, pdf, filename, csv_file)
    key = sha256(pdf + raw).hexdigest()
    if st.session_state.get('amex_pair_identity') != key:
        st.session_state['amex_pair_identity'] = key
        st.session_state.pop('amex_pair_preview', None)
    try:
        if 'amex_pair_preview' not in st.session_state:
            st.session_state['amex_pair_preview'] = prepare(db, pdf, raw)
        source, account, frame, identity, token = st.session_state['amex_pair_preview']
        st.table([dict(Account=account['account_name'], Member=source['holder'], Card=source['card_ending'],
                       Currency='USD', Transactions=len(frame), **{k: str(source[k]) for k in metadata.FIELDS})])
        st.dataframe(frame, hide_index=True)
        confirmed = st.checkbox('Confirm new AMEX statement import', key='amex_pair_confirm_'+key)
        if st.button('Import AMEX statement', disabled=not confirmed):
            require(st.session_state.get('authenticated'), 'sign in before importing.')
            from auth import get_login_user
            result = record(db, pdf, filename, raw, csv_file.name, get_login_user(), token)
            st.success('Already imported; no changes.' if result == 'already imported' else
                       'AMEX statement imported: transactions pending review, balances and Import History saved together.')
            st.cache_data.clear()
    except ValueError as error:
        st.error(str(error))
    except Exception:
        st.error('AMEX source-pair operation failed; no import committed.')
    return True
