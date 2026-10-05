"""Exact reviewed reduced UI compatibility for historical source assertions only.

Runtime tests execute current code. No parser, database or financial source is
restored by this helper. Unknown app bytes or changed companion modules fail
closed before an older pinned-source assertion can receive baseline UI bytes.
"""
import hashlib
from pathlib import Path
import subprocess

BASE = 'f3ea9ce2d0143ef3239871bb94791ffb39fd857e'
APP_SHA256 = '42ee08926a16fd77d3808a01dfaffe552c1bbde21f0258cf1a91e5b6249679ae'
COMPANIONS = {
    'latest_import_balances.py': '5d22f8eefee6c3334ced91b7fc26d8c9a19e87e0800edce6ef63ec33f59eee5a',
    'review_state.py': '207e28bcab1217ef210c74a60f06c1ca00a94ae21339c2512bc9364f60d3cff5',
}


def historical_app_source(source):
    source = source.replace(b'\r\n', b'\n')
    original = subprocess.check_output(['git', 'show', BASE + ':app.py']).replace(b'\r\n', b'\n')
    if source == original:
        return original
    assert hashlib.sha256(source).hexdigest() == APP_SHA256, 'Unreviewed reduced app source change'
    for name, expected in COMPANIONS.items():
        content = Path(__file__).with_name(name).read_bytes().replace(b'\r\n', b'\n')
        assert hashlib.sha256(content).hexdigest() == expected, 'Unreviewed reduced companion source change: ' + name
    return original
