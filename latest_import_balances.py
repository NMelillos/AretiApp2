"""Read-only account snapshot and standalone A4 landscape print document."""
from datetime import date, datetime, timezone
from html import escape
from zoneinfo import ZoneInfo
import re

import pandas as pd
from financial_decimal import optional_decimal, exact_sum, cents
from import_history import cyprus_time

NOTE = ('INTERIM PARTIAL REPORT: current Setup accounts only. Accounts absent from Setup are outside '
        'this report and are not enumerated. This is not a complete reconciled total of all accounts. '
        'One row per account/currency. Latest means committed import time, not statement end date. '
        'Newer incomplete imports remain visible; older balances are never substituted.')
COLUMNS = ('Account number', 'Account name', 'Bank', 'Import date',
           'Statement start date', 'Statement end date', 'Opening balance',
           'Closing balance', 'Currency', 'Closing balance converted to USD', 'Status',
           'Verification', 'Applied FX')

BANK_ALIASES = {'BOC':'BANKOFCYPRUS','CITIBANK':'CITI','CITIBANKCREDITCARD':'CITI',
                'CNB':'CITYNATIONALBANK','AMEX':'AMERICANEXPRESS',
                'JPMORGANCHASE':'CHASE','SAPPHIRECHASE':'CHASE'}


def _sql_compact(field, postgres):
    value = 'COALESCE(' + field + ", '')"
    for token in (' ', '.', '-', '/'):
        value = 'REPLACE(' + value + ", '" + token + "', '')"
    for code in (9, 10, 13):
        value = 'REPLACE(' + value + ', ' + ('CHR' if postgres else 'CHAR') + '(' + str(code) + "), '')"
    return 'UPPER(' + value + ')'


def _sql_bank(field, postgres):
    compact = _sql_compact(field, postgres)
    return '(CASE ' + compact + ''.join(" WHEN '" + key + "' THEN '" + value + "'" for key,value in BANK_ALIASES.items()) + ' ELSE ' + compact + ' END)'


def _sql_number(number, bank, currency, postgres):
    compact = _sql_compact(number, postgres)
    return '(CASE WHEN LOWER(' + bank + ") LIKE '%safra%' THEN REPLACE(" + compact + ", 'CURRENTACCOUNT' || UPPER(" + currency + ") || 'IBAN', '') ELSE " + compact + ' END)'


