"""Synthetic release verification; no network or production database access."""
import json
import os
from pathlib import Path
import subprocess
import tempfile
from unittest.mock import patch

import deployment_identity as identity


def blocked(call):
    try:
        call()
    except ValueError:
        return
    raise AssertionError('Unverified identity accepted')


def main():
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        def git(*args):
            return subprocess.check_output(['git', '-C', directory, *args], stderr=subprocess.DEVNULL)
        git('init')
        git('config', 'user.name', 'Synthetic QA')
        git('config', 'user.email', 'qa@example.invalid')
        git('config', 'core.autocrlf', 'false')
        (root / 'app.py').write_bytes(b'print("synthetic")\n')
        (root / 'deployment_identity.py').write_bytes(b'# synthetic\n')
        (root / 'nested').mkdir()
        (root / 'nested' / 'config.txt').write_bytes(b'approved config\n')
        git('add', '.')
        git('commit', '-m', 'Synthetic identity fixture')
        actual = git('rev-parse', 'HEAD').decode().strip()
        evidence = identity.checkout_evidence(root)
        assert identity.actual_identity(root) == actual
        (root / identity.ARTIFACT).write_text(json.dumps(evidence))
        # No Git or Render runtime variable is needed for build evidence.
        with patch.object(identity, 'checkout_evidence', side_effect=AssertionError('Git unavailable')):
            assert identity.actual_identity(root) == actual
        with patch.object(identity, 'actual_identity', return_value=actual):
            for render in ('', actual):
                with patch.dict(os.environ, {'NOMAD_APPROVED_RELEASE_SHA': actual,
                                             'RENDER_GIT_COMMIT': render}, clear=True):
                    assert identity.release_identity()['status'] == 'PASS'
            for approved in ('', 'b'*40, actual+'\n', ' '+actual, 'invalid'):
                with patch.dict(os.environ, {'NOMAD_APPROVED_RELEASE_SHA': approved}, clear=True):
                    blocked(identity.release_identity)
            with patch.dict(os.environ, {}, clear=True):
                blocked(identity.release_identity)
                assert identity.release_status()['reason'] == 'APPROVED_IDENTITY_UNAVAILABLE'
            with patch.dict(os.environ, {'NOMAD_APPROVED_RELEASE_SHA': 'invalid'}, clear=True):
                assert identity.release_status()['reason'] == 'APPROVED_IDENTITY_INVALID'
            for render in ('c'*40, 'invalid'):
                with patch.dict(os.environ, {'NOMAD_APPROVED_RELEASE_SHA': actual,
                                             'RENDER_GIT_COMMIT': render}, clear=True):
                    blocked(identity.release_identity)
        with patch.object(identity, 'actual_identity', side_effect=ValueError('ACTUAL_IDENTITY_UNAVAILABLE')):
            with patch.dict(os.environ, {'NOMAD_APPROVED_RELEASE_SHA': actual,
                                         'RENDER_GIT_COMMIT': actual}, clear=True):
                blocked(identity.release_identity)
                assert identity.release_status()['reason'] == 'ACTUAL_IDENTITY_UNAVAILABLE'
        (root / 'app.py').write_bytes(b'print("changed")\n')
        blocked(lambda: identity.actual_identity(root))
        (root / 'app.py').write_bytes(b'print("synthetic")\r\n')
        assert identity.actual_identity(root) == actual
        (root / 'shadow.py').write_text('# untracked module')
        blocked(lambda: identity.actual_identity(root))
        (root / 'shadow.py').unlink()
        # An invalid artifact never falls back to otherwise valid Git evidence.
        (root / identity.ARTIFACT).write_text('{}')
        blocked(lambda: identity.actual_identity(root))
        bad = dict(evidence, trees={key: 'YWJj' for key in evidence['trees']})
        (root / identity.ARTIFACT).write_text(json.dumps(bad))
        blocked(lambda: identity.actual_identity(root))
        (root / identity.ARTIFACT).unlink()
        (root / 'nested' / 'config.txt').unlink()
        blocked(lambda: identity.actual_identity(root))
    with tempfile.TemporaryDirectory() as directory:
        blocked(lambda: identity.actual_identity(directory))
    print('PASS exact match, mismatch, missing actual/approval, runtime conflict, Git/build evidence, file/tree tampering, unavailable identity')


if __name__ == '__main__':
    main()
