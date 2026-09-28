"""Isolated LOCAL rehearsal only. No application integration or production entry point.

Conversion requires an explicit full-table manifest. It never infers original
decimal values from floats. Source repair is a separate, field-allowlisted step.
"""
from decimal import Decimal
import json
from pathlib import Path
import struct

from financial_decimal import decimal_value
from financial_schema import FINANCIAL_COLUMNS, REPAIR_FIELDS
from repair_readiness import state_hash


class RemediationBlocked(RuntimeError):
    pass


LOCK_ID = 617294381


def _local_only(connection):
    from psycopg2.extensions import TRANSACTION_STATUS_IDLE
    parameters = connection.get_dsn_parameters()
    if (parameters.get('host') not in ('127.0.0.1', '::1')
            or not parameters.get('dbname', '').startswith('qa_financial_')
            or connection.get_transaction_status() != TRANSACTION_STATUS_IDLE):
        raise RemediationBlocked('Only idle, isolated local QA databases are supported')


def inspect_schema(cursor):
    cursor.execute('''SELECT table_name, column_name, data_type, numeric_precision,
        numeric_scale, is_nullable, column_default, domain_name
        FROM information_schema.columns WHERE table_schema='public'
        AND table_name = ANY(%s) ORDER BY table_name, ordinal_position''', (list(FINANCIAL_COLUMNS),))
    selected = [tuple(r) for r in cursor.fetchall() if r[1] in FINANCIAL_COLUMNS.get(r[0], ())]
    if len(selected) != 9:
        raise RemediationBlocked('Incomplete financial schema')
    for _, _, kind, precision, scale, _, _, domain in selected:
        if domain is not None or kind not in ('real', 'double precision', 'numeric'):
            raise RemediationBlocked('Unsupported monetary schema')
        if kind == 'numeric' and (precision is not None or scale is not None):
            raise RemediationBlocked('Constrained numeric requires independent review')
    return selected


def _rows(cursor, table, schema):
    from psycopg2 import sql
    types = {r[1]: r[2] for r in schema if r[0] == table}
    extra = []
    for column, kind in types.items():
        if kind in ('real', 'double precision'):
            function = 'float4send' if kind == 'real' else 'float8send'
            extra.append(sql.SQL('{}({}) AS {}').format(sql.Identifier('pg_catalog', function),
                         sql.Identifier(column), sql.Identifier('_binary_' + column)))
    query = sql.SQL('SELECT *{extra} FROM {table} ORDER BY id').format(
        extra=sql.SQL(', ') + sql.SQL(', ').join(extra) if extra else sql.SQL(''),
        table=sql.Identifier('public', table))
    cursor.execute(query)
    names = [d[0] for d in cursor.description]
    rows = [dict(zip(names, r)) for r in cursor.fetchall()]
    for row in rows:
        for column, kind in types.items():
            if kind in ('real', 'double precision'):
                raw = row.pop('_binary_' + column)
                row[column] = None if raw is None else Decimal.from_float(
                    struct.unpack('!f' if kind == 'real' else '!d', bytes(raw))[0])
                if row[column] is not None:
                    decimal_value(row[column])
    return rows


def _snapshot(cursor):
    schema = inspect_schema(cursor)
    return {'schema': schema, 'tables': {t: _rows(cursor, t, schema) for t in FINANCIAL_COLUMNS}}


def _catalog_contract(cursor, tables):
    cursor.execute('''SELECT t.tgname FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid
        JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname='public' AND c.relname=ANY(%s) AND NOT t.tgisinternal''', (list(tables),))
    if cursor.fetchall():
        raise RemediationBlocked('Unreviewed user trigger could mutate dependent records')
    cursor.execute('''SELECT r.rulename FROM pg_rewrite r JOIN pg_class c ON c.oid=r.ev_class
        JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname='public' AND c.relname=ANY(%s)''', (list(tables),))
    if cursor.fetchall():
        raise RemediationBlocked('Unreviewed table rule requires separate dependency review')
    cursor.execute('''SELECT c.relname, k.conname, k.contype, pg_get_constraintdef(k.oid)
        FROM pg_constraint k JOIN pg_class c ON c.oid=k.conrelid
        JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname='public' AND c.relname=ANY(%s)
        ORDER BY c.relname,k.conname''', (list(tables),))
    return cursor.fetchall()


def inspect_local(connection):
    _local_only(connection)
    connection.set_session(readonly=True, autocommit=False)
    try:
        with connection.cursor() as cursor:
            cursor.execute('SHOW data_directory')
            if Path(cursor.fetchone()[0]).drive.upper() != 'E:':
                raise RemediationBlocked('QA data directory must be on E:')
            snapshot = _snapshot(cursor)
        return snapshot, state_hash(snapshot)
    finally:
        connection.rollback()


