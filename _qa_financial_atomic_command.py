"""Command is read-only by default; missing deliberate approval fails before DB."""
from contextlib import redirect_stdout, redirect_stderr
from io import StringIO
import hashlib
import json
import os
from pathlib import Path
import tempfile
from unittest.mock import patch


def main():
    import financial_atomic_command as cmd
    import financial_atomic as work
    root = Path(os.environ['TEMP']).resolve()
    assert root.drive.upper() == 'E:'
    with tempfile.TemporaryDirectory(dir=root) as folder:
        folder = Path(folder)
        pdf = folder/'synthetic.pdf'
        pdf.write_bytes(b'SYNTHETIC NONCONFIDENTIAL PDF PLACEHOLDER')
        manifest = folder/'synthetic.json'
        content = {'pdf_sha256': hashlib.sha256(pdf.read_bytes()).hexdigest()}
        manifest.write_text(json.dumps(content))
        digest = hashlib.sha256(manifest.read_bytes()).hexdigest()
        plan_path = folder/'plan.json'
        args = ['--manifest', str(manifest), '--pdf', str(pdf), '--plan', str(plan_path),
                '--operation-id', 'synthetic', '--actor', 'Synthetic tester']
        plan = dict(state_hash='1'*64, database_hash='2'*64, manifest_hash=work.state_hash(content), pdf_sha256=content['pdf_sha256'])
        class Connection:
            def close(self): pass
        with patch.object(cmd, 'APPROVED_MANIFEST', digest), patch.object(cmd, 'connection', return_value=Connection()) as connect, \
             patch.object(work, 'prepare', return_value=plan) as prepare:
            out, err = StringIO(), StringIO()
            with redirect_stdout(out), redirect_stderr(err):
                assert cmd.main(args) == 0
            assert prepare.call_count == connect.call_count == 1
            assert 'PASS READ_ONLY_PREFLIGHT' in out.getvalue() and not err.getvalue()
        with patch.object(cmd, 'APPROVED_MANIFEST', digest), \
             patch.object(cmd, 'connection', side_effect=AssertionError('Unexpected database connection')) as connect, \
             patch.object(work, 'apply', side_effect=AssertionError('Apply reached')) as apply:
            for flags in ([], ['--execute'], ['--execute', '--confirm-plan', 'wrong'],
                          ['--execute', '--confirm-plan', hashlib.sha256(plan_path.read_bytes()).hexdigest()]):
                with patch.dict(os.environ, {'NOMAD_ATOMIC_TOKEN': ''}), redirect_stdout(StringIO()), redirect_stderr(StringIO()):
                    assert cmd.main(['apply']+args+flags) == 2
            apply.assert_not_called()
            connect.assert_not_called()
            with redirect_stdout(StringIO()), redirect_stderr(StringIO()), patch.object(cmd, 'source_digest', return_value='changed'):
                assert cmd.main(['apply']+args) == 2
            with patch.object(work, 'verify', return_value={'after_hash':'3'*64}) as verify, redirect_stdout(StringIO()), redirect_stderr(StringIO()):
                assert cmd.main(['verify']+args+['--reversed-state']) == 0
                assert verify.call_args.kwargs['reversed_state'] is True
            plan_path.write_text(json.dumps(dict(json.loads(plan_path.read_text()), operation_id='changed')))
            with redirect_stdout(StringIO()), redirect_stderr(StringIO()):
                assert cmd.main(['reverse']+args) == 2
        with patch.object(cmd, 'connection', side_effect=AssertionError('Unapproved manifest connection')) as connect:
            with redirect_stdout(StringIO()), redirect_stderr(StringIO()):
                assert cmd.main(args) == 2
            connect.assert_not_called()
        # No exception/DSN content may cross the command's log boundary.
        with patch.object(cmd, 'APPROVED_MANIFEST', digest), patch.object(cmd, 'connection', side_effect=RuntimeError('SYNTHETIC_SECRET')):
            out, err = StringIO(), StringIO()
            with redirect_stdout(out), redirect_stderr(err): assert cmd.main(args) == 2
            assert 'SYNTHETIC_SECRET' not in out.getvalue()+err.getvalue()
    print('PASS command: read-only default, manifest/source/operator binding, write refusal, no DB on missing approval, safe errors')


if __name__ == '__main__': main()
