"""Explicit operator workflow; never imported by startup or exposed over HTTP.

In-database snapshots protect against this operation, not loss of the database.
Append-only triggers do not constrain a database owner/superuser.
"""
from contextlib import closing, nullcontext
from datetime import date, datetime
from decimal import Decimal
import hashlib
import json
import re

from psycopg2 import sql
from financial_schema import FINANCIAL_COLUMNS, REPAIR_FIELDS
from financial_remediation import _snapshot, _catalog_contract, LOCK_ID
from financial_preconditions import validate_locked, collect_locked, hash_index
from financial_conversion import preservation_manifest
from repair_readiness import _canonical, state_hash

TABLES = tuple(FINANCIAL_COLUMNS) + ('statement_imports', 'category_list',
    'transaction_change_log', 'account_list', 'transaction_memory')
JOURNAL = 'financial_recovery'
REJECT_BODY = " BEGIN RAISE EXCEPTION 'Recovery evidence is append-only'; END "


class Blocked(RuntimeError):
    """Only fixed, non-confidential error codes cross the command boundary."""


def require(value, code):
    if not value:
        raise Blocked(code)


def encode(value):
    return json.dumps(_canonical(value), ensure_ascii=True, separators=(',', ':'))


def decode(text):
    def unpack(value):
        tag, *parts = value
        if tag == 'object': return {k: unpack(v) for k, v in parts[0]}
        if tag == 'list': return [unpack(v) for v in parts[0]]
        if tag == 'null': return None
        if tag == 'decimal': return Decimal(parts[0])
        if tag == 'integer': return int(parts[0])
        # Lossless reconstruction of NON-financial metadata (e.g. confidence).
        if tag == 'float': return float.fromhex(parts[0])
        if tag in ('bool', 'text'): return parts[0]
        if tag == 'date': return date.fromisoformat(parts[0])
        if tag == 'datetime': return datetime.fromisoformat(parts[0])
        raise Blocked('SNAPSHOT_ENCODING_UNSUPPORTED')
    return unpack(json.loads(text))


def identity(cur):
    cur.execute('SELECT current_database(), inet_server_addr()::text, inet_server_port()')
    return state_hash(cur.fetchone())