def _conversion_targets(snapshot, conversions):
    required = {(table, row['id'], column): row[column]
                for table, rows in snapshot['tables'].items() for row in rows
                for column in FINANCIAL_COLUMNS[table]}
    supplied = {}
    for item in conversions:
        key = (item['table'], item['id'], item['field'])
        if key in supplied or key not in required or not item.get('evidence'):
            raise RemediationBlocked('Conversion manifest is incomplete or ambiguous')
        old = None if item['old'] is None else decimal_value(item['old'])
        new = None if item['new'] is None else decimal_value(item['new'])
        policy = item.get('policy')
        kind = next(r[2] for r in snapshot['schema'] if r[:2] == key[::2])
        expected_policy = ('NULL_PRESERVE' if old is None else
                           'EXACT_NUMERIC_PRESERVE' if kind == 'numeric' else 'LEGACY_BINARY_UNVERIFIED')
        if policy != expected_policy or new != old:
            raise RemediationBlocked('Historical conversion must preserve the exact stored value with explicit provenance')
        if required[key] != old or (old is None) != (new is None):
            raise RemediationBlocked('Conversion precondition or NULL semantics changed')
        supplied[key] = new
    if supplied.keys() != required.keys():
        raise RemediationBlocked('Every historical monetary field needs an approved conversion')
    return supplied


def _audit_tables(cursor):
    cursor.execute('''CREATE TABLE IF NOT EXISTS public.financial_rehearsal_audit (
        operation_id TEXT PRIMARY KEY, manifest_hash TEXT NOT NULL,
        before_hash TEXT NOT NULL, after_hash TEXT NOT NULL,
        before_snapshot TEXT NOT NULL, changes TEXT NOT NULL,
        actor TEXT NOT NULL, recorded_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP)''')
    cursor.execute('''CREATE OR REPLACE FUNCTION public.reject_financial_audit_mutation()
        RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN
        RAISE EXCEPTION 'Financial audit is append-only'; END $$''')
    cursor.execute('''CREATE TRIGGER financial_audit_append_only
        BEFORE UPDATE OR DELETE OR TRUNCATE ON public.financial_rehearsal_audit
        FOR EACH STATEMENT EXECUTE FUNCTION public.reject_financial_audit_mutation()''')


