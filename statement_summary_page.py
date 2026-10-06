"""Read-only summary page; deliberately does not import the application database."""
import streamlit as st
from statement_summary import extract_pdf


def render_statement_summary_page():
    st.title('Statement Summary')
    st.info('Read-only PDF preview. No transactions, classifications or stored balances are created or changed. This is separate from normal Upload.')
    st.caption('Bank of Cyprus text statements supported first. Other layouts or scanned fields return NOT VERIFIED. Zero transactions are supported; transaction extraction is never required.')
    if st.button('Return to application', key='return_from_summary'):
        st.query_params['page'] = 'Import'
        st.rerun()
    uploaded = st.file_uploader('Choose a statement PDF', type=['pdf'], key='summary_pdf')
    if uploaded is None:
        return
    if st.button('Extract Statement Summary', key='extract_summary', type='primary'):
        try:
            result = extract_pdf(uploaded.getvalue())
        except Exception:
            # No private PDF text, paths or credentials in the failure display.
            st.error('Unable to read this PDF. Fields are NOT VERIFIED. Use a readable text PDF within 20 MiB and 100 pages.')
            return
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
        st.caption('Values retain source decimal precision and sign. Source-field verification does not establish statement authenticity, transaction reconciliation or successful production import. Nothing is saved.')


if __name__ == '__main__':
    st.set_page_config(page_title='Areti - Statement Summary', layout='wide')
    from auth import require_login
    require_login()
    render_statement_summary_page()
