"""Permanent retirement guard for the removed temporary database diagnostic."""
import importlib.util
from pathlib import Path
import subprocess


def without_database_identity(name, source):
    # Historical comparison helper used by the import-history regression guard.
    if name != 'app.py':
        return source
    block = b'from database_identity import render_database_identity\nif render_database_identity():\n    st.stop()\n\n'
    source = source.replace(b'\r\n', b'\n')
    if block in source:
        assert source.count(block) == 1
        anchor = b'else:\n    require_login()\n\n' + block
        assert source.count(anchor) == 1
        source = source.replace(block, b'', 1)
    return source


def main():
    assert not Path('database_identity.py').exists(), 'Temporary diagnostic module still deployed'
    assert importlib.util.find_spec('database_identity') is None, 'Retired diagnostic remains importable'
    source = Path('app.py').read_bytes().replace(b'\r\n', b'\n')
    from financial_storage_qa import protected_source
    source = protected_source('app.py', source)
    baseline = subprocess.check_output(['git', 'show',
        '48a6efd9cdc4b58972e87887a7f7af7d613aeca6:app.py']).replace(b'\r\n', b'\n')
    assert source == baseline, 'Normal application must be restored exactly, without diagnostic exposure'
    for path in Path('.').glob('*.py'):
        if path.name.startswith('_qa_'):
            continue
        text = path.read_text(encoding='utf-8').lower()
        assert 'databaseidentity' not in text and 'render_database_identity' not in text, path.name
        assert 'database identity diagnostics' not in text and 'read database identity' not in text, path.name
    print('PASS diagnostic module/UI/route removed; normal application restored exactly; no diagnostic production entry')


if __name__ == '__main__': main()
