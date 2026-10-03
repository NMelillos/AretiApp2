"""Read-only account snapshot and standalone A4 landscape print document."""
from datetime import datetime
from html import escape
from zoneinfo import ZoneInfo

import pandas as pd
from financial_decimal import optional_decimal, exact_sum
from import_history import cyprus_time

NOTE = ('One row per account. Figures are based on the latest successfully '
        'imported statement available for each account.')
COLUMNS = ('Account number', 'Account name', 'Bank', 'Import date',
           'Statement start date', 'Statement end date', 'Opening balance',
           'Closing balance', 'Currency', 'Closing balance converted to USD', 'Status')


def snapshot(db):
    """Select completed imports using the existing completed_history contract.

    Setup IDs define the account population. Safra's exact IBAN normalization
    mirrors _safra_page_accounts; no currency-only or name-only matching.
    All statement fields come from the single ranked balance/import join.
    Only counts are read from transactions, never transaction bodies.
    """
    timestamp = ("CAST(NULLIF(i.imported_at, '') AS TIMESTAMPTZ)" if db.USING_POSTGRES
                 else "julianday(NULLIF(i.imported_at, ''))")
    query = f'''
        WITH accounts AS (
            SELECT id, account_name, bank, account_number, currency, rate_type,
                   CASE WHEN LOWER(bank) LIKE '%safra%'
                        THEN REPLACE(UPPER(REPLACE(account_number, ' ', '')),
                             'CURRENTACCOUNT' || UPPER(currency) || '/IBAN', '')
                        ELSE account_number END AS match_number
            FROM account_list
        ), ranked AS (
            SELECT a.id AS account_id, i.id AS import_id, i.imported_at,
                   b.period_start, b.period_end,
                   CAST(b.opening_balance AS TEXT) AS opening_balance,
                   CAST(b.closing_balance AS TEXT) AS closing_balance,
                   b.currency AS statement_currency,
                   ROW_NUMBER() OVER (
                       PARTITION BY a.id
                       ORDER BY {timestamp} DESC NULLS LAST, i.id DESC
                   ) AS position
            FROM accounts a
            JOIN statement_balances b
              ON b.account_name = a.account_name AND b.bank = a.bank
             AND UPPER(b.currency) = UPPER(a.currency)
             AND (CASE WHEN LOWER(a.bank) LIKE '%safra%'
                       THEN UPPER(REPLACE(b.account_number, ' ', ''))
                       ELSE b.account_number END) = a.match_number
            JOIN statement_imports i ON i.statement_hash = b.statement_hash
            WHERE i.transaction_count = (
                SELECT COUNT(*) FROM classified_transactions t
                WHERE t.statement_hash = b.statement_hash AND t.split_parent_id IS NULL
            ) AND (i.transaction_count > 0 OR (
                COALESCE(b.period_start, '') <> '' AND COALESCE(b.period_end, '') <> ''
                AND COALESCE(b.account_number, '') <> '' AND COALESCE(b.currency, '') <> ''
                AND b.opening_balance IS NOT NULL AND b.closing_balance IS NOT NULL
                AND b.opening_balance = b.closing_balance
            ))
        )
        SELECT a.id AS account_id, a.account_number, a.account_name, a.bank,
               a.currency, a.rate_type, r.import_id, r.imported_at,
               r.period_start, r.period_end, r.opening_balance, r.closing_balance,
               r.statement_currency
        FROM accounts a LEFT JOIN ranked r ON r.account_id = a.id AND r.position = 1
        ORDER BY a.bank, a.account_name, a.account_number, a.id
    '''
    connection = db.get_connection()
    try:
        rows = pd.read_sql_query(query, connection, coerce_float=False).to_dict('records')
    finally:
        connection.close()
    rates = db._load_rate_lookup()  # One batch, using the application's approved lookup.
    result, warnings, eligible = [], [], []
    for row in rows:
        imported = pd.notna(row['import_id'])
        currency = (row['statement_currency'] if imported else row['currency']) or ''
        closing = optional_decimal(row['closing_balance']) if imported else None
        usd = None
        if closing is not None:
            if currency.upper() == 'USD':
                usd = closing
            elif pd.notna(pd.to_datetime(row['period_end'], errors='coerce')):
                _, rate = db._resolve_rate_for_values(
                    rates, row['rate_type'], currency, row['period_end'])
                if (parsed_rate := optional_decimal(rate)) is not None and parsed_rate > 0:
                    usd = db._usd_from_amount(closing, parsed_rate)
        if imported and usd is None:
            warnings.append(f"{row['account_number']}: USD NOT AVAILABLE; excluded from total.")
        if usd is not None:
            eligible.append(usd)
        result.append(dict(zip(COLUMNS, (
            row['account_number'] or '', row['account_name'] or '', row['bank'] or '',
            cyprus_time(row['imported_at']) if imported else '',
            row['period_start'] or '' if imported else '',
            row['period_end'] or '' if imported else '',
            optional_decimal(row['opening_balance']) if imported else None, closing,
            currency, usd if usd is not None else ('NOT AVAILABLE' if imported else ''),
            'IMPORTED' if imported else 'NO IMPORT',
        ))))
    return result, exact_sum(eligible), warnings