def snapshot(db):
    """Select completed imports using the existing completed_history contract.

    Full bank/account/currency identities define the account population. Safra's exact IBAN normalization
    mirrors _safra_page_accounts; no currency-only or name-only matching.
    All statement fields come from the single ranked balance/import join.
    Only counts are read from transactions, never transaction bodies.
    """
    storage_type = (lambda field: f'pg_typeof(b.{field})::text' if db.USING_POSTGRES
                    else f'typeof(b.{field})')
    source_bank = _sql_bank('matched.bank', db.USING_POSTGRES)
    source_number = _sql_number('matched.account_number','matched.bank','matched.currency',db.USING_POSTGRES)
    bank_match = '(' + source_bank + ' = ' + _sql_bank('a.bank', db.USING_POSTGRES) + ' AND ' + source_bank + " <> '')"
    number_match = ('(' + source_number + ' = ' + _sql_number('a.account_number','a.bank','a.currency',db.USING_POSTGRES)
                    + ' AND ' + source_number + " <> '')")
    query = f'''
        WITH accounts AS (
            SELECT id, account_name, bank, account_number, currency, rate_type,
                   CASE WHEN LOWER(bank) LIKE '%safra%'
                        THEN REPLACE(UPPER(REPLACE(account_number, ' ', '')),
                             'CURRENTACCOUNT' || UPPER(currency) || '/IBAN', '')
                        ELSE account_number END AS match_number
            FROM account_list
        ), candidates AS (
            SELECT a.id AS account_id, i.id AS import_id, i.imported_at, i.transaction_count,
                   b.period_start, b.period_end,
                   CAST(b.opening_balance AS TEXT) AS opening_balance,
                   CAST(b.closing_balance AS TEXT) AS closing_balance,
                   {storage_type('opening_balance')} AS opening_storage_type,
                   {storage_type('closing_balance')} AS closing_storage_type,
                   b.currency AS statement_currency,
                   b.account_number AS statement_account_number,
                   b.bank AS statement_bank, b.account_name AS statement_account_name,
                   (SELECT COUNT(*) FROM classified_transactions t
                    WHERE t.statement_hash = i.statement_hash AND t.split_parent_id IS NULL) AS source_rows
            FROM accounts a
            JOIN statement_imports i ON EXISTS (
                SELECT 1 FROM statement_balances matched
                WHERE matched.statement_hash = i.statement_hash
                  AND {bank_match}
                  AND UPPER(matched.currency) = UPPER(a.currency)
                  AND {number_match}
            ) OR EXISTS (
                SELECT 1 FROM classified_transactions matched
                WHERE matched.statement_hash = i.statement_hash
                  AND {bank_match}
                  AND UPPER(matched.currency) = UPPER(a.currency)
                  AND {number_match}
            )
            LEFT JOIN statement_balances b ON b.statement_hash = i.statement_hash
        )
        SELECT a.id AS account_id, a.account_number, a.account_name, a.bank,
               a.currency, a.rate_type, r.import_id, r.imported_at,
               r.period_start, r.period_end, r.opening_balance, r.closing_balance,
               r.statement_currency, r.statement_account_number, r.transaction_count,
               r.statement_bank, r.statement_account_name,
               r.source_rows, r.opening_storage_type, r.closing_storage_type
        FROM accounts a LEFT JOIN candidates r ON r.account_id = a.id
        ORDER BY a.bank, a.account_name, a.account_number, a.id
    '''
    connection = db.get_connection()
    try:
        rows = pd.read_sql_query(query, connection, coerce_float=False).to_dict('records')
    finally:
        connection.close()
    rates = db._load_rate_lookup()  # One batch, using the application's approved lookup.
    result, warnings, eligible = [], [], []
    populations = {}
    for row in rows:
        populations.setdefault(_account_identity(row), []).append(row)
    selected_rows = []
    for population in populations.values():
        aliases = {row['account_id']: (str(row['account_name'] or ''), str(row['rate_type'] or ''))
                   for row in population}
        imported_rows = [row for row in population if pd.notna(row['import_id'])]
        if not imported_rows:
            selected = min(population, key=lambda row: row['account_id'])
        elif all(_verified_time(row['imported_at']) is not None for row in imported_rows):
            selected = max(imported_rows, key=lambda row: (_verified_time(row['imported_at']), row['import_id']))
        else:
            selected = max(imported_rows, key=lambda row: row['import_id'])
            warnings.append(f"{selected['account_number']}: import ordering is unverified; latest import ID selected because a legacy timestamp is missing, invalid or timezone-unverified.")
        selected = dict(selected)
        if len(aliases) > 1:
            representative = min(population, key=lambda row: row['account_id'])
            selected['_display_number'] = representative['account_number']
            selected['_display_bank'] = representative['bank']
            selected['_ambiguous_aliases'] = [f'{name} ({rate or "rate unspecified"})'
                                              for _, (name, rate) in sorted(aliases.items())]
        selected_rows.append(selected)
    for row in selected_rows:
        ambiguous = row.get('_ambiguous_aliases')
        imported = pd.notna(row['import_id'])
        label_mismatch = imported and pd.notna(row['statement_account_name']) and (
            row['statement_account_name'] != row['account_name'] or row['statement_bank'] != row['bank'])
        currency = (row['statement_currency'] if imported and pd.notna(row['statement_currency'])
                    and str(row['statement_currency']).strip() else row['currency']) or ''
        opening = optional_decimal(row['opening_balance']) if imported else None
        closing = optional_decimal(row['closing_balance']) if imported else None
        complete = imported and opening is not None and closing is not None and all(
            pd.notna(row[key]) and str(row[key]).strip() for key in ('period_start', 'period_end'))
        complete = complete and pd.notna(row['statement_currency']) and bool(str(row['statement_currency']).strip())
        complete = complete and pd.notna(row['statement_account_number']) and bool(str(row['statement_account_number']).strip())
        def identity_number(value):
            compact = str(value).replace(' ', '').upper()
            return compact.replace('CURRENTACCOUNT' + str(currency).upper() + '/IBAN', '') if 'safra' in str(row['bank']).lower() else str(value)
        complete = complete and str(row['statement_currency']).upper() == str(row['currency']).upper()
        complete = complete and identity_number(row['statement_account_number']) == identity_number(row['account_number'])
        complete = complete and row['statement_bank'] == row['bank'] and row['statement_account_name'] == row['account_name']
        complete = complete and row['transaction_count'] == row['source_rows']
        if complete:
            try:
                complete = date.fromisoformat(str(row['period_start'])) <= date.fromisoformat(str(row['period_end']))
            except ValueError:
                complete = False
        if complete and row['transaction_count'] == 0 and opening != closing:
            complete = False
        precision_known = currency.upper() in ('USD', 'EUR', 'GBP', 'CHF')
        exact_storage = all(str(row[key]).lower() in ('numeric', 'decimal', 'text', 'integer', 'bigint', 'smallint')
                            for key in ('opening_storage_type', 'closing_storage_type'))
        exact_precision = exact_storage and precision_known and all(value is None or value == cents(value)
                                                  for value in (opening, closing))
        card_convention = (_account_identity(row)[0] in ('CITI', 'AMERICANEXPRESS', 'CHASE')
                           or any(token in (str(row['bank']) + ' ' + str(row['account_name'])).lower()
                                  for token in ('credit card', 'liability')))
        verification = ('AMBIGUOUS SETUP IDENTITY' if ambiguous else 'NO IMPORT' if not imported else 'SETUP/SOURCE LABEL MISMATCH' if label_mismatch else 'INCOMPLETE METADATA' if not complete
                        else 'UNVERIFIED BINARY STORAGE' if not exact_storage
                        else 'UNVERIFIED STORED PRECISION' if not exact_precision
                        else 'LIABILITY CONVENTION UNVERIFIED' if card_convention
                        else 'SOURCE RECONCILIATION NOT VERIFIED')
        usd = None
        applied_fx = ''
        if complete and exact_precision and not ambiguous:
            if currency.upper() == 'USD':
                usd = closing
                applied_fx = '1 (USD identity)'
            elif pd.notna(pd.to_datetime(row['period_end'], errors='coerce')):
                _, rate = db._resolve_rate_for_values(
                    rates, row['rate_type'], currency, row['period_end'])
                if (parsed_rate := optional_decimal(rate)) is not None and parsed_rate > 0:
                    usd = db._usd_from_amount(closing, parsed_rate)
                    applied_fx = f'{parsed_rate} (Setup rate at statement month)'
        if ambiguous:
            warnings.append(f"{row['account_number']}: AMBIGUOUS SETUP IDENTITY; aliases: {'; '.join(ambiguous)}. One account/currency row is shown using the latest available import; excluded from total until Setup ownership is resolved.")
        elif label_mismatch:
            warnings.append(f"{row['account_number']}: SETUP/SOURCE LABEL MISMATCH. Latest stored balances remain visible; source labels: {row['statement_bank']} / {row['statement_account_name']}. Excluded from total until ownership/labels are verified.")
        elif imported and usd is None:
            warnings.append(f"{row['account_number']}: USD NOT AVAILABLE ({verification}); excluded from total.")
        elif not imported:
            warnings.append(f"{row['account_number']}: NO IMPORT; excluded from total.")
        elif card_convention:
            warnings.append(f"{row['account_number']}: card/liability USD equivalent retains the stored statement sign; excluded from total pending an approved net-value convention.")
        if usd is not None and not card_convention:
            eligible.append(usd)
        result.append(dict(zip(COLUMNS, (
            row.get('_display_number', row['account_number']) or '', '; '.join(ambiguous) if ambiguous else (str(row['account_name']) + '; source label: ' + str(row['statement_account_name']) if label_mismatch else row['account_name'] or ''), row.get('_display_bank', row['bank']) or '',
            cyprus_time(row['imported_at']) if imported else '',
            row['period_start'] if imported and pd.notna(row['period_start']) else '',
            row['period_end'] if imported and pd.notna(row['period_end']) else '',
            opening, closing,
            currency, usd if usd is not None else ('NOT AVAILABLE' if imported else ''),
            'AMBIGUOUS SETUP IDENTITY' if ambiguous else ('IMPORTED' if complete else 'INCOMPLETE') if imported else 'NO IMPORT',
            verification, applied_fx,
        ))))
    result.sort(key=lambda row: (row['Bank'], row['Account name'],
                                -(datetime.fromisoformat(row['Import date'][:19]).timestamp()
                                  if _iso_time(row['Import date']) else 0), row['Account number']))
    return result, exact_sum(eligible), warnings


