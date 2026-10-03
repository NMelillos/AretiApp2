"""Fixed, bounded diagnostics. No business data, repair imports or command execution."""
from datetime import datetime, timezone
import hashlib
import json
import logging
import re
import time
from types import MappingProxyType

from ops_auth import require_owner
import ops_db

COMMANDS = ('status', 'health', 'deployment', 'release-identity', 'db-connection-test',
            'financial-schema', 'writer-status', 'repair-status', 'nomad-status', 'recent-errors', 'help')
STATES = {'NORMAL', 'REPAIR_IN_PROGRESS', 'REPAIR_COMMITTED_UNVERIFIED', 'RECOVERY_REQUIRED'}
OPERATION = 'nomad-frozen-final-v1'
REASONS = {'ACTUAL_IDENTITY_UNAVAILABLE', 'ACTUAL_IDENTITY_INVALID', 'ACTUAL_CODE_CHANGED',
           'RENDER_IDENTITY_CONFLICT', 'APPROVED_IDENTITY_INVALID', 'APPROVED_IDENTITY_UNAVAILABLE',
           'RELEASE_IDENTITY_MISMATCH'}
AUDIT = logging.getLogger('areti.ops')
if not AUDIT.handlers:
    AUDIT.addHandler(logging.StreamHandler())
AUDIT.setLevel(logging.INFO)
AUDIT.propagate = False


def deployment():
    import deployment_identity
    # Use the existing rules and artifact; never run its Git subprocess fallback.
    result = deployment_identity.release_status(allow_git=False)
    sha = lambda value: value if isinstance(value, str) and re.fullmatch('[0-9a-f]{40}', value) else 'NOT AVAILABLE'
    reason = result.get('reason', '')
    return dict(status='PASS' if result.get('status') == 'PASS' else 'BLOCKED',
                deployed_sha=sha(result.get('deployed_sha')), approved_sha=sha(result.get('approved_sha')),
                render_sha=sha(result.get('render_sha')),
                reason=reason if reason in REASONS else ('DIAGNOSTIC_UNAVAILABLE' if reason else ''))


def db_info(reader):
    start = time.perf_counter()
    if reader.read('ping') != [(1,)]: raise ops_db.ReadOnlyUnavailable()
    latency = round((time.perf_counter() - start) * 1000, 2)
    version = reader.read('version')[0][0]
    match = re.match(r'^([0-9]+(?:\.[0-9]+){0,2})(?:\s|$)', version) if isinstance(version, str) else None
    if not match: raise ops_db.ReadOnlyUnavailable()
    return dict(status='PASS', database='CONNECTED', postgresql_version=match[1],
                read_only='on', query_latency_ms=latency)


def schema_info(reader):
    rows = reader.read('schema')
    found = {(row[0], row[1]): row for row in rows}
    failures = []
    for table, column in ops_db.FIELDS:
        row = found.get((table, column))
        if row is None:
            failures.append({'field': table + '.' + column, 'actual_type': 'NOT AVAILABLE'})
        elif row[2:] != ('numeric', 'numeric', None, None, None):
            actual = row[2] if row[2] in ('numeric', 'real', 'double precision', 'integer', 'bigint', 'text', 'USER-DEFINED') else 'UNEXPECTED'
            if row[2] == 'numeric': actual = 'numeric (constrained or domain)'
            failures.append({'field': table + '.' + column, 'actual_type': actual})
    passed = len(rows) == len(found) == 9 and not failures
    return dict(status='PASS' if passed else 'BLOCKED', financial_schema='PASS' if passed else 'BLOCKED',
                exact_numeric_fields=9 - len(failures), required_fields=9, mismatches=failures)


