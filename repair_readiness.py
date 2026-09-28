"""Read-only evidence and versioned hashes. No repair implementation exists."""
from datetime import date, datetime
from decimal import Decimal
import hashlib
import json
import math


FIELDS = {
    'classified_transactions': set('id statement_hash statement_name row_hash txn_date original_description normalized_description amount currency rate_type fx_rate amount_usd account_name bank account_number beneficiary transaction_type category subcategory suggested_category suggested_subcategory match_type confidence reviewed status dup_flag created_at reviewed_at split_parent_id split_group_id split_allocation_index split_original_amount'.split()),
    'statement_balances': set('id statement_hash statement_name account_name bank account_number currency period_start period_end opening_balance money_out money_in closing_balance source notes imported_at updated_at'.split()),
    'statement_imports': set('id statement_hash statement_name imported_at transaction_count duplicate_attempts last_duplicate_at'.split()),
    'category_list': set('id category subcategory report_group created_at'.split()),
    'transaction_change_log': set('id transaction_id field_name old_value new_value source changed_at'.split()),
}


def _canonical(value):
    if isinstance(value, dict):
        return ['object', [[key, _canonical(value[key])] for key in sorted(value)]]
    if isinstance(value, (list, tuple)):
        return ['list', [_canonical(v) for v in value]]
    if value is None:
        return ['null']
    if isinstance(value, Decimal):
        return ['decimal', str(value)]
    if isinstance(value, bool):
        return ['bool', value]
    if isinstance(value, int):
        return ['integer', str(value)]
    if isinstance(value, float):
        return ['float', value.hex()]
    if isinstance(value, str):
        return ['text', value]
    if isinstance(value, (datetime, date)):
        return [type(value).__name__, value.isoformat()]
    raise ValueError('Unsupported snapshot value type')


def state_hash(value):
    payload = ['areti-readiness-v1', _canonical(value)]
    return hashlib.sha256(json.dumps(payload, ensure_ascii=True, separators=(',', ':')).encode()).hexdigest()


def _display(value):
    if value is None:
        return 'NULL'
    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError('Non-finite snapshot field')
        return str(Decimal.from_float(value))
    return str(value)


def describe_fields(table, row, proposed):
    if set(row) - FIELDS[table] - {'_read_version'}:
        raise ValueError('Unrecognized snapshot column; diagnostics blocked')
    fields = []
    for field, value in row.items():
        if field == '_read_version':
            continue
        has_proposal = field in proposed
        target = proposed.get(field)
        differs = has_proposal and value != target
        fields.append({'Table': table, 'Record ID': row['id'], 'Field': field,
            'Current database value': _display(value),
            'Proposed PDF/parser value': _display(target) if has_proposal else 'NOT APPLICABLE',
            'Disposition': 'PDF DIFFERENCE - DIAGNOSTIC ONLY' if differs else 'UNCHANGED / PRESERVE'})
    return fields