def catalog(cur, review=None):
    if review is None:
        cur.execute("SELECT 1 FROM pg_event_trigger WHERE evtenabled<>'D'")
        require(not cur.fetchall(), 'DDL_EVENT_TRIGGERS_REQUIRE_SEPARATE_REVIEW')
    else:
        from nomad_execution_review import ReviewedNomad
        if type(review) is not ReviewedNomad:
            # Synthetic policy injection is confined to isolated local QA, not
            # a generic production "allow triggers/RLS" switch.
            parameters = cur.connection.get_dsn_parameters()
            require(parameters.get('host') in ('127.0.0.1','::1') and
                    parameters.get('dbname','').startswith('qa_financial_'),
                    'REVIEWED_PRODUCTION_POLICY_REQUIRED')
        review.execution_catalog(cur)
    _catalog_contract(cur, TABLES)
    cur.execute("SELECT c.relname,c.relkind FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' AND c.relname=ANY(%s) ORDER BY 1", (list(TABLES),))
    require(cur.fetchall() == [(t, 'r') for t in sorted(TABLES)], 'NONSTANDARD_TABLE_REQUIRES_REVIEW')
    cur.execute("SELECT 1 FROM pg_inherits i JOIN pg_class c ON c.oid=i.inhparent OR c.oid=i.inhrelid JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' AND c.relname=ANY(%s)", (list(TABLES),))
    require(not cur.fetchall(), 'INHERITANCE_REQUIRES_REVIEW')
    # SQL/PLpgSQL helpers can hide financial coercions or side effects not fully
    # represented by pg_depend. Never infer their safety from table hashes.
    cur.execute("SELECT p.oid FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname='public' AND NOT EXISTS (SELECT 1 FROM pg_depend d WHERE d.classid='pg_proc'::regclass AND d.objid=p.oid AND d.deptype='e') ORDER BY p.oid")
    functions = cur.fetchall()
    if review is None:
        require(not functions, 'PUBLIC_FUNCTIONS_REQUIRE_SEPARATE_REVIEW')
    else:
        review.public_functions(functions)
    cur.execute('''SELECT DISTINCT ns.nspname, c.relname FROM pg_constraint k
        JOIN pg_class c ON c.oid=k.conrelid JOIN pg_namespace ns ON ns.oid=c.relnamespace
        JOIN pg_class p ON p.oid=k.confrelid JOIN pg_namespace pn ON pn.oid=p.relnamespace
        WHERE k.contype='f' AND pn.nspname='public' AND p.relname=ANY(%s)''', (list(TABLES),))
    require(all(n == 'public' and t in TABLES for n, t in cur.fetchall()), 'UNKNOWN_INBOUND_DEPENDENCY')
    cur.execute('''SELECT DISTINCT pn.nspname,p.relname FROM pg_constraint k
        JOIN pg_class c ON c.oid=k.conrelid JOIN pg_namespace ns ON ns.oid=c.relnamespace
        JOIN pg_class p ON p.oid=k.confrelid JOIN pg_namespace pn ON pn.oid=p.relnamespace
        WHERE k.contype='f' AND ns.nspname='public' AND c.relname=ANY(%s)''', (list(TABLES),))
    require(all(n == 'public' and t in TABLES for n, t in cur.fetchall()), 'UNKNOWN_OUTBOUND_DEPENDENCY')
    result = {}
    queries = {
        'columns': "SELECT table_name,column_name,ordinal_position,data_type,udt_name,is_nullable,column_default,numeric_precision,numeric_scale FROM information_schema.columns WHERE table_schema='public' ORDER BY 1,3",
        'constraints': "SELECT c.relname,k.conname,k.contype,pg_get_constraintdef(k.oid) FROM pg_constraint k JOIN pg_class c ON c.oid=k.conrelid JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' ORDER BY 1,2",
        'indexes': "SELECT tablename,indexname,indexdef FROM pg_indexes WHERE schemaname='public' ORDER BY 1,2",
        'views': "SELECT viewname,definition FROM pg_views WHERE schemaname='public' ORDER BY 1",
        'policies': "SELECT tablename,policyname,permissive,roles::text,cmd,qual,with_check FROM pg_policies WHERE schemaname='public' ORDER BY 1,2",
        'security': "SELECT c.relname,c.relkind,c.relrowsecurity,c.relforcerowsecurity,pg_get_userbyid(c.relowner),c.relacl::text FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' ORDER BY 1",
    }
    for key, query in queries.items():
        cur.execute(query)
        result[key] = cur.fetchall()
    if review is None:
        require(not any(r[2] or r[3] for r in result['security'] if r[0] in TABLES), 'ROW_SECURITY_REQUIRES_REVIEW')
    else:
        review.security(cur, result)
    return result


def capture(cur, review=None):
    result = {'money': _snapshot(cur), 'catalog': catalog(cur, review), 'dependencies': {}, 'bits': {}, 'versions': {}}
    for table in TABLES:
        if table not in FINANCIAL_COLUMNS:
            cur.execute(sql.SQL('SELECT * FROM public.{} ORDER BY id').format(sql.Identifier(table)))
            names = [d[0] for d in cur.description]
            result['dependencies'][table] = [dict(zip(names, r)) for r in cur.fetchall()]
        cur.execute(sql.SQL('SELECT id,xmin::text FROM public.{} ORDER BY id').format(sql.Identifier(table)))
        result['versions'][table] = cur.fetchall()
    for table, field, kind, *_ in result['money']['schema']:
        if kind in ('real', 'double precision'):
            function = 'float4send' if kind == 'real' else 'float8send'
            cur.execute(sql.SQL("SELECT id,encode(pg_catalog.{}({}),'hex') FROM public.{} ORDER BY id").format(
                sql.Identifier(function), sql.Identifier(field), sql.Identifier(table)))
            result['bits'][table + '.' + field] = cur.fetchall()
    return result


