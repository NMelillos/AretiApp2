"""Read-only validation of an existing Safra document using its original identity."""
from contextlib import closing


def is_existing_safra(statement_hash):
    from db import get_connection
    with closing(get_connection()) as conn:
        cur = conn.cursor()
        try:
            cur.execute('SELECT COUNT(*) FROM statement_balances WHERE source = ?',
                        ('Safra document ' + statement_hash,))
            return cur.fetchone()[0] > 0
        finally:
            cur.close()


def render_preview(st, file_bytes, file_name, accounts, parse_statement):
    from existing_import_compare import render_compare
    render_compare(st, file_bytes)
    from db import _safra_page_accounts
    from safra_history import preview_sections, preview_transactions
    st.warning('This statement already exists. The preview below is for validation only and cannot be imported again.')
    validate = st.button('Validate existing statement in read-only preview')
    if st.button('Cancel') or not validate:
        return
    try:
        parsed = parse_statement(file_bytes, file_name)
        if not parsed.attrs.get('safra_sections'):
            raise ValueError('The existing Safra statement could not be validated.')
        mapped = _safra_page_accounts(parsed, accounts)
        sections = preview_sections(parsed, accounts)
        sections['Matched account'] = [mapped[page]['account_number'] for page in sections.Page]
        sections['Booking currencies'] = [
            ', '.join(sorted(set(parsed.loc[parsed.source_page.eq(page), 'statement_currency'])))
            or 'No bookings' for page in sections.Page
        ]
        sections['Validation result'] = 'Passed'
        transactions = parsed.copy()
        transactions['currency'] = transactions['statement_currency']
        for field in ('account_name', 'bank', 'rate_type'):
            transactions[field] = transactions.source_page.map(lambda page: mapped[page].get(field, ''))
        st.dataframe(sections, use_container_width=True, hide_index=True)
        st.dataframe(preview_transactions(transactions), use_container_width=True, hide_index=True)
    except Exception:
        st.error('Safra read-only validation failed. No changes were saved. Verify the statement and Setup account mapping.')
