"""Adversarial checks: reviewed compatibility is never a runtime substitution."""
from pathlib import Path
import subprocess
from unittest.mock import patch
import reduced_release_qa as guard
from safra_balances_qa import without_safra_balances


def rejected(action):
    try:
        action()
    except AssertionError:
        return
    raise AssertionError('Unexpected code was accepted by a reduced source guard')


def main():
    app=Path('app.py').read_bytes()
    prior=subprocess.check_output(['git','show',guard.BASE+':app.py']).replace(b'\r\n',b'\n')
    old=subprocess.check_output(['git','show','dfa218f944bb7204df179b9a7e4ce0f31d5d8c74:app.py']).replace(b'\r\n',b'\n')
    assert guard.historical_app_source(app)==prior
    assert guard.historical_app_source(prior)==prior
    assert without_safra_balances('app.py',app)==old
    for changed in (app+b'\n# unexpected edit\n',app.replace(b'def _save_setup_upload',b'def _changed_setup_upload'),
                    app.replace(b'if not corrections_authorized():',b'if False:')):
        rejected(lambda:guard.historical_app_source(changed))
        rejected(lambda:without_safra_balances('app.py',changed))
    read=Path.read_bytes
    for name in guard.COMPANIONS:
        def changed(path):
            content=read(path)
            return content+b'\n# unexpected companion edit\n' if path.name==name else content
        with patch.object(Path,'read_bytes',changed):
            rejected(lambda:guard.historical_app_source(app))
            rejected(lambda:without_safra_balances('app.py',app))
    parsing=Path('parsing.py').read_bytes()
    rejected(lambda:without_safra_balances('parsing.py',parsing+b'\n# unexpected parser edit\n'))
    assert Path('app.py').read_bytes()==app and Path('parsing.py').read_bytes()==parsing
    assert not any(Path(name).exists() for name in ('bank_units.py','statement_reconciliation.py','setup_uploads.py'))
    print('PASS exact approved app/companions accepted only for historical assertions; app, permission, companion and parser tampering rejected; current runtime source untouched; full financial candidate not imported')


if __name__=='__main__':main()