def session(conn, readonly):
    from psycopg2.extensions import TRANSACTION_STATUS_IDLE
    require(conn.get_transaction_status() == TRANSACTION_STATUS_IDLE, 'IDLE_CONNECTION_REQUIRED')
    # Locked writes must see commits that completed while locks were acquired.
    # Read-only evidence uses one snapshot; write tables are stable under locks.
    conn.set_session(readonly=readonly, autocommit=False,
                     isolation_level='REPEATABLE READ' if readonly else 'READ COMMITTED')


def locks(cur):
    cur.execute("SET LOCAL lock_timeout='3s'")
    cur.execute("SET LOCAL statement_timeout='120s'")
    cur.execute('SELECT pg_try_advisory_xact_lock(%s)', (LOCK_ID,))
    require(cur.fetchone()[0], 'OPERATION_BUSY')
    cur.execute(sql.SQL('LOCK TABLE {} IN ACCESS EXCLUSIVE MODE').format(
        sql.SQL(',').join(sql.Identifier('public', t) for t in sorted(TABLES))))


def source(cur, content, manifest, review=None):
    catalog(cur, review)
    if review is not None:
        review.preconditions(cur, content, manifest)
    require(hashlib.sha256(content).hexdigest() == manifest['pdf_sha256'], 'PDF_FINGERPRINT_CHANGED')
    changes = manifest['changes']
    keys = {(r['table'], r['id'], r['field']) for r in changes}
    require(len(changes) == len(keys) == 18 and len({k[:2] for k in keys}) == 11, 'REPAIR_SCOPE_CHANGED')
    require(len(hash_index(manifest['preconditions'])) == 24, 'PRECONDITION_COUNT_CHANGED')
    require(all(f in REPAIR_FIELDS.get(t, ()) for t, _, f in keys), 'REPAIR_FIELD_FORBIDDEN')
    validate_locked(cur, content, manifest['preconditions'], changes)


def targets(before, manifest):
    values = {(t, r['id'], f): r[f] for t, rows in before['money']['tables'].items()
              for r in rows for f in FINANCIAL_COLUMNS[t]}
    rows = {(t, r['id']): r for t, rs in before['money']['tables'].items() for r in rs}
    for change in manifest['changes']:
        key = change['table'], change['id'], change['field']
        require(key in values and values[key] == Decimal(change['old']), 'EXPECTED_OLD_VALUE_CHANGED')
        require(rows[key[:2]]['statement_hash'] == change['statement_hash'], 'IMPORT_LINK_CHANGED')
        values[key] = Decimal(change['new'])
        require(values[key].is_finite(), 'NONFINITE_SOURCE_AMOUNT')
    return values


def reconcile(cur, content):
    from financial_decimal import exact_sum
    comparison, _ = collect_locked(cur, content)
    require(len(comparison['sections']) == 6 and len(comparison['transactions']) == 9, 'SOURCE_COUNTS_CHANGED')
    require(all(r['Status'] == 'MATCH' for r in comparison['sections'] + comparison['transactions']), 'SOURCE_RECONCILIATION_FAILED')
    for s in comparison['sections']:
        require(exact_sum([Decimal(s['Stored opening_balance']), Decimal(s['Stored money_in']),
            Decimal(s['Stored money_out']).copy_negate()]) == Decimal(s['Stored closing_balance']), 'BALANCE_EQUATION_FAILED')


