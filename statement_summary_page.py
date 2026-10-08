"""Read-only summary page; deliberately does not import the application database."""
import streamlit as st
from hashlib import sha256
from statement_summary import extract_pdf


def render_statement_summary_page():
    st.title('Statement Summary')
    st.info('PDF preview is read-only. Balances are recorded only after the separate balance-only preview and your confirmation. No transactions or classifications are changed.')
    st.caption('Bank of Cyprus text statements supported first. Other layouts or scanned fields return NOT VERIFIED. Zero transactions are supported; transaction extraction is never required.')
    if st.button('Return to application', key='return_from_summary'):
        st.query_params['page'] = 'Import'
        st.rerun()
    uploaded = st.file_uploader('Choose a statement PDF', type=['pdf'], key='summary_pdf')
    if uploaded is None:
        return
    content = uploaded.getvalue()
    fingerprint = sha256(content).hexdigest()
    if st.session_state.get('summary_balance_hash') != fingerprint:
        st.session_state['summary_balance_hash'] = fingerprint
        st.session_state.pop('summary_balance_preview', None)
        st.session_state.pop('summary_balance_result', None)
        st.session_state.pop('summary_preview_result', None)
    if st.button('Extract Statement Summary', key='extract_summary', type='primary'):
        try:
            result = extract_pdf(content)
        except Exception:
            # No private PDF text, paths or credentials in the failure display.
            st.error('Unable to read this PDF. Fields are NOT VERIFIED. Use a readable text PDF within 20 MiB and 100 pages.')
            return
        st.session_state['summary_preview_result'] = result
    result = st.session_state.get('summary_preview_result')
    if result is not None:
        st.caption(f'Bank: {result.bank} | Account: {result.account}')
        st.caption(f'Currency: {result.currency.value or "NOT VERIFIED"}')
        rows = []
        for label, key in [('Statement start date', 'period_start'), ('Statement end date', 'period_end'),
                           ('Opening balance', 'opening_balance'), ('Closing balance', 'closing_balance')]:
            field = getattr(result, key)
            rows.append({'Field': label, 'Value': ((field.value + (' ' + field.currency if field.currency else '')) if field.verified else 'NOT VERIFIED'),
                         'Verification': 'SOURCE FIELD VERIFIED' if field.verified else 'NOT VERIFIED',
                         'Source page': ', '.join(str(e.page) for e in field.evidence) or 'Not found',
                         'Source anchor': ' | '.join(dict.fromkeys(e.anchor for e in field.evidence)) or 'Not found',
                         'Note': field.note})
        st.table(rows)
        st.caption('Values retain source decimal precision and sign. Source-field verification does not establish statement authenticity, transaction reconciliation or successful production import. Extracting this preview saves nothing; the separate confirmed balance-only action can record metadata.')


    controls = st.empty()
    if st.session_state.get('summary_balance_result'):
        controls.success(st.session_state['summary_balance_result'])
        return
    with controls.container():
        if st.button('Import balances only', key='summary_prepare_balances'):
            try:
                from boc_summary_balances import prepare
                _, balance = prepare(content)
                st.session_state['summary_balance_preview'] = {key: str(balance[key]) for key in ('account_number','currency','period_start','period_end','opening_balance','money_in','money_out','closing_balance')}
            except Exception:
                st.session_state.pop('summary_balance_preview', None)
                st.error('Balance-only preview could not be verified. Use a complete reconciled BOC text statement.')
        preview = st.session_state.get('summary_balance_preview')
        if preview:
            st.subheader('Balance-only confirmation')
            st.table([{'Field':key,'Source value':value} for key,value in preview.items()])
            st.caption('Only balance metadata will be recorded. Existing source transactions must match exactly. No transactions, classifications or original import date are changed; conflicting metadata is rejected.')
            confirmed = st.checkbox('I confirm these source balances; record balance metadata only.', key='summary_balance_confirm_'+fingerprint)
            if st.button('Confirm balance-only import', key='summary_record_balances', disabled=not confirmed):
                if not st.session_state.get('authenticated'):
                    st.error('Sign in before confirming balance metadata.');return
                try:
                    import db
                    from boc_summary_balances import record, BalanceOnlyError
                    from auth import get_login_user
                    outcome = record(db, content, uploaded.name, get_login_user())
                except BalanceOnlyError as error:
                    st.error(str(error));return
                except Exception:
                    st.error('Balance metadata could not be recorded. No balance-only operation was committed.');return
                st.session_state.pop('summary_balance_preview', None)
                st.session_state['summary_balance_result'] = 'Balance metadata already recorded; no changes made.' if outcome == 'already recorded' else 'Balance metadata recorded. No transactions imported or changed.'
                st.cache_data.clear()
                controls.empty()
                controls.success(st.session_state['summary_balance_result'])
                st.rerun()


if __name__ == '__main__':
    st.set_page_config(page_title='Areti - Statement Summary', layout='wide')
    from auth import require_login
    require_login()
    render_statement_summary_page()