def _account_identity(row):
    bank = re.sub(r'[\s.\-/]', '', str(row['bank'] or '')).upper()
    bank = BANK_ALIASES.get(bank, bank)
    currency = str(row['currency'] or '').strip().upper()
    number = str(row['account_number'] or '').strip().upper()
    if 'SAFRA' in bank:
        number = re.sub(r'\s', '', number)
        number = number.replace('CURRENTACCOUNT' + currency + '/IBAN', '')
    number = re.sub(r'[\s.\-/]', '', number)
    # Missing identifiers cannot establish equality between unrelated Setup rows.
    return (bank, number, currency) if bank and number and currency else ('UNRESOLVED', row['account_id'])


def _verified_time(value):
    try:
        parsed = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        return parsed.astimezone(timezone.utc) if parsed.tzinfo is not None else None
    except (TypeError, ValueError):
        return None


def _iso_time(value):
    try:
        datetime.fromisoformat(str(value)[:19])
        return bool(value)
    except ValueError:
        return False


def fresh_import(value, generated_at):
    """Verified timezone-aware import dates only; local calendar age 0..29."""
    if not value or 'unverified' in str(value).lower():
        return False
    # cyprus_time's explicit EET/EEST suffix is verified by its timezone adapter.
    try:
        parsed = datetime.strptime(value, '%Y-%m-%d %H:%M:%S EEST') if value.endswith(' EEST') else datetime.strptime(value, '%Y-%m-%d %H:%M:%S EET')
    except ValueError:
        return False
    today = generated_at.astimezone(ZoneInfo('Europe/Nicosia')).date()
    return 0 <= (today - parsed.date()).days < 30