def prepare(conn, content, manifest, review=None):
    """Read-only plan digest; full values are never printed or returned to UI."""
    session(conn, True)
    try:
        with conn.cursor() as cur:
            source(cur, content, manifest, review)
            state = capture(cur, review)
            targets(state, manifest)
            return {'state_hash': state_hash(state), 'database_hash': identity(cur),
                    'manifest_hash': state_hash(manifest), 'pdf_sha256': manifest['pdf_sha256']}
    finally:
        conn.rollback()


def journal(cur, create=False):
    cur.execute('SELECT to_regnamespace(%s)', (JOURNAL,))
    exists = cur.fetchone()[0] is not None
    if not exists:
        require(create, 'SNAPSHOT_NOT_FOUND')
        cur.execute('CREATE SCHEMA financial_recovery')
        cur.execute('REVOKE ALL ON SCHEMA financial_recovery FROM PUBLIC')
        cur.execute('''CREATE TABLE financial_recovery.snapshots (
            operation_id text PRIMARY KEY, payload text NOT NULL, payload_hash text NOT NULL,
            actor text NOT NULL, recorded_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP)''')
        cur.execute('''CREATE TABLE financial_recovery.events (
            operation_id text REFERENCES financial_recovery.snapshots(operation_id),
            kind text CHECK(kind IN ('APPLY','REVERSE')), payload text NOT NULL,
            payload_hash text NOT NULL, recorded_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY(operation_id,kind))''')
        cur.execute('CREATE FUNCTION financial_recovery.reject_mutation() RETURNS trigger LANGUAGE plpgsql AS %s', (REJECT_BODY,))
        for table in ('snapshots', 'events'):
            cur.execute(sql.SQL('REVOKE ALL ON financial_recovery.{} FROM PUBLIC').format(sql.Identifier(table)))
            cur.execute(sql.SQL('CREATE TRIGGER immutable BEFORE UPDATE OR DELETE OR TRUNCATE ON financial_recovery.{} FOR EACH STATEMENT EXECUTE FUNCTION financial_recovery.reject_mutation()').format(sql.Identifier(table)))
            cur.execute(sql.SQL('ALTER TABLE financial_recovery.{} ENABLE ALWAYS TRIGGER immutable').format(sql.Identifier(table)))
    audit_guard(cur)
    cur.execute('LOCK TABLE financial_recovery.snapshots,financial_recovery.events IN ACCESS EXCLUSIVE MODE')


def audit_guard(cur):
    # Existing recovery objects must retain the exact mutation guards.
    cur.execute("SELECT prosrc FROM pg_proc WHERE oid='financial_recovery.reject_mutation()'::regprocedure")
    require(cur.fetchone() == (REJECT_BODY,), 'AUDIT_GUARD_CHANGED')
    cur.execute("SELECT c.relname,t.tgtype,t.tgenabled,t.tgfoid='financial_recovery.reject_mutation()'::regprocedure FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname=%s AND NOT t.tgisinternal ORDER BY 1", (JOURNAL,))
    require(cur.fetchall() == [('events', 58, 'A', True), ('snapshots', 58, 'A', True)], 'AUDIT_TRIGGER_CHANGED')
    cur.execute("SELECT 1 FROM pg_namespace n CROSS JOIN LATERAL aclexplode(n.nspacl) a WHERE n.nspname=%s AND a.grantee<>n.nspowner", (JOURNAL,))
    require(not cur.fetchall(), 'AUDIT_SCHEMA_GRANTS_REQUIRE_REVIEW')
    cur.execute("SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace CROSS JOIN LATERAL aclexplode(c.relacl) a WHERE n.nspname=%s AND a.grantee<>c.relowner", (JOURNAL,))
    require(not cur.fetchall(), 'AUDIT_TABLE_GRANTS_REQUIRE_REVIEW')
    cur.execute("SELECT 1 FROM pg_rewrite r JOIN pg_class c ON c.oid=r.ev_class JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname=%s", (JOURNAL,))
    require(not cur.fetchall(), 'AUDIT_RULES_REQUIRE_REVIEW')


