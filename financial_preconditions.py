"""Readiness-v1 binding for isolated migration rehearsals; no connection creation."""
import hashlib
import re


DEPENDENCY_TABLES = ('statement_imports', 'category_list', 'transaction_change_log')


def hash_index(rows):
    result = {}
    for row in rows:
        key = (row['Scope'], str(row['Record ID']))
        value = row['Precondition SHA-256']
        if key in result or not isinstance(value, str) or not re.fullmatch('[0-9a-f]{64}', value):
            raise ValueError('Invalid or duplicate readiness precondition')
        result[key] = value
    if ('complete import scope + schema + dependencies', 'ALL') not in result:
        raise ValueError('Complete readiness precondition is required')
    return result


def collect_locked(cursor, content):
    """Caller holds locks before invoking the exact deployed hashing contract."""
    from db import PostgresCursor
    from existing_import_compare import _expected, _schema, _dicts, _money_select, _decode, _reconcile
    from repair_readiness import collect
    cur = PostgresCursor(cursor)
    fingerprint = hashlib.sha256(content).hexdigest()
    sections = _expected(content)
    types = _schema(cur, True)
    balances = _decode(_dicts(cur, 'SELECT ' + _money_select('statement_balances', types, True)
        + ' FROM statement_balances WHERE source = ? ORDER BY id', ('Safra document ' + fingerprint,)),
        'statement_balances', types, True)
    imports, transactions = [], []
    for balance in balances:
        key = balance['statement_hash']
        imports.extend(_dicts(cur, 'SELECT *, xmin::text AS _read_version FROM statement_imports WHERE statement_hash = ?', (key,)))
        transactions.extend(_decode(_dicts(cur, 'SELECT ' + _money_select('classified_transactions', types, True)
            + ' FROM classified_transactions WHERE statement_hash = ? ORDER BY id', (key,)),
            'classified_transactions', types, True))
    comparison = _reconcile(sections, balances, imports, transactions)
    diagnostics = collect(cur, True, fingerprint, comparison, balances, imports, transactions)
    return comparison, diagnostics


def validate_locked(cursor, content, expected_hashes, repairs):
    from decimal import Decimal
    comparison, diagnostics = collect_locked(cursor, content)
    if hash_index(expected_hashes) != hash_index(diagnostics['hashes']):
        raise ValueError('Readiness preconditions changed since export')
    expected = {}
    for row in diagnostics['fields']:
        if row['Disposition'] == 'PDF DIFFERENCE - DIAGNOSTIC ONLY':
            expected[(row['Table'], row['Record ID'], row['Field'])] = (
                Decimal(row['Current database value']), Decimal(row['Proposed PDF/parser value']))
    actual = {}
    for row in repairs:
        key = (row['table'], row['id'], row['field'])
        if key in actual:
            raise ValueError('Duplicate repair target')
        actual[key] = (Decimal(row['old']), Decimal(row['new']))
    if actual != expected:
        raise ValueError('Repair allowlist differs from authoritative parser comparison')
    return diagnostics
