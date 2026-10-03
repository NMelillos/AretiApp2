"""Recovery-only approval and UI tests; no production connection or repair."""
from contextlib import ExitStack
from copy import deepcopy
import hashlib
from types import SimpleNamespace
from unittest.mock import Mock, patch


def main():
    import streamlit as st
    import nomad_controlled_repair as controlled
    import nomad_runtime as runtime
    import financial_atomic as atomic

    content = b'synthetic-original-nomad-pdf'
    fingerprint = hashlib.sha256(content).hexdigest()
    new_release = {'approved_sha': 'b' * 40, 'deployed_sha': 'b' * 40, 'status': 'PASS'}
    initial = dict(repair_id=controlled.OPERATION, state='REPAIR_COMMITTED_UNVERIFIED',
                   binding=dict(release_sha=controlled.RECOVERY_SOURCE_RELEASE,
                                evidence_digest=controlled.precheck.bindings.EXTENDED_CATALOG_DIGEST))
    verified = dict(verified=True, after_hash='c' * 64)

    def backend(current, *, pdf=content, release_error=None, verification_error=None):
        current = deepcopy(current)
        original = deepcopy(current)
        controller = Mock()
        def transition(conn, operation, old, new, phase):
            assert conn is controller and operation == controlled.OPERATION
            assert current['state'] == old
            current.update(state=new, phase=phase)
        with ExitStack() as stack:
            stack.enter_context(patch.object(controlled.precheck, 'require_auth'))
            identity = stack.enter_context(patch.object(controlled.precheck, 'release_identity',
                return_value=new_release, side_effect=release_error))
            connect = stack.enter_context(patch.object(controlled, 'connection', return_value=controller))
            stack.enter_context(patch.object(runtime, 'PDF_SHA256', fingerprint))
            stack.enter_context(patch.object(atomic, 'session'))
            stack.enter_context(patch.object(controlled.fence, 'acquire'))
            stack.enter_context(patch.object(controlled.fence, 'release'))
            stack.enter_context(patch.object(controlled.fence, 'read', return_value=current))
            moved = stack.enter_context(patch.object(controlled.fence, 'transition', side_effect=transition))
            verify = stack.enter_context(patch.object(atomic, 'verify', return_value=verified,
                                                      side_effect=verification_error))
            repair = stack.enter_context(patch.object(controlled, 'execute'))
            try:
                result = controlled.verify_and_release(pdf, 'VERIFY COMMITTED NOMAD REPAIR')
            except (atomic.Blocked, ValueError):
                assert current == original
                controller.commit.assert_not_called()
                moved.assert_not_called()
                repair.assert_not_called()
                return False, verify.call_count
            assert result == verified
            identity.assert_called_once_with()
            connect.assert_called_once_with()
            verify.assert_called_once_with(controlled.connection, operation_id=controlled.OPERATION,
                                           content=content, review=verify.call_args.kwargs['review'])
            assert current['state'] == 'NORMAL'
            assert current['phase'] == 'independently_verified:' + verified['after_hash']
            controller.commit.assert_called_once_with()
            repair.assert_not_called()
            return True, verify.call_count

    # A/F: approved new release can verify only the exact committed source.
    assert backend(initial) == (True, 1)
    recovery = deepcopy(initial); recovery['state'] = 'RECOVERY_REQUIRED'
    assert backend(recovery) == (True, 1)
    # B/C/D: mismatched source, in-progress release, evidence and operation block.
    for bad in (dict(initial, binding=dict(initial['binding'], release_sha='d' * 40)),
                dict(initial, state='REPAIR_IN_PROGRESS'),
                dict(initial, binding=dict(initial['binding'], evidence_digest='wrong')),
                dict(initial, repair_id='unrelated'), dict(initial, state='NORMAL')):
        assert backend(bad) == (False, 0)
    # E/G: bad PDF, unapproved runtime and failed independent verification never release.
    assert backend(initial, pdf=b'wrong-pdf') == (False, 0)
    assert backend(initial, release_error=ValueError('RELEASE_IDENTITY_UNVERIFIED')) == (False, 0)
    assert backend(initial, verification_error=atomic.Blocked('POST_STATE_CHANGED')) == (False, 1)

    class UI:
        def __init__(self, confirmed=False, clicked=False):
            self.confirmed, self.clicked = confirmed, clicked
            self.messages, self.controls = [], []
        def info(self, message): self.messages.append(message)
        def error(self, message): self.messages.append(message)
        def success(self, message): self.messages.append(message)
        def checkbox(self, label, **kwargs):
            self.controls.append(label)
            return self.confirmed
        def button(self, label, disabled=False, **kwargs):
            self.controls.append(label)
            return self.clicked and not disabled

    # H: no page-load/unconfirmed recovery; recovery UI never invokes Repair.
    for state in ('REPAIR_COMMITTED_UNVERIFIED', 'RECOVERY_REQUIRED'):
        for uploaded, confirmed, clicked, fail, calls in (
            (None, True, True, False, 0), (b'wrong', True, True, False, 0),
            (content, False, True, False, 0), (content, True, False, False, 0),
            (content, True, True, False, 1), (content, True, True, True, 1)):
            session = {controlled.READY_KEY: 'stale-repair-readiness'}
            if uploaded is not None:
                session['nomad_runtime_pdf'] = SimpleNamespace(getvalue=lambda: uploaded)
            ui = UI(confirmed, clicked)
            with patch.object(st, 'session_state', session), \
                 patch.object(controlled.precheck, 'authorized', return_value=True), \
                 patch.object(runtime, 'PDF_SHA256', fingerprint), \
                 patch('nomad_recovery_status.read_status', return_value=dict(state=state, transaction_status='UNCERTAIN')), \
                 patch.object(controlled, 'verify_and_release', return_value=verified,
                              side_effect=RuntimeError('SECRET DO NOT DISPLAY') if fail else None) as verify, \
                 patch.object(controlled, 'execute') as repair:
                controlled.render(ui, {'overall': 'PASS'})
                assert controlled.READY_KEY not in session
                assert verify.call_count == calls
                if calls:
                    verify.assert_called_once_with(content, 'VERIFY COMMITTED NOMAD REPAIR')
                repair.assert_not_called()
                assert 'Execute final NOMAD Repair' not in ui.controls
                assert 'SECRET' not in str(ui.messages)
                if calls and not fail:
                    assert ui.messages[-1] == 'NOMAD repair independently verified. Normal writers are enabled.'
    with patch.object(controlled.precheck, 'authorized', return_value=False), \
         patch.object(st, 'session_state', {}), \
         patch('nomad_recovery_status.read_status') as status:
        controlled.render(UI(True, True))
        status.assert_not_called()
    print('PASS A-H source/new release, operation/digest/PDF gates, verified NORMAL phase, failure fence preservation, authenticated recovery-only UI')


if __name__ == '__main__':
    main()
