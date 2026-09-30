"""Deliberate Areti-only execution. Never called by page load or startup."""
from contextlib import closing
import hashlib

import financial_atomic as atomic
import financial_writer_control as fence
from financial_atomic_command import connection
import nomad_precheck as precheck
from nomad_execution_review import ReviewedNomad

OPERATION = 'nomad-frozen-final-v1'
CONFIRMATION = 'REPAIR APPROVED NOMAD IMPORT'
READY_KEY = '_nomad_controlled_readiness'


def _execute(connect, review, content, release):
    """Server-owned dependencies only; no SQL, IDs or plan accepted from the UI."""
    with closing(connect()) as controller:
        atomic.session(controller, False)
        fence.acquire(controller)
        committed_or_uncertain = False
        started = False
        try:
            # Validate the approved trigger/RLS bodies before *any* control DDL.
            with controller.cursor() as cur:
                atomic.catalog(cur, review)
            controller.rollback()
            fence.install(controller)
            fence.begin(controller, OPERATION, release['approved_sha'],
                        precheck.bindings.EXTENDED_CATALOG_DIGEST, 'Areti')
            controller.commit()
            started = True
            fence.assert_owned(controller)
            atomic.require(fence.read(controller)['state'] == 'REPAIR_IN_PROGRESS', 'FENCE_STATE_CHANGED')
            controller.rollback()
            atomic.session(controller, True)
            with controller.cursor() as cur:
                manifest = review.derive(cur, content)
            controller.rollback()
            plan = atomic.prepare(controller, content, manifest, review)

            def before_commit(conn):
                fence.assert_owned(conn)
                fence.transition(conn, OPERATION, 'REPAIR_IN_PROGRESS',
                                 'REPAIR_COMMITTED_UNVERIFIED', 'reconciled_before_commit')

            # If the commit acknowledgement is lost, never guess whether it ran.
            committed_or_uncertain = True
            try:
                result = atomic.apply(connect, content=content, manifest=manifest,
                    plan=plan, operation_id=OPERATION, actor='Areti', controller=controller,
                    review=review, before_commit=before_commit)
            except atomic.Blocked as error:
                if str(error) != 'COMMIT_UNCERTAIN_KEEP_WRITERS_CLOSED_VERIFY':
                    committed_or_uncertain = False
                raise
            # A genuinely separate lease performs the read-back. The dedicated
            # controller retains its exclusive SESSION lock across both commits.
            fence.assert_owned(controller)
            controller.rollback()
            verification = atomic.verify(connect, operation_id=OPERATION, content=content, review=review)
            atomic.require(verification['verified'], 'INDEPENDENT_VERIFY_FAILED')
            fence.assert_owned(controller)
            fence.transition(controller, OPERATION, 'REPAIR_COMMITTED_UNVERIFIED',
                             'NORMAL', 'independently_verified:' + verification['after_hash'])
            controller.commit()
            fence.release(controller)
            return result
        except BaseException:
            try:
                controller.rollback()
                atomic.session(controller, False)
                if started and not committed_or_uncertain:
                    # Safe only after confirmed rollback on the same live session.
                    fence.assert_owned(controller)
                    fence.transition(controller, OPERATION, 'REPAIR_IN_PROGRESS',
                                     'NORMAL', 'aborted_before_repair_commit')
                    controller.commit()
                    fence.release(controller)
                elif started:
                    fence.assert_owned(controller)
                    current = fence.read(controller)
                    if current['state'] in ('REPAIR_IN_PROGRESS', 'REPAIR_COMMITTED_UNVERIFIED'):
                        fence.transition(controller, OPERATION, current['state'],
                                         'RECOVERY_REQUIRED', 'outcome_requires_independent_verification')
                        controller.commit()
            except BaseException:
                # Lost connection/uncertain rollback leaves the durable fence.
                pass
            raise
        finally:
            # Never return a session advisory lock to the shared application pool.
            # Closing the dedicated physical session cannot clear the durable fence.
            raw = getattr(controller, 'raw', controller)
            raw.close()