def stored(cur, operation, kind=None):
    table = 'snapshots' if kind is None else 'events'
    query = sql.SQL('SELECT payload,payload_hash FROM financial_recovery.{} WHERE operation_id=%s').format(sql.Identifier(table))
    if kind is not None: query += sql.SQL(' AND kind=%s')
    cur.execute(query, (operation,) if kind is None else (operation, kind))
    row = cur.fetchone()
    if row is None: return None
    value = decode(row[0])
    require(state_hash(value) == row[1], 'SNAPSHOT_INTEGRITY_FAILED')
    return value


def event(cur, operation, kind, state):
    cur.execute('INSERT INTO financial_recovery.events(operation_id,kind,payload,payload_hash) VALUES (%s,%s,%s,%s)',
                (operation, kind, encode(state), state_hash(state)))


def write_values(cur, values):
    current = _snapshot(cur)
    kinds = {(r[0], r[1]): r[2] for r in current['schema']}
    for table, rows in current['tables'].items():
        for row in rows:
            for field in FINANCIAL_COLUMNS[table]:
                value = values[table, row['id'], field]
                # Include exponent preservation for authoritative source cents.
                if encode(row[field]) != encode(value):
                    bound = (str(value) if value is not None and kinds[table, field] != 'numeric' else value)
                    cur.execute(sql.SQL('UPDATE public.{} SET {}=%s WHERE id=%s AND {} IS NOT DISTINCT FROM %s').format(
                        sql.Identifier(table), sql.Identifier(field), sql.Identifier(field)), (bound, row['id'], row[field]))
                    require(cur.rowcount == 1, 'EXPECTED_OLD_UPDATE_FAILED')


def check_after(before, after, values):
    for table, rows in before['money']['tables'].items():
        expected = [dict(row, **{f: values[table, row['id'], f] for f in FINANCIAL_COLUMNS[table]}) for row in rows]
        actual = after['money']['tables'][table]
        require(len(actual) == len(expected), 'ROW_COUNT_CHANGED')
        for new, old in zip(actual, expected):
            require(new.keys() == old.keys(), 'ROW_FIELDS_CHANGED')
            for field in old:
                # NUMERIC does not distinguish IEEE negative zero. Its original
                # bits remain in the snapshot and are checked during reverse.
                same = new[field] == old[field] if field in FINANCIAL_COLUMNS[table] else encode(new[field]) == encode(old[field])
                require(same, 'DATA_OR_METADATA_CHANGED')
    require(encode(after['dependencies']) == encode(before['dependencies']), 'DEPENDENCY_CHANGED')
    for old, new in zip(before['money']['schema'], after['money']['schema']):
        require(tuple(old[:2]) == tuple(new[:2]) and tuple(old[5:]) == tuple(new[5:])
                and tuple(new[2:5]) == ('numeric', None, None), 'SCHEMA_CONTRACT_CHANGED')
    for key in before['catalog']:
        if key != 'columns':
            require(encode(before['catalog'][key]) == encode(after['catalog'][key]), 'CATALOG_DEPENDENCY_CHANGED')
    financial = {(t, f) for t, fs in FINANCIAL_COLUMNS.items() for f in fs}
    for old, new in zip(before['catalog']['columns'], after['catalog']['columns']):
        expected = list(old)
        if tuple(old[:2]) in financial:
            expected[3:5] = ['numeric', 'numeric']
            expected[7:9] = [None, None]
        require(encode(expected) == encode(list(new)), 'COLUMN_CONTRACT_CHANGED')


