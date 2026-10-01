"""Recovery status is evidence-only: no inference from a UI traceback or timer."""
from unittest.mock import Mock, patch


def main():
    import nomad_recovery_status as status
    import financial_atomic as atomic
    operation = status.OPERATION
    base = dict(state='REPAIR_IN_PROGRESS', repair_id=operation, phase='precheck')
    assert status.classify(base, None, None, None, False)['transaction_status'] == 'UNCERTAIN'
    assert status.classify(base, None, None, None, True)['transaction_status'] == 'UNCERTAIN'
    snapshot = {'manifest': {'pdf_sha256': status.PDF_SHA256}, 'plan': {}}
    snapshot['plan'] = dict(pdf_sha256=status.PDF_SHA256, manifest_hash=atomic.state_hash(snapshot['manifest']))
    applied = {'synthetic': 'audited state'}
    for state in ('REPAIR_COMMITTED_UNVERIFIED', 'RECOVERY_REQUIRED'):
        row = dict(base, state=state)
        assert status.classify(row, snapshot, applied, None, False)['transaction_status'] == 'COMMITTED_UNVERIFIED'
        assert status.classify(row, snapshot, None, None, False)['transaction_status'] == 'UNCERTAIN'
    assert status.classify(base, snapshot, applied, None, False)['transaction_status'] == 'UNCERTAIN'
    row = dict(base, state='NORMAL', phase='independently_verified:' + atomic.state_hash(applied))
    assert status.classify(row, snapshot, applied, None, False)['transaction_status'] == 'VERIFIED'
    assert status.classify(row, snapshot, applied, {'reverse': True}, False)['transaction_status'] == 'UNCERTAIN'
    row = dict(base, state='NORMAL', phase='aborted_before_repair_commit')
    assert status.classify(row, None, None, None, False)['transaction_status'] == 'ROLLED BACK'
    assert status.classify({'state':'NORMAL','repair_id':None}, None, None, None, False)['transaction_status'] == 'NOT STARTED'
    assert status.classify(dict(base,repair_id='other'),snapshot,applied,None,False)['transaction_status']=='UNCERTAIN'
    connection = Mock()
    with patch.object(status.precheck, 'require_auth', side_effect=ValueError('unauthorized')), patch.object(status,'connection',connection):
        try: status.read_status()
        except ValueError: pass
        else: raise AssertionError('Unauthorized status accepted')
        connection.assert_not_called()
    # Unknown status and active fences never render a Repair control or clear it.
    import nomad_controlled_repair as controlled
    ui = Mock()
    with patch.object(controlled.precheck,'authorized',return_value=True), patch.object(status,'read_status',return_value=dict(base,transaction_status='UNCERTAIN')), patch.object(controlled,'execute') as execute:
        controlled.render(ui)
        execute.assert_not_called()
        ui.button.assert_not_called()
    print('PASS audit-based phase classification; uncertain outcomes stay blocked; unauthorized status denied; no Repair/no fence transition')


if __name__ == '__main__': main()
