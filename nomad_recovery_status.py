"""Areti-only evidence reader. No locks, DDL, repair, or fence transitions."""
from contextlib import closing

import financial_atomic as atomic
import financial_writer_control as fence
import nomad_precheck as precheck
from financial_atomic_command import connection
from nomad_runtime import PDF_SHA256

OPERATION = 'nomad-frozen-final-v1'


def classify(current, snapshot, applied, reversed_state, controller_active):
    result = dict(state=current['state'], transaction_status='UNCERTAIN')
    if controller_active or reversed_state is not None:
        return result
    if current.get('repair_id') not in (None, OPERATION):
        return result
    if current['state'] == 'NORMAL' and snapshot is None and applied is None:
        if current.get('repair_id') is None:
            result['transaction_status'] = 'NOT STARTED'
        elif current.get('phase') == 'aborted_before_repair_commit':
            result['transaction_status'] = 'ROLLED BACK'
        return result
    if snapshot is None or applied is None:
        return result
    manifest, plan = snapshot.get('manifest', {}), snapshot.get('plan', {})
    if (manifest.get('pdf_sha256') != PDF_SHA256
            or plan.get('pdf_sha256') != PDF_SHA256
            or plan.get('manifest_hash') != atomic.state_hash(manifest)):
        return result
    if current['state'] in ('REPAIR_COMMITTED_UNVERIFIED', 'RECOVERY_REQUIRED'):
        result['transaction_status'] = 'COMMITTED_UNVERIFIED'
    elif (current['state'] == 'NORMAL'
          and current.get('phase') == 'independently_verified:' + atomic.state_hash(applied)):
        result['transaction_status'] = 'VERIFIED'
    # IN_PROGRESS plus APPLY is inconsistent with the atomic fence contract.
    # Never infer rollback or permission to reopen writers from absent evidence.
    return result


def read_status():
    precheck.require_auth()
    with closing(connection()) as conn:
        atomic.session(conn, True)
        try:
            with conn.cursor() as cur:
                cur.execute('SHOW transaction_read_only')
                atomic.require(cur.fetchone() == ('on',), 'READ_ONLY_REQUIRED')
                current = fence.read(conn)
                cur.execute("SELECT EXISTS(SELECT 1 FROM pg_locks WHERE locktype='advisory' AND classid=0 AND objid=%s AND objsubid=1 AND mode='ExclusiveLock' AND granted)", (fence.KEY,))
                active = cur.fetchone()[0]
                cur.execute("SELECT to_regnamespace('financial_recovery')")
                snapshot = applied = reversed_state = None
                if cur.fetchone()[0] is not None:
                    atomic.audit_guard(cur)
                    snapshot = atomic.stored(cur, OPERATION)
                    applied = atomic.stored(cur, OPERATION, 'APPLY')
                    reversed_state = atomic.stored(cur, OPERATION, 'REVERSE')
                return classify(current, snapshot, applied, reversed_state, active)
        finally:
            conn.rollback()