def apply(connect, *, content, manifest, plan, operation_id, actor, fail_at=None,
          controller=None, review=None, before_commit=None):
    """Explicit trusted server call. No provider/manual-backup Boolean gate."""
    require(re.fullmatch('[A-Za-z0-9_-]{1,80}', operation_id or '') and actor, 'OPERATOR_ID_REQUIRED')
    require(plan['manifest_hash'] == state_hash(manifest) and plan['pdf_sha256'] == hashlib.sha256(content).hexdigest(), 'APPROVAL_CHANGED')
    attempted = False
    require((controller is None and review is None and before_commit is None)
            or (controller is not None and review is not None and before_commit is not None), 'CONTROLLER_CONTRACT_REQUIRED')
    with (closing(connect()) if controller is None else nullcontext(controller)) as conn:
        session(conn, False)
        try:
            with conn.cursor() as cur:
                if controller is None:
                    from financial_writer_control import writer_guard
                    writer_guard(cur)
                else:
                    from financial_writer_control import assert_owned
                    assert_owned(conn)
                locks(cur)
                require(identity(cur) == plan['database_hash'], 'WRONG_DATABASE')
                cur.execute('SELECT to_regnamespace(%s)', (JOURNAL,))
                if cur.fetchone()[0] is not None:
                    journal(cur)
                    prior = stored(cur, operation_id)
                    if prior is not None:
                        require(prior['plan'] == plan, 'OPERATION_BINDING_CHANGED')
                        require(stored(cur, operation_id, 'REVERSE') is None, 'OPERATION_ALREADY_REVERSED')
                        require(state_hash(capture(cur, review)) == state_hash(stored(cur, operation_id, 'APPLY')), 'POST_STATE_CHANGED')
                        reconcile(cur, content)
                        conn.rollback()
                        return {'repeated': True, 'changed': 0}
                source(cur, content, manifest, review)
                before = capture(cur, review)
                require(state_hash(before) == plan['state_hash'], 'LOCKED_PRECONDITION_CHANGED')
                values = targets(before, manifest)
                if review is not None: review.execution_catalog(cur)
                journal(cur, create=True)
                payload = {'before': before, 'plan': plan, 'manifest': manifest,
                           'policy': preservation_manifest(before['money'])}
                cur.execute('INSERT INTO financial_recovery.snapshots(operation_id,payload,payload_hash,actor) VALUES (%s,%s,%s,%s)',
                            (operation_id, encode(payload), state_hash(payload), actor))
                require(fail_at != 'snapshot', 'INJECTED_FAILURE')
                for table, field, kind, *_ in before['money']['schema']:
                    if kind != 'numeric':
                        cur.execute(sql.SQL('ALTER TABLE public.{} ALTER COLUMN {} TYPE numeric USING {}::numeric').format(
                            sql.Identifier(table), sql.Identifier(field), sql.Identifier(field)))
                require(fail_at != 'ddl', 'INJECTED_FAILURE')
                write_values(cur, values)
                require(fail_at != 'repair', 'INJECTED_FAILURE')
                after = capture(cur, review)
                check_after(before, after, values)
                reconcile(cur, content)
                event(cur, operation_id, 'APPLY', after)
                require(fail_at != 'audit', 'INJECTED_FAILURE')
                if before_commit is not None: before_commit(conn)
            attempted = True
            conn.commit()
        except BaseException:
            try: conn.rollback()
            except Exception: pass
            if attempted: raise Blocked('COMMIT_UNCERTAIN_KEEP_WRITERS_CLOSED_VERIFY') from None
            raise
    if controller is not None:
        return {'changed': 18, 'repeated': False}
    try:
        verify(connect, operation_id=operation_id, content=content)
    except BaseException:
        raise Blocked('POST_COMMIT_VERIFY_FAILED_KEEP_WRITERS_CLOSED') from None
    return {'changed': 18, 'repeated': False}


