"""Fresh-process import barriers and A-M authenticated recovery route checks."""
import ast
import builtins
import hashlib
from pathlib import Path
import subprocess
import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import Mock, patch

HEAVY = {'pandas', 'db', 'financial_atomic', 'financial_atomic_command',
         'nomad_controlled_repair', 'parsing', 'reporting'}


def cold_route(mode):
    import streamlit as st
    import auth
    class Stopped(BaseException): pass
    class HeavyBoundary(BaseException): pass
    original_import = builtins.__import__
    attempted = []
    def guarded_import(name, *args, **kwargs):
        if name.split('.')[0] in HEAVY:
            attempted.append(name)
            raise HeavyBoundary()
        return original_import(name, *args, **kwargs)
    state = dict(authenticated=True, login_user='Areti', third_report_authenticated=mode == 'third')
    query = {'nomad_recovery': '1'} if mode != 'normal' else {}
    if mode == 'third': query['page'] = 'TB & NF Family Office Report'
    assert not HEAVY.intersection(sys.modules), 'Streamlit/auth imported a heavy dependency'
    with patch.object(st, 'session_state', state), patch.object(st, 'query_params', query), \
         patch.object(st, 'set_page_config'), patch.object(st, 'title') as title, \
         patch.object(st, 'info'), patch.object(st, 'error'), \
         patch.object(st, 'file_uploader', return_value=None), \
         patch.object(st, 'stop', side_effect=Stopped), \
         patch.object(auth, 'require_login', wraps=auth.require_login) as main_login, \
         patch.object(auth, 'require_third_report_login', wraps=auth.require_third_report_login) as third_login, \
         patch.object(builtins, '__import__', side_effect=guarded_import):
        try:
            exec(compile(Path('app.py').read_text(encoding='utf-8'), 'app.py', 'exec'), {})
        except Stopped:
            assert mode == 'recovery' and attempted == []
            title.assert_called_once_with('NOMAD Committed Repair Verification')
        except HeavyBoundary:
            assert mode in ('normal', 'third') and attempted == ['pandas']
            title.assert_not_called()
        else:
            raise AssertionError('Application did not hit the expected route boundary')
        assert main_login.call_count == (mode != 'third')
        assert third_login.call_count == (mode == 'third')
    assert not HEAVY.intersection(sys.modules)
    print('PASS fresh process route/import barrier:', mode)


def main():
    source = Path('app.py').read_text(encoding='utf-8')
    from _qa_ops_console import without_ops_hook
    source = without_ops_hook(source.encode()).decode()
    hook = ('    if st.query_params.get("nomad_recovery") == "1":\n'
            '        from nomad_recovery_page import render_nomad_recovery_page\n'
            '        render_nomad_recovery_page()\n'
            '        st.stop()\n')
    baseline = subprocess.check_output(['git', 'show', '8cce9bbc6deb2292b6ea6912c1fc44acce6ab5d3:app.py']).decode().replace('\r\n', '\n')
    assert source.count(hook) == 1
    assert source.replace(hook, '', 1) == baseline, 'Normal/THIRD application changed beyond the single hook'
    assert 'else:\n    require_login()\n' + hook in source
    assert source.index(hook) < source.index('import pandas as pd') < source.index('from db import (')
    page_tree = ast.parse(Path('nomad_recovery_page.py').read_text(encoding='utf-8'))
    for node in page_tree.body:
        if isinstance(node, ast.Import):
            assert all(n.name in ('hashlib', 'streamlit') for n in node.names)
        assert not isinstance(node, ast.ImportFrom), 'Unexpected top-level dependency'
    runtime_tree = ast.parse(Path('nomad_runtime.py').read_text(encoding='utf-8'))
    runtime_pdf = next(ast.literal_eval(n.value) for n in runtime_tree.body
                       if isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id == 'PDF_SHA256' for t in n.targets))
    import nomad_recovery_page as page
    assert page.PDF_SHA256 == runtime_pdf
    for mode in ('recovery', 'normal', 'third'):
        subprocess.run([sys.executable, '-B', __file__, '--cold', mode], check=True, timeout=90)

    content = b'synthetic-owner-original-pdf'
    fingerprint = hashlib.sha256(content).hexdigest()
    backend = ModuleType('nomad_controlled_repair')
    backend.verify_and_release = Mock()
    backend.execute = Mock(side_effect=AssertionError('Repair must never run'))
    import deployment_identity
    release = dict(status='PASS', deployed_sha='a' * 40, approved_sha='a' * 40)
    failure = 'Verification did not complete. Writers remain protected. Do not rerun Repair.'
    success = 'NOMAD repair independently verified. Normal writers are enabled.'
    def run(state, uploaded=None, confirmed=False, clicked=False, error=None):
        ui = Mock()
        ui.session_state = state
        ui.file_uploader.return_value = None if uploaded is None else SimpleNamespace(getvalue=lambda: uploaded)
        ui.checkbox.return_value = confirmed
        # Deliberately ignore disabled to exercise the server-side confirmation guard.
        ui.button.return_value = clicked
        backend.verify_and_release.reset_mock()
        backend.verify_and_release.side_effect = error
        with patch.object(page, 'st', ui), patch.object(page, 'PDF_SHA256', fingerprint), \
             patch.object(deployment_identity, 'release_status', return_value=release), \
             patch.dict(sys.modules, {'nomad_controlled_repair': backend}):
            page.render_nomad_recovery_page()
        backend.execute.assert_not_called()
        return ui

    owner = dict(authenticated=True, login_user='Areti', third_report_authenticated=False)
    for denied in ({}, dict(owner, authenticated=False), dict(owner, login_user='Other'),
                   dict(owner, third_report_authenticated=True)):
        ui = run(denied, content, True, True)
        ui.stop.assert_called_once_with()
        ui.title.assert_not_called()
        backend.verify_and_release.assert_not_called()
    ui = run(owner)
    ui.title.assert_called_once_with('NOMAD Committed Repair Verification')
    backend.verify_and_release.assert_not_called()
    ui = run(owner, b'wrong', True, True)
    ui.error.assert_called_once_with(failure)
    backend.verify_and_release.assert_not_called()
    ui = run(owner, content, False, True)
    backend.verify_and_release.assert_not_called()
    for _ in range(2):
        run(owner, content, True, False)
        backend.verify_and_release.assert_not_called()
    ui = run(owner, content, True, True)
    backend.verify_and_release.assert_called_once_with(content, 'VERIFY COMMITTED NOMAD REPAIR')
    ui.success.assert_called_once_with(success)
    ui = run(owner, content, True, True, RuntimeError('SECRET DSN SQL ROW DATA'))
    ui.error.assert_called_once_with(failure)
    assert 'SECRET' not in str(ui.mock_calls)
    print('PASS A-M unchanged routes, authorization, fresh-process lazy imports, PDF/confirmation, explicit verification only, sanitized failure and success')


if __name__ == '__main__':
    if sys.argv[1:2] == ['--cold']:
        cold_route(sys.argv[2])
    else:
        main()