def rehearse(connection, *, expected_hash, conversions, repairs, operation_id,
             actor, backup_verified=False, fail_after=None, readiness=None):
    """LOCAL ONLY: one atomic migration + source repair + append-only audit.

    The caller's conversion manifest is mandatory even for values to preserve.
    Production authorization, readiness-envelope linkage and backup verification
    are deliberately not exposed as an application capability.
    """
    from psycopg2 import sql
    _local_only(connection)
    if not backup_verified or not actor or not operation_id:
        raise RemediationBlocked('Verified local backup, actor and operation ID required')
    import hashlib
    from financial_preconditions import DEPENDENCY_TABLES, validate_locked
    proof = None if readiness is None else {
        'fingerprint': hashlib.sha256(readiness['content']).hexdigest(),
        'hashes': readiness['hashes']}
    manifest_hash = state_hash({'conversions': conversions, 'repairs': repairs,
                               'expected': expected_hash, 'readiness': proof})
    connection.set_session(readonly=False, autocommit=False)
    try:
        with connection.cursor() as cursor:
            cursor.execute('SHOW data_directory')
            if Path(cursor.fetchone()[0]).drive.upper() != 'E:':
                raise RemediationBlocked('QA data directory must be on E:')
            cursor.execute("SET LOCAL lock_timeout='3s'")
            cursor.execute("SET LOCAL statement_timeout='60s'")
            cursor.execute('SELECT pg_try_advisory_xact_lock(%s)', (LOCK_ID,))
            if not cursor.fetchone()[0]:
                raise RemediationBlocked('Financial rehearsal already running')
            locked_tables = tuple(FINANCIAL_COLUMNS) + (DEPENDENCY_TABLES if readiness is not None else ())
            cursor.execute(sql.SQL('LOCK TABLE {} IN ACCESS EXCLUSIVE MODE').format(
                sql.SQL(', ').join(sql.Identifier('public', t) for t in locked_tables)))
            catalog_before = _catalog_contract(cursor, locked_tables)
            before = _snapshot(cursor)
            def dependencies():
                result = {}
                if readiness is not None:
                    for table in DEPENDENCY_TABLES:
                        cursor.execute(sql.SQL('SELECT * FROM {} ORDER BY id').format(sql.Identifier('public', table)))
                        result[table] = cursor.fetchall()
                return result
            dependency_before = dependencies()
            def postcondition(snapshot):
                if readiness is None:
                    return state_hash(snapshot)
                versions = {}
                for table in locked_tables:
                    cursor.execute(sql.SQL('SELECT id, xmin::text FROM {} ORDER BY id').format(sql.Identifier('public', table)))
                    versions[table] = cursor.fetchall()
                return state_hash({'snapshot': snapshot, 'dependencies': dependencies(), 'versions': versions})
            cursor.execute("SELECT to_regclass('public.financial_rehearsal_audit')")
            audit_exists = cursor.fetchone()[0] is not None
            if audit_exists:
                cursor.execute('SELECT manifest_hash, after_hash FROM public.financial_rehearsal_audit WHERE operation_id=%s', (operation_id,))
                prior = cursor.fetchone()
                if prior:
                    if prior != (manifest_hash, postcondition(before)):
                        raise RemediationBlocked('Prior operation or current state differs')
                    connection.rollback()
                    return {'changed': 0, 'repeated': True}
            if state_hash(before) != expected_hash:
                raise RemediationBlocked('Snapshot changed since diagnosis')
            if readiness is not None:
                try:
                    validate_locked(cursor, readiness['content'], readiness['hashes'], repairs)
                except ValueError as exc:
                    raise RemediationBlocked('Source or exported readiness preconditions failed') from exc
            targets = _conversion_targets(before, conversions)
            source_rows = {(t, r['id']): r for t, rows in before['tables'].items() for r in rows}
            seen = set()
            for change in repairs:
                key = (change['table'], change['id'], change['field'])
                if (key in seen or key not in targets
                        or change['field'] not in REPAIR_FIELDS.get(change['table'], ())):
                    raise RemediationBlocked('Repair field is not allowlisted')
                seen.add(key)
                row = source_rows[key[:2]]
                if row.get('statement_hash') != change['statement_hash']:
                    raise RemediationBlocked('Import fingerprint changed')
                if any(row.get(f) is not None for f in ('split_parent_id', 'split_group_id', 'split_allocation_index', 'split_original_amount')):
                    raise RemediationBlocked('SPLIT requires separate approval')
                if any(r.get('split_parent_id') == change['id'] for r in before['tables']['classified_transactions']) and change['table'] == 'classified_transactions':
                    raise RemediationBlocked('Dependent split rows exist')
                if row[change['field']] != decimal_value(change['old']):
                    raise RemediationBlocked('Expected old value changed')
                targets[key] = decimal_value(change['new'])
            if (all(r[2] == 'numeric' for r in before['schema'])
                    and all(source_rows[(table, row_id)][field] == value
                            for (table, row_id, field), value in targets.items())):
                connection.rollback()
                return {'changed': 0, 'repeated': True}
            for table, column, kind, *_ in before['schema']:
                if kind != 'numeric':
                    cursor.execute(sql.SQL('ALTER TABLE {} ALTER COLUMN {} TYPE NUMERIC USING {}::numeric').format(
                        sql.Identifier('public', table), sql.Identifier(column), sql.Identifier(column)))
            # Restore each explicitly approved exact target in the same locked
            # transaction. The temporary cast is never an authoritative value.
            cast = _snapshot(cursor)
            cast_rows = {(t, r['id']): r for t, rows in cast['tables'].items() for r in rows}
            for index, ((table, row_id, field), value) in enumerate(targets.items(), 1):
                if cast_rows[table, row_id][field] != value:
                    cursor.execute(sql.SQL('UPDATE {} SET {}=%s WHERE id=%s').format(
                        sql.Identifier('public', table), sql.Identifier(field)), (value, row_id))
                    if cursor.rowcount != 1:
                        raise RemediationBlocked('Target row count changed')
                if fail_after == index:
                    raise RemediationBlocked('Injected local rehearsal failure')
            after = _snapshot(cursor)
            if _catalog_contract(cursor, locked_tables) != catalog_before:
                raise RemediationBlocked('Constraint definitions changed')
            if dependencies() != dependency_before:
                raise RemediationBlocked('Import, classification or audit dependencies changed')
            for table, rows in after['tables'].items():
                for row in rows:
                    original = source_rows[(table, row['id'])]
                    for field, value in row.items():
                        expected = targets[(table, row['id'], field)] if field in FINANCIAL_COLUMNS[table] else original[field]
                        if value != expected:
                            raise RemediationBlocked('Read-back or metadata preservation failed')
            for old, new in zip(before['schema'], after['schema']):
                if old[5:] != new[5:] or new[2:5] != ('numeric', None, None):
                    raise RemediationBlocked('Schema contract changed')
            if not audit_exists:
                _audit_tables(cursor)
            cursor.execute('''INSERT INTO public.financial_rehearsal_audit
                (operation_id,manifest_hash,before_hash,after_hash,before_snapshot,changes,actor)
                VALUES (%s,%s,%s,%s,%s,%s,%s)''',
                (operation_id, manifest_hash, expected_hash, postcondition(after),
                 json.dumps(dict(before, dependencies=dependency_before), default=str),
                 json.dumps({'repairs': repairs, 'conversion_policy': conversions, 'readiness': proof}, default=str), actor))
        connection.commit()
        return {'changed': len(repairs), 'repeated': False}
    except Exception:
        connection.rollback()
        raise


def rehearse_existing_import(connection, *, content, exported_hashes, **kwargs):
    """Source-backed entry point: the complete export contract is mandatory."""
    from financial_preconditions import hash_index
    hash_index(exported_hashes)
    return rehearse(connection, readiness={'content': content, 'hashes': exported_hashes}, **kwargs)