def collect(cur, postgres, fingerprint, result, balances, imports, transactions):
    import pandas as pd
    from existing_import_compare import _dicts, MONEY
    from reporting import _assign_report_groups
    schema = []
    for table, fields in dict(MONEY, rates=('rate_value',)).items():
        if postgres:
            rows = _dicts(cur, '''SELECT column_name, data_type, udt_name, numeric_precision,
                numeric_precision_radix, numeric_scale, is_nullable
                FROM information_schema.columns
                WHERE table_schema = current_schema() AND table_name = ? ORDER BY ordinal_position''', (table,))
        else:
            rows = [{'column_name': r['name'], 'data_type': r['type'], 'udt_name': 'SQLite',
                     'numeric_precision': None, 'numeric_precision_radix': None,
                     'numeric_scale': None, 'is_nullable': not r['notnull']}
                    for r in _dicts(cur, 'PRAGMA table_info(' + table + ')')]
        selected = [dict(Table=table, **r) for r in rows if r['column_name'] in fields]
        if len(selected) != len(fields):
            raise ValueError('Incomplete monetary schema evidence')
        schema.extend(selected)
    select = '*, xmin::text AS _read_version' if postgres else '*'
    categories = {}
    audit = []
    for row in transactions:
        # Read only taxonomy dependencies of this transaction's category. Apply
        # the report's own exact/subcategory-fallback logic to these entries.
        for item in _dicts(cur, 'SELECT ' + select + ''' FROM category_list
                WHERE lower(trim(category)) = lower(trim(?)) ORDER BY category, subcategory''', (row['category'] or '',)):
            categories[item['id']] = item
        audit.extend(_dicts(cur, 'SELECT ' + select +
            ' FROM transaction_change_log WHERE transaction_id = ? ORDER BY id', (row['id'],)))
        children = _dicts(cur, 'SELECT id FROM classified_transactions WHERE split_parent_id = ?', (row['id'],))
        if children:
            raise ValueError('Dependent SPLIT rows require separate review')
    categories = list(categories.values())
    audit.sort(key=lambda r: r['id'])
    tx_compare = {r['Record ID']: r for r in result['transactions']}
    balance_compare = {r['Balance ID']: r for r in result['sections']}
    fields, hashes = [], []
    def add(table, row, proposed):
        fields.extend(describe_fields(table, row, proposed))
        hashes.append({'Scope': table, 'Record ID': str(row['id']), 'Precondition SHA-256': state_hash(
            {'fingerprint': fingerprint, 'table': table, 'row': row})})
    taxonomy = pd.DataFrame(categories).fillna('')
    groups = _assign_report_groups(pd.DataFrame(transactions), taxonomy) if transactions else pd.DataFrame()
    for index, row in enumerate(transactions):
        add('classified_transactions', row, {'amount': Decimal(tx_compare[row['id']]['PDF amount'])})
        group = groups.iloc[index]['report_group']
        fields.append({'Table': 'classified_transactions', 'Record ID': row['id'],
            'Field': 'reporting_group (derived from category_list)', 'Current database value': str(group),
            'Proposed PDF/parser value': 'NOT APPLICABLE', 'Disposition': 'UNCHANGED / PRESERVE'})
    for row in balances:
        comparison = balance_compare[row['id']]
        # All document sections are part of the hash, including zero-row sections;
        # only mismatched balance records need expanded diagnostics.
        if comparison['Status'] == 'MISMATCH':
            add('statement_balances', row, {f: Decimal(comparison['PDF ' + f]) for f in MONEY['statement_balances']})
    for row in imports:
        add('statement_imports', row, {})
    for row in categories:
        add('category_list', row, {})
    for row in audit:
        # Unknown audit fields remain protected by the hash but are not exposed.
        displayed = dict(row)
        if row['field_name'] not in FIELDS['classified_transactions']:
            displayed.update(old_value='PRESERVED / NOT DISPLAYED', new_value='PRESERVED / NOT DISPLAYED')
        fields.extend(describe_fields('transaction_change_log', displayed, {}))
    envelope = {'fingerprint': fingerprint, 'schema': schema, 'transactions': sorted(transactions, key=lambda r: r['id']),
                'balances': sorted(balances, key=lambda r: r['id']), 'imports': sorted(imports, key=lambda r: r['id']),
                'taxonomy': categories, 'audit': audit}
    hashes.append({'Scope': 'complete import scope + schema + dependencies', 'Record ID': 'ALL',
                   'Precondition SHA-256': state_hash(envelope)})
    return {'schema': schema, 'fields': fields, 'hashes': hashes, 'audit_count': len(audit)}


def render(ui, diagnostics):
    ui.subheader('Repair Readiness Diagnostics')
    ui.info('Current production values when connected to production; otherwise current test-database values. '
            'Proposed values come only from the PDF/parser. UNCHANGED / PRESERVE is not an update instruction. '
            'Hashes are diagnostic preconditions, not repair authorization. Repair is unavailable.')
    ui.info(f"Related audit records: {diagnostics['audit_count']}. "
            'Exchange rates and USD amounts are preserved, not recalculated from the PDF. '
            'The rates table is inspected for column types only; unrelated rate rows are not read.')
    ui.dataframe(diagnostics['schema'], use_container_width=True, hide_index=True)
    ui.dataframe(diagnostics['fields'], use_container_width=True, hide_index=True)
    ui.dataframe(diagnostics['hashes'], use_container_width=True, hide_index=True)