def verify(connect, *, operation_id, content, reversed_state=False, review=None):
    with closing(connect()) as conn:
        session(conn, True)
        try:
            with conn.cursor() as cur:
                audit_guard(cur)
                snapshot = stored(cur, operation_id)
                require(snapshot is not None, 'SNAPSHOT_NOT_FOUND')
                require(identity(cur) == snapshot['plan']['database_hash'], 'WRONG_DATABASE')
                require(hashlib.sha256(content).hexdigest() == snapshot['manifest']['pdf_sha256'], 'PDF_FINGERPRINT_CHANGED')
                expected = stored(cur, operation_id, 'REVERSE' if reversed_state else 'APPLY')
                require(expected is not None and state_hash(capture(cur, review)) == state_hash(expected), 'POST_STATE_CHANGED')
                if not reversed_state: reconcile(cur, content)
        finally:
            conn.rollback()
    return {'verified': True, 'reversed': reversed_state, 'after_hash': state_hash(expected)}


def reverse(connect, *, operation_id, content, expected_after_hash, fail_at=None):
    """Restore only from the recorded preimage, refusing every intervening row edit."""
    attempted = False
    with closing(connect()) as conn:
        session(conn, False)
        try:
            with conn.cursor() as cur:
                from financial_writer_control import writer_guard
                writer_guard(cur)
                locks(cur)
                journal(cur)
                snapshot = stored(cur, operation_id)
                require(snapshot is not None, 'SNAPSHOT_NOT_FOUND')
                require(identity(cur) == snapshot['plan']['database_hash'], 'WRONG_DATABASE')
                require(hashlib.sha256(content).hexdigest() == snapshot['manifest']['pdf_sha256'], 'PDF_FINGERPRINT_CHANGED')
                applied = stored(cur, operation_id, 'APPLY')
                require(applied is not None and state_hash(applied) == expected_after_hash, 'REVERSE_APPROVAL_CHANGED')
                prior = stored(cur, operation_id, 'REVERSE')
                current = capture(cur)
                if prior is not None:
                    require(state_hash(current) == state_hash(prior), 'POST_REVERSE_STATE_CHANGED')
                    conn.rollback()
                    return {'repeated': True, 'reversed': True}
                require(state_hash(current) == expected_after_hash, 'NEWER_CHANGES_BLOCK_REVERSE')
                before = snapshot['before']
                values = {(t, r['id'], f): r[f] for t, rows in before['money']['tables'].items()
                          for r in rows for f in FINANCIAL_COLUMNS[t]}
                # Restore and verify the exact preimage in NUMERIC first. Only
                # those proven legacy values may be encoded into the old types;
                # never cast the repaired/current values or stage invalid NULLs.
                write_values(cur, values)
                preimage = _snapshot(cur)
                for table, rows in preimage['tables'].items():
                    for row in rows:
                        for field in FINANCIAL_COLUMNS[table]:
                            require(row[field] == values[table, row['id'], field], 'REVERSE_PREIMAGE_FAILED')
                for table, field, kind, *_ in before['money']['schema']:
                    if kind == 'numeric': continue
                    require(kind in ('real', 'double precision'), 'REVERSE_TYPE_UNSUPPORTED')
                    cur.execute(sql.SQL('ALTER TABLE public.{} ALTER COLUMN {} TYPE {} USING {}::{}').format(
                        sql.Identifier(table), sql.Identifier(field), sql.SQL(kind), sql.Identifier(field), sql.SQL(kind)))
                # Recreate IEEE negative zero from the snapshot when applicable.
                write_values(cur, values)
                restored = capture(cur)
                for key in ('money', 'dependencies', 'catalog', 'bits'):
                    require(encode(restored[key]) == encode(before[key]), 'REVERSE_READBACK_FAILED')
                require(fail_at != 'reverse', 'INJECTED_FAILURE')
                event(cur, operation_id, 'REVERSE', restored)
            attempted = True
            conn.commit()
        except BaseException:
            try: conn.rollback()
            except Exception: pass
            if attempted: raise Blocked('REVERSE_COMMIT_UNCERTAIN_KEEP_WRITERS_CLOSED') from None
            raise
    verify(connect, operation_id=operation_id, content=content, reversed_state=True)
    return {'repeated': False, 'reversed': True}
