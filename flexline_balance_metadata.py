"""Explicit Woking Way Flexline closing metadata; never import statement activity."""
from datetime import datetime, timezone
from decimal import Decimal
from hashlib import sha256
from io import BytesIO
import json
import re

SOURCE = 'Fifth Third Flexline closing metadata only'
ACTION = 'user-confirmed Flexline closing metadata only'
SOURCE_ACCOUNT = '00548546519'
SETUP_ACCOUNT = '05590000991CREDITLINE00548546519'


def compact(value):
    return re.sub(r'[^A-Z0-9]', '', str(value or '').upper())


def first_page(content):
    import pdfplumber
    with pdfplumber.open(BytesIO(content)) as document:
        return document.pages[0].extract_text() or ''


def recognized(content):
    text = compact(first_page(content))
    return 'FIFTHTHIRDEQUITYFLEXLINE' in text and 'LINEOFCREDITSTATEMENT' in text


def parse(content):
    text = first_page(content)
    if not ('FIFTHTHIRDEQUITYFLEXLINE' in compact(text)
            and 'LINEOFCREDITSTATEMENT' in compact(text)):
        raise ValueError('Use the Fifth Third Equity Flexline Line of Credit statement.')
    def one(pattern):
        values = re.findall(pattern, text, re.MULTILINE)
        if len(values) != 1:
            raise ValueError('Flexline statement identity/date/new balance is missing or ambiguous.')
        return values[0]
    number = one(r'^ACCOUNT\s*NUMBER\s+(\d{11})\s*$')
    if number != SOURCE_ACCOUNT or len(re.findall(r'\bWOKING WAY LLC\b', text)) != 1:
        raise ValueError('Only the source-proven Woking Way Flexline account is supported.')
    when = datetime.strptime(one(r'^STATEMENT\s*DATE\s+([A-Z]+ \d{2}, \d{4})\s*$'), '%B %d, %Y').date()
    value = one(r'^(?:WOKING WAY LLC\s+)?NEW\s*BALANCE\s+(-?(?:\d{1,3}(?:,\d{3})+|\d+)\.\d{2})\s*$')
    # This positively identified facility's New Balance is debt. Preserve an
    # explicit negative source sign; never negate an already-negative liability.
    closing = -abs(Decimal(value.replace(',', '')))
    # No billing-period start, opening balance, payment or finance charge is
    # inferred. The approved operation records these two labelled values only.
    return dict(source=SOURCE, account_number=number, period_end=when.isoformat(),
                closing_balance=closing)


def prepare(db, content):
    source = parse(content)
    choices = [row for row in db.get_accounts().to_dict('records')
               if compact(row['account_name']) == 'WOKINGWAYLLC'
               and compact(row['bank']) == 'FIFTHTHIRDEXCOMERICA'
               and compact(row['account_number']) == SETUP_ACCOUNT
               and row['currency'] == 'USD']
    if len(choices) != 1:
        raise ValueError('Exactly one approved Woking Way USD Setup account containing both credit-line identifiers is required.')
    return source, choices[0]