def workbook_bytes(rows, total, warnings, generated_at=None):
    """Same authoritative dataset; Excel precision limits are explicit."""
    from io import BytesIO
    from openpyxl import Workbook
    book = Workbook()
    sheet = book.active
    sheet.title = 'Latest Import Balances'
    sheet.append(list(COLUMNS))
    for row in rows:
        values = []
        for column in COLUMNS:
            value = row[column]
            if hasattr(value, 'as_tuple'):
                # Numeric Excel cells are safe only within its 15-digit contract.
                value = float(value) if len(value.as_tuple().digits) <= 15 else str(value)
            values.append(value)
        sheet.append(values)
        for cell in sheet[sheet.max_row]:
            if isinstance(cell.value, str):
                cell.data_type = 's'
        for position in (7,8,10):
            known_precision = position == 10 or row['Currency'].upper() in ('USD','EUR','GBP','CHF')
            sheet.cell(sheet.max_row, position).number_format = (
                '#,##0.00;[Red]-#,##0.00' if known_precision else '#,##0.################')
    sheet.freeze_panes = 'A2'
    sheet.auto_filter.ref = sheet.dimensions
    info = book.create_sheet('Verification')
    info.append(['Generated', (generated_at or datetime.now(ZoneInfo('Europe/Nicosia'))).isoformat()])
    info.append(['Report scope', NOTE])
    info.append(['Total basis', 'Eligible deposit balances only; liabilities and unverified values excluded'])
    info.append(['Partial USD total (current Setup eligible deposits)', float(total) if len(total.as_tuple().digits) <= 15 else str(total)])
    info.cell(4,2).number_format = '#,##0.00'
    info.append(['Excel precision', 'Values exceeding 15 digits are exact text, not rounded numeric cells.'])
    info.append(['Excluded accounts/balances', 'Per-account exclusions and reasons follow; balances remain visible on the main sheet. Accounts outside Setup are not enumerated.'])
    if not warnings:
        info.append(['Excluded', 'No current Setup balances excluded by report eligibility; accounts outside Setup remain outside scope.'])
    for warning in warnings:
        info.append(['Excluded' if 'excluded' in warning.lower() else 'Verification warning', warning])
    for row in info:
        for cell in row:
            if isinstance(cell.value, str):
                cell.data_type = 's'
    output = BytesIO()
    book.save(output)
    return output.getvalue()


