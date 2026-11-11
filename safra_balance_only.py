"""Safra no-bookings balance action; reuse strict section identity and persistence."""
from hashlib import sha256
from io import BytesIO
from decimal import Decimal


def recognized(content):
    import pdfplumber
    with pdfplumber.open(BytesIO(content)) as pdf:
        text = pdf.pages[0].extract_text() if pdf.pages else ''
    import re
    return bool(re.search(r'(?m)^Bank J\. Safra Sarasin AG\s*$', text or '')
                and re.search(r'(?m)^Account statement in [A-Z]{3} ', text or ''))


def prepare(db, content):
    from parsing import parse_pdf
    from safra_history import preview_sections
    frame = parse_pdf(BytesIO(content))
    sections = frame.attrs.get('safra_sections')
    if not sections or not frame.empty:
        raise ValueError('Safra balances-only requires a validated document with no bookings in every section. Use normal Import statement for booked activity.')
    for s in sections:
        if (s['transaction_count'] != 0 or Decimal(s['money_in']) != 0
                or Decimal(s['money_out']) != 0
                or Decimal(s['opening_balance']) != Decimal(s['closing_balance'])):
            raise ValueError('Safra no-bookings section is inconsistent.')
    preview = preview_sections(frame, db.get_accounts())
    if preview[['IBAN', 'Currency', 'Period start', 'Period end']].duplicated().any():
        raise ValueError('Repeated Safra account section.')
    return frame, preview


def record(db, content, filename, actor):
    if not actor or not str(filename).strip():
        raise ValueError('Authenticated source confirmation is required.')
    frame, _ = prepare(db, content)
    from safra_history import save_sections
    count, duplicate, skipped = save_sections(db, frame, filename, sha256(content).hexdigest())
    return 'already recorded' if duplicate else 'recorded'


def render(st, db, content, filename):
    digest = sha256(content).hexdigest()
    if st.session_state.get('safra_balance_hash') != digest:
        st.session_state['safra_balance_hash'] = digest
        st.session_state.pop('safra_balance_preview', None)
        st.session_state.pop('safra_balance_result', None)
    if st.session_state.get('safra_balance_result'):
        st.success(st.session_state['safra_balance_result'])
        return
    if st.button('Import Balances Only', key='safra_balance_prepare'):
        try:
            _, preview = prepare(db, content)
            st.session_state['safra_balance_preview'] = preview
        except ValueError as error:
            st.session_state.pop('safra_balance_preview', None)
            st.error(str(error))
        except Exception:
            st.session_state.pop('safra_balance_preview', None)
            st.error('Safra balance validation failed. No data changed.')
    preview = st.session_state.get('safra_balance_preview')
    if preview is None:
        return
    st.dataframe(preview, hide_index=True)
    st.caption('Validated no-bookings sections only. Each account/currency remains separate; zero transactions will be imported.')
    confirmed = st.checkbox('I confirm these balances only; do not import transactions.', key='safra_balance_confirm_'+digest)
    if st.button('Confirm Balance-Only Import', key='safra_balance_record', disabled=not confirmed):
        if not st.session_state.get('authenticated'):
            st.error('Sign in before confirming.')
            return
        try:
            from auth import get_login_user
            outcome = record(db, content, filename, get_login_user())
        except ValueError as error:
            st.error(str(error))
            return
        except Exception:
            st.error('Safra balance-only import failed. No operation was committed.')
            return
        st.session_state.pop('safra_balance_preview', None)
        st.session_state['safra_balance_result'] = ('Safra balances already recorded; no changes made. Existing dates are preserved.' if outcome == 'already recorded' else 'Safra account balances recorded separately. No transactions were imported.')
        st.cache_data.clear()
        st.rerun()