def record(db, content, filename, actor):
    if not str(actor or '').strip() or not str(filename or '').strip():
        raise ValueError('Authenticated source confirmation is required.')
    source, account = prepare(db, content)
    digest = sha256(content).hexdigest()
    conn = db.get_connection()
    try:
        cur = conn.cursor()
        if not db.USING_POSTGRES:
            cur.execute('BEGIN IMMEDIATE')
        # Serialize this facility's confirmations, including same-period copies.
        # No schema or global lock/guard change is required.
        cur.execute('SELECT id,account_name,bank,account_number,currency FROM account_list WHERE id=?'
                    + (' FOR UPDATE' if db.USING_POSTGRES else ''), (account['id'],))
        row = cur.fetchone()
        if row is None or tuple(row) != tuple(account[k] for k in ('id','account_name','bank','account_number','currency')):
            raise ValueError('Flexline Setup changed; reload before confirming.')
        cur.execute('SELECT transaction_count FROM statement_imports WHERE statement_hash=?'
                    + (' FOR UPDATE' if db.USING_POSTGRES else ''), (digest,))
        header = cur.fetchone()
        cur.execute('SELECT COUNT(*) FROM classified_transactions WHERE statement_hash=?', (digest,))
        if cur.fetchone()[0] or (header is not None and header[0] != 0):
            raise ValueError('This PDF already has transaction history; no automatic rewrite is permitted.')
        cur.execute('SELECT id,bank,account_name,account_number,currency,period_end,closing_balance,notes '
                    'FROM statement_balances WHERE statement_hash=? OR '
                    '(source=? AND bank=? AND account_number=? AND currency=? AND period_end=?)'
                    + (' FOR UPDATE' if db.USING_POSTGRES else ''),
                    (digest, SOURCE, account['bank'], account['account_number'], 'USD', source['period_end']))
        existing = cur.fetchall()
        if len(existing) > 1:
            raise ValueError('Multiple Flexline balance identities exist; nothing changed.')
        prior = existing[0] if existing else None
        changes = {}
        if prior is not None:
            if tuple(prior[1:5]) != tuple(account[k] for k in ('bank','account_name','account_number','currency')):
                raise ValueError('Stored Flexline balance identity conflicts; nothing changed.')
            for key, value in zip(('period_end','closing_balance'), prior[5:7]):
                if value in (None, ''):
                    changes[key] = source[key]
                elif (Decimal(str(value)) != source[key] if key == 'closing_balance' else str(value) != source[key]):
                    raise ValueError('Stored Flexline closing metadata conflicts; nothing overwritten.')
            if not changes:
                conn.rollback()
                return 'already recorded'
        now = datetime.now(timezone.utc).isoformat(timespec='seconds')
        audit = json.dumps(dict(action=ACTION, actor=str(actor), source_hash=digest,
                               setup_id=account['id'], confirmed_at=now, transaction_changes=0,
                               statement_date=source['period_end'], new_balance=str(source['closing_balance'])),
                           separators=(',', ':'))
        if prior is None:
            if header is None:
                cur.execute('INSERT INTO statement_imports(statement_hash,statement_name,imported_at,transaction_count) VALUES(?,?,?,0)',
                            (digest, filename, now))
            if not db.save_statement_balance(digest, filename, dict(source, currency='USD', notes=audit), account, _connection=conn):
                raise ValueError('Flexline closing metadata was not saved.')
        else:
            changes.update(source=SOURCE, notes=(str(prior[7] or '')+'\n'+audit).strip(), updated_at=now)
            cur.execute('UPDATE statement_balances SET '+','.join(k+'=?' for k in changes)+' WHERE id=?',
                        (*changes.values(), prior[0]))
            if cur.rowcount != 1:
                raise ValueError('Flexline metadata changed concurrently.')
        conn.commit()
        return 'recorded'
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def render(st, content, filename):
    digest = sha256(content).hexdigest()
    st.info('Line of Credit balance only: Statement Date and New Balance. No payment, finance-charge or other transactions will be imported.')
    if st.session_state.get('flexline_result_hash') == digest:
        st.success(st.session_state['flexline_result'])
        return True
    try:
        import db
        source, account = prepare(db, content)
    except ValueError as error:
        st.error(str(error))
        return True
    except Exception:
        st.error('Flexline source/account verification unavailable. No data changed.')
        return True
    st.table([{'Account': account['account_name'], 'Statement Date': source['period_end'],
               'New Balance (USD)': format(source['closing_balance'], ',.2f')}])
    st.caption('Only Statement Date and New Balance are recorded; debt is negative. Opening balance and activity remain unknown; existing report completeness/total safeguards remain in force.')
    confirmed = st.checkbox('Confirm Line of Credit balance metadata only — zero transactions.', key='flexline_confirm_'+digest)
    if st.button('Import Line of Credit Balance Only', key='flexline_record', disabled=not confirmed):
        if not st.session_state.get('authenticated'):
            st.error('Sign in before confirming.')
            return True
        try:
            from auth import get_login_user
            outcome = record(db, content, filename, get_login_user())
        except ValueError as error:
            st.error(str(error))
            return True
        except Exception:
            st.error('Flexline metadata operation failed; no operation committed.')
            return True
        st.session_state['flexline_result_hash'] = digest
        st.session_state['flexline_result'] = ('LOC closing metadata already recorded; no changes made.' if outcome == 'already recorded'
            else 'Statement Date and New Balance recorded. Zero transactions imported; existing transactions unchanged.')
        st.cache_data.clear()
        st.rerun()
    return True