def fence_info(reader):
    if reader.read('fence_contract') != [('r', False, False)]: raise ops_db.ReadOnlyUnavailable()
    rows = reader.read('fence')
    if len(rows) != 1: raise ops_db.ReadOnlyUnavailable()
    state, revision, repair_id, phase, recorded = rows[0]
    if state not in STATES or not isinstance(revision, int) or isinstance(revision, bool) or revision < 1:
        raise ops_db.ReadOnlyUnavailable()
    safe_id = repair_id if repair_id == OPERATION else ('NONE' if repair_id is None else 'UNEXPECTED')
    safe_phase = 'UNEXPECTED'
    if isinstance(phase, str) and (re.fullmatch('independently_verified:[0-9a-f]{64}', phase)
            or phase in {'precheck', 'reconciled_before_commit', 'committed_apply_verified',
                         'aborted_before_repair_commit', 'outcome_requires_independent_verification'}):
        safe_phase = phase
    timestamp = recorded.isoformat() if isinstance(recorded, datetime) else 'NOT AVAILABLE'
    return dict(status='PASS' if state == 'NORMAL' else 'BLOCKED', writers=state, revision=revision,
                repair_id=safe_id, phase=safe_phase, recorded_at=timestamp)


def nomad_info(fence):
    verified = (fence['writers'] == 'NORMAL' and fence['repair_id'] == OPERATION
                and re.fullmatch('independently_verified:[0-9a-f]{64}', fence['phase']) is not None)
    return dict(status='PASS' if verified else 'WARNING', nomad='VERIFIED' if verified else 'NOT AVAILABLE',
                basis='Durable fence verification marker; no financial values revalidated.')


def status():
    release = deployment()
    database = dict(status='BLOCKED', database='FAILED', read_only='NOT VERIFIED')
    schema = dict(status='BLOCKED', financial_schema='NOT AVAILABLE')
    fence = dict(status='BLOCKED', writers='NOT AVAILABLE')
    nomad = dict(status='BLOCKED', nomad='NOT AVAILABLE')
    try:
        with ops_db.connection() as reader:
            database = db_info(reader)
            schema = schema_info(reader)
            fence = fence_info(reader)
            nomad = nomad_info(fence)
    except ops_db.ReadOnlyUnavailable:
        pass
    statuses = [r['status'] for r in (release, database, schema, fence, nomad)]
    overall = 'BLOCKED' if 'BLOCKED' in statuses else 'WARNING' if 'WARNING' in statuses else 'PASS'
    return dict(status=overall, app='REACHABLE', database=database, release_identity=release,
                financial_schema=schema, writers=fence, nomad=nomad)


def health():
    try:
        with ops_db.connection() as reader: database = db_info(reader)
    except ops_db.ReadOnlyUnavailable:
        database = dict(status='BLOCKED', database='FAILED', read_only='NOT VERIFIED')
    return dict(status=database['status'], app_process='REACHABLE', database=database, external_render_health='NOT AVAILABLE')


def db_connection_test():
    with ops_db.connection() as reader: return db_info(reader)


def financial_schema():
    with ops_db.connection() as reader: return schema_info(reader)


def writer_status():
    with ops_db.connection() as reader: return fence_info(reader)


def nomad_status():
    with ops_db.connection() as reader: return nomad_info(fence_info(reader))


def recent_errors():
    return dict(status='WARNING', message='External runtime logs are not available from this read-only console.')


def help_command():
    return dict(status='PASS', commands=list(COMMANDS), message='Fixed read-only diagnostics only. No SQL, shell, repairs or recovery actions.')


HANDLERS = MappingProxyType(dict(zip(COMMANDS, (status, health, deployment, deployment,
    db_connection_test, financial_schema, writer_status, writer_status, nomad_status, recent_errors, help_command))))


def run(command):
    require_owner()
    start = time.perf_counter()
    known = isinstance(command, str) and command in HANDLERS
    try:
        result = HANDLERS[command]() if known else dict(status='BLOCKED', message='Unknown Ops command. Type help.')
    except Exception:
        result = dict(status='BLOCKED', message='Diagnostic unavailable. Verified read-only access and configuration are required.')
        if command == 'db-connection-test':
            result.update(database='FAILED', postgresql_version='NOT AVAILABLE', read_only='NOT VERIFIED')
    result['elapsed_ms'] = round((time.perf_counter() - start) * 1000, 2)
    result['command'] = command if known else 'unknown'
    result['captured_at'] = datetime.now(timezone.utc).isoformat()
    import streamlit as st
    owner_id = hashlib.sha256(st.session_state['login_user'].encode()).hexdigest()[:16]
    AUDIT.info(json.dumps(dict(timestamp=datetime.now(timezone.utc).isoformat(),
        owner_id=owner_id, command=command if known else 'unknown', status=result['status'], duration_ms=result['elapsed_ms'])))
    return result