def print_document(rows, total, warnings, generated_at=None):
    generated_at = generated_at or datetime.now(ZoneInfo('Europe/Nicosia'))
    def cell(value, currency='USD'):
        if value is None:
            return ''
        # Display only. Unverified stored precision is explicitly disclosed and
        # excluded from arithmetic; cents never reconstructs historical truth.
        if hasattr(value, 'as_tuple'):
            return escape(format(cents(value), ',.2f') if currency.upper() in ('USD','EUR','GBP','CHF')
                          else format(value, ',f'))
        return escape(str(value))
    headings = ''.join(f'<th>{escape(column)}</th>' for column in COLUMNS)
    body_parts, group = [], None
    for row in rows:
        current = (row['Bank'], row['Account name'])
        if current != group:
            body_parts.append(f'<tr class="group"><th colspan="{len(COLUMNS)}">{escape(current[0])} / {escape(current[1])}</th></tr>')
            group = current
        body_parts.append('<tr>' + ''.join(
            f'<td class="fresh">{cell(row[column])}<br>Under 30 days</td>'
            if column == 'Import date' and fresh_import(row[column], generated_at)
            else f'<td>{cell(row[column], row["Currency"] if column in ("Opening balance", "Closing balance") else "USD")}</td>' for column in COLUMNS) + '</tr>')
    body = ''.join(body_parts)
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
.fresh {{ color: #146b36; font-weight: bold; }}
.group th {{ background: #dce7ef; break-after: avoid; }}
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
<p>Status: IMPORTED / INCOMPLETE / NO IMPORT. Green import dates are verified local dates under 30 days old.
Legacy timezone uncertainty is preserved. Binary storage and decimal tails are unverified, displayed at currency precision and excluded from totals.
Card/liability balances are shown in their stored statement convention and excluded pending an approved net-value convention.</p>
<p>Non-USD values use Setup &gt; Rates as of each statement end date: the latest configured rate at or before that month,
or the earliest configured rate when no earlier month exists (existing application policy). Missing rates are excluded.</p>
<button onclick="window.print()">Print / Save as PDF</button>
<table><thead><tr>{headings}</tr></thead><tbody>{body}</tbody></table>
<p class="total">PARTIAL TOTAL USD — CURRENT SETUP ACCOUNTS (eligible deposit balances): {cell(total)}</p>
<h2>Excluded balances and other verification warnings</h2>
<p>Only warnings stating that a balance is excluded remove it from the partial total.
Import-time verification warnings alone do not change balance eligibility.</p>
{'' if warnings else '<p>No current Setup balances excluded by report eligibility; accounts outside Setup remain outside scope.</p>'}
<ul class="warnings">{warning_html}</ul>
</body></html>'''


def render(st):
    import db
    import streamlit.components.v1 as components
    st.subheader('Latest Import Balances')
    st.info(NOTE)
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
    st.download_button('Download balances workbook', workbook_bytes(rows,total,warnings),
                       file_name='latest_import_balances.xlsx',
                       mime='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')
    components.html(document, height=650, scrolling=True)
