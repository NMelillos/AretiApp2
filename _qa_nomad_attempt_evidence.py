"""Synthetic payload-free failure evidence and retry eligibility regression."""
import json
from unittest.mock import patch
import financial_writer_control as fence
from nomad_attempt import Attempt, SESSION_KEY


def main():
    binding = {'attempt_id': 'a' * 32}
    start = ('REPAIR_IN_PROGRESS', binding, 'precheck')
    finish = ('NORMAL', binding, 'aborted_before_repair_commit')
    fence.retry_history([])
    fence.retry_history([start, finish])
    for state, phase in [('NORMAL','independently_verified:x'),
                         ('REPAIR_COMMITTED_UNVERIFIED','reconciled_before_commit'),
                         ('RECOVERY_REQUIRED','outcome_requires_independent_verification'),
                         ('UNCERTAIN','unknown')]:
        try: fence.retry_history([start, (state,binding,phase)])
        except fence.WritersBlocked: pass
        else: raise AssertionError('Unsafe retry accepted: '+state)
    try: fence.retry_history([start])
    except fence.WritersBlocked: pass
    else: raise AssertionError('Incomplete attempt accepted')
    class DatabaseFailure(Exception):
        pgcode = '23514'
    attempt = Attempt('synthetic-operation')
    attempt.phase('transaction_locks')
    attempt.phase('approved_corrections')
    evidence = attempt.failure(DatabaseFailure('postgres://private:secret@host amount=123456.78'))
    assert evidence['sqlstate'] == '23514'
    assert evidence['phase'] == 'approved_corrections'
    assert evidence['exception_class'] == 'DatabaseFailure'
    assert evidence['transaction_begun'] and not evidence['commit_attempted']
    session = {}
    with patch('streamlit.session_state',session), patch('nomad_attempt.logging.getLogger') as log:
        attempt.publish()
        output = log.return_value.error.call_args.args[0]
        assert session[SESSION_KEY] == evidence
        assert json.loads(output) == evidence
        for secret in ('postgres://','private','secret','123456.78'):
            assert secret not in output.replace('private exception text withheld','')
    attempt.phase('repair_commit')
    assert attempt.data['commit_attempted'] and not attempt.data['commit_outcome_known']
    attempt.phase('repair_committed')
    assert attempt.data['commit_outcome_known']
    assert Attempt('synthetic-operation').data['attempt_id'] != attempt.data['attempt_id']
    print('PASS retry classifications, unique IDs, SQLSTATE, exact phase, safe logger/session evidence')


if __name__ == '__main__': main()