def execute(content, confirmation):
    precheck.require_auth()
    atomic.require(confirmation == CONFIRMATION, 'DELIBERATE_CONFIRMATION_REQUIRED')
    from nomad_runtime import PDF_SHA256
    atomic.require(isinstance(content, bytes) and hashlib.sha256(content).hexdigest() == PDF_SHA256,
                   'PDF_FINGERPRINT_CHANGED')
    release = precheck.release_identity()
    return _execute(connection, ReviewedNomad(), content, release)


def verify_and_release(content, confirmation):
    """Explicit recovery entry point, not exposed by UI, startup or HTTP routes.

    Does not rerun Repair or infer success from a timeout. A committed audited
    repair must pass the same independent verifier before writers can resume.
    """
    precheck.require_auth()
    atomic.require(confirmation == 'VERIFY COMMITTED NOMAD REPAIR', 'DELIBERATE_CONFIRMATION_REQUIRED')
    release = precheck.release_identity()
    with closing(connection()) as controller:
        atomic.session(controller, False)
        fence.acquire(controller)
        try:
            current = fence.read(controller)
            atomic.require(current['repair_id'] == OPERATION and current['state'] in
                           ('REPAIR_IN_PROGRESS', 'REPAIR_COMMITTED_UNVERIFIED', 'RECOVERY_REQUIRED'), 'RECOVERY_STATE_CHANGED')
            atomic.require(current['binding']['release_sha'] == release['approved_sha']
                           and current['binding']['evidence_digest'] == precheck.bindings.EXTENDED_CATALOG_DIGEST,
                           'RECOVERY_APPROVAL_CHANGED')
            controller.rollback()
            result = atomic.verify(connection, operation_id=OPERATION, content=content, review=ReviewedNomad())
            atomic.require(result['verified'], 'INDEPENDENT_VERIFY_FAILED')
            # An IN_PROGRESS fence can arise from lost acknowledgement; only an
            # intact committed APPLY journal and matching read-back resolve it.
            if current['state'] == 'REPAIR_IN_PROGRESS':
                fence.transition(controller, OPERATION, current['state'], 'RECOVERY_REQUIRED', 'committed_apply_verified')
                current['state'] = 'RECOVERY_REQUIRED'
            fence.transition(controller, OPERATION, current['state'], 'NORMAL', 'independently_verified:' + result['after_hash'])
            controller.commit()
            fence.release(controller)
            return result
        finally:
            getattr(controller, 'raw', controller).close()


def render(ui, evidence=None):
    import streamlit as st
    from nomad_runtime import session_id, PDF_SHA256
    if not precheck.authorized():
        st.session_state.pop(READY_KEY, None)
        return
    uploaded = st.session_state.get('nomad_runtime_pdf')
    if uploaded is None:
        st.session_state.pop(READY_KEY, None)
        ui.info('Repair is unavailable until the existing read-only precheck passes.')
        return
    content = uploaded.getvalue()
    try:
        binding = (session_id(), precheck.release_identity()['approved_sha'], hashlib.sha256(content).hexdigest())
    except ValueError:
        st.session_state.pop(READY_KEY, None)
        ui.info('Repair is unavailable until the deployed and approved release identities match.')
        return
    if evidence is not None:
        st.session_state.pop(READY_KEY, None)
        if evidence.get('overall') == 'PASS' and binding[2] == PDF_SHA256:
            st.session_state[READY_KEY] = binding
    if st.session_state.get(READY_KEY) != binding:
        ui.info('Repair is unavailable until the existing read-only precheck passes.')
        return
    ui.info('Backup gate passed. Repair is atomic. Frozen preconditions are rechecked immediately before repair writes; a mismatch aborts without financial changes. Writers remain blocked until independent verification succeeds.')
    confirmed = ui.checkbox('I confirm the approved NOMAD repair and nine-column NUMERIC migration', key='nomad_final_confirm')
    if ui.button('Execute final NOMAD Repair', disabled=not confirmed, key='nomad_final_execute'):
        st.session_state.pop(READY_KEY, None)
        try:
            precheck.require_auth()
            atomic.require(confirmed is True, 'DELIBERATE_CONFIRMATION_REQUIRED')
            execute(content, CONFIRMATION)
            ui.success('NOMAD repair committed and independently verified. Normal writers are enabled.')
        except BaseException:
            ui.error('Repair did not complete verified release. Do not retry or clear maintenance state; controlled recovery may be required.')