def print_document(rows, total, warnings, generated_at=None):
    generated_at = generated_at or datetime.now(ZoneInfo('Europe/Nicosia'))
    def cell(value):
        if value is None:
            return ''
        # Decimal formatting; no float conversion or balance rounding.
        if hasattr(value, 'as_tuple'):
            whole, _, fraction = format(value, ',f').partition('.')
            return escape(whole + '.' + fraction.ljust(2, '0'))
        return escape(str(value))
    headings = ''.join(f'<th>{escape(column)}</th>' for column in COLUMNS)
    body = ''.join('<tr>' + ''.join(f'<td>{cell(row[column])}</td>' for column in COLUMNS)
                   + '</tr>' for row in rows)
    warning_html = ''.join(f'<li>{escape(warning)}</li>' for warning in warnings)
    return f'''<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Latest Import Balances</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
@page {{ size: A4 landscape; margin: 10mm; }}
* {{ box-sizing: border-box; }}
body {{ margin: 20px; color: #182638; font-family: Arial, sans-serif; font-size: 11px; }}
h1 {{ font-size: 22px; margin: 0 0 8px; }}
p {{ line-height: 1.5; margin: 6px 0; }}
table {{ width: 100%; border-collapse: collapse; margin: 16px 0; table-layout: fixed; }}
th, td {{ padding: 7px 5px; border-bottom: 1px solid #ccd4df; vertical-align: top;
           overflow-wrap: anywhere; text-align: left; }}
th {{ background: #eaf0f6; font-size: 10px; }}
td:nth-child(7), td:nth-child(8), td:nth-child(10) {{ text-align: right; }}
tr {{ break-inside: avoid; }} thead {{ display: table-header-group; }}
.total {{ font-size: 15px; font-weight: bold; border-top: 2px solid #182638; padding-top: 12px; }}
.warnings {{ color: #803900; }}
button {{ margin: 14px 0; padding: 8px 14px; cursor: pointer; }}
@media print {{ body {{ margin: 0; font-size: 9px; }} button {{ display: none; }}
               th, td {{ padding: 5px 4px; }} th {{ font-size: 9px; }} }}
</style></head><body>
<h1>Latest Import Balances</h1>
<p>As of: {escape(generated_at.strftime('%Y-%m-%d %H:%M:%S %Z'))}</p>
<p>{NOTE}</p>
<p>Status: IMPORTED / NO IMPORT. No business freshness threshold is configured.</p>
<p>Non-USD values use Setup &gt; Rates as of each statement end date: the latest configured rate at or before that month,
or the earliest configured rate when no earlier month exists (existing application policy). Missing rates are excluded.</p>
<button onclick="window.print()">Print / Save as PDF</button>
<table><thead><tr>{headings}</tr></thead><tbody>{body}</tbody></table>
<p class="total">TOTAL USD VALUE OF ALL ACCOUNTS: {cell(total)}</p>
<ul class="warnings">{warning_html}</ul>
</body></html>'''


def render(st):
    import db
    import streamlit.components.v1 as components
    st.subheader('Latest Import Balances')
    try:
        rows, total, warnings = snapshot(db)
    except Exception:
        st.error('Latest Import Balances is temporarily unavailable. Please try again.')
        return  # Database errors must not expose connection details in the report.
    for warning in warnings:
        st.warning(warning)
    document = print_document(rows, total, warnings)
    st.caption('Download the printable report and open it in your browser to Print / Save as PDF (A4 landscape).')
    st.download_button('Download printable report', document.encode('utf-8'),
                       file_name='latest_import_balances.html', mime='text/html')
    components.html(document, height=650, scrolling=True)
