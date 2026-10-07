"""Compact read-only THIRD report; exact partial subtotals in each native currency."""
from html import escape
from financial_decimal import optional_decimal, exact_sum
from latest_import_balances import NOTE, snapshot


def currency_totals(rows):
    grouped = {}
    for row in rows:
        amount = optional_decimal(row.get('Closing balance'))
        currency = str(row.get('Currency') or '').strip().upper()
        if (row.get('Status') == 'IMPORTED' and row.get('Verification') == 'SOURCE RECONCILIATION NOT VERIFIED'
                and currency and amount is not None and amount.is_finite()):
            grouped.setdefault(currency, []).append(amount)
    return {currency: exact_sum(values) for currency, values in sorted(grouped.items())}


def compact_html(rows):
    columns = ('Account number', 'Account name', 'Bank', 'Currency', 'Statement end date',
               'Closing balance', 'Import date', 'Verification')
    def cell(value):
        return escape(str(value) if value is not None else '')
    table = '<table style="font-size:12px;width:100%;border-collapse:collapse"><thead><tr>'
    table += ''.join('<th style="text-align:left;padding:4px">'+escape(key)+'</th>' for key in columns)
    table += '</tr></thead><tbody>'
    for row in rows:
        table += '<tr>'+''.join('<td style="padding:4px;border-bottom:1px solid #ddd">'+cell(row.get(key))+'</td>' for key in columns)+'</tr>'
    return table+'</tbody></table>'


def render(st, db):
    try:
        rows, _, warnings = snapshot(db)
    except Exception:
        st.error('Latest Import Balances is temporarily unavailable. Please try again.')
        return
    st.subheader('Latest Import Balances — compact list')
    st.info(NOTE)
    st.caption('Partial subtotals per native currency only. Incomplete, ambiguous, unverified-storage and liability rows are excluded from subtotals. Source reconciliation remains unverified; no FX conversion or combined total is used.')
    st.markdown(compact_html(rows), unsafe_allow_html=True)
    for currency, total in currency_totals(rows).items():
        st.write(f'{currency} partial subtotal: {total:,.2f}')
    if not currency_totals(rows):
        st.info('No eligible balances are available for a subtotal.')
    for warning in warnings:
        st.warning(warning)
