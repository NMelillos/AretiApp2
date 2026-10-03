"""Verify deployed files against Git's content-addressed commit/tree objects.

The build artifact contains public Git metadata only, never environment values.
Git checkout verification is also supported when Render retains .git at runtime.
The approval variable is never used to obtain the actual identity.
"""
import base64
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess

ROOT = Path(__file__).resolve().parent
ARTIFACT = '.deployment_identity.json'
SHA = re.compile(r'[0-9a-f]{40}')
TEXT_SUFFIXES = {'.py', '.md', '.txt', '.yaml', '.json', '.html', '.css', '.js', '.toml'}


def object_hash(kind, data):
    return hashlib.sha1(kind.encode() + b' ' + str(len(data)).encode() + b'\0' + data).hexdigest()


def git(root, *args):
    return subprocess.check_output(['git', '-C', str(root), *args],
                                   stderr=subprocess.DEVNULL, timeout=20)


def checkout_evidence(root):
    # Reject accidentally discovering a parent checkout.
    if Path(git(root, 'rev-parse', '--show-toplevel').decode().strip()).resolve() != root.resolve():
        raise ValueError('ACTUAL_IDENTITY_UNAVAILABLE')
    sha = git(root, 'rev-parse', '--verify', 'HEAD^{commit}').decode().strip()
    commit = git(root, 'cat-file', 'commit', sha)
    trees = {}

    def collect(oid):
        data = git(root, 'cat-file', 'tree', oid)
        trees[oid] = base64.b64encode(data).decode()
        for mode, name, child in tree_entries(data):
            if mode == '40000':
                collect(child)

    collect(commit.split(b'\n', 1)[0].removeprefix(b'tree ').decode())
    return dict(format_version=1, commit=base64.b64encode(commit).decode(), trees=trees)


def tree_entries(data):
    offset = 0
    while offset < len(data):
        end = data.index(b'\0', offset)
        mode, name = data[offset:end].split(b' ', 1)
        oid = data[end+1:end+21]
        if len(oid) != 20:
            raise ValueError('ACTUAL_IDENTITY_INVALID')
        yield mode.decode(), name.decode('utf-8'), oid.hex()
        offset = end + 21


def verify_evidence(root, evidence):
    if evidence['format_version'] != 1:
        raise ValueError('ACTUAL_IDENTITY_INVALID')
    commit = base64.b64decode(evidence['commit'], validate=True)
    sha = object_hash('commit', commit)
    tree_line = commit.split(b'\n', 1)[0]
    if not tree_line.startswith(b'tree '):
        raise ValueError('ACTUAL_IDENTITY_INVALID')
    tree = tree_line[5:].decode()
    files = set()

    def verify_tree(oid, parent):
        data = base64.b64decode(evidence['trees'][oid], validate=True)
        if object_hash('tree', data) != oid:
            raise ValueError('ACTUAL_IDENTITY_INVALID')
        for mode, name, child in tree_entries(data):
            if not name or name in ('.', '..') or '/' in name or '\\' in name:
                raise ValueError('ACTUAL_IDENTITY_INVALID')
            relative = parent / name
            path = root.joinpath(*relative.parts)
            if path.is_symlink():
                raise ValueError('ACTUAL_CODE_CHANGED')
            if mode == '40000':
                verify_tree(child, relative)
            elif mode in ('100644', '100755'):
                content = path.read_bytes()
                # Git's Windows text checkout can use CRLF. Only accept LF
                # equivalence if it hashes to the committed blob itself.
                actual = object_hash('blob', content)
                if actual != child and (path.suffix in TEXT_SUFFIXES or name.startswith('.')):
                    actual = object_hash('blob', content.replace(b'\r\n', b'\n'))
                if actual != child:
                    raise ValueError('ACTUAL_CODE_CHANGED')
                files.add(relative.as_posix())
            else:
                raise ValueError('ACTUAL_IDENTITY_INVALID')

    verify_tree(tree, PurePosixPath())
    # Untracked Python modules beside the application can shadow reviewed code.
    if any(p.name not in files for p in root.glob('*.py')):
        raise ValueError('ACTUAL_CODE_CHANGED')
    if 'app.py' not in files or 'deployment_identity.py' not in files:
        raise ValueError('ACTUAL_IDENTITY_INVALID')
    return sha


def actual_identity(root=None):
    root = ROOT if root is None else Path(root).resolve()
    artifact = root / ARTIFACT
    try:
        evidence = json.loads(artifact.read_text()) if artifact.exists() else checkout_evidence(root)
        return verify_evidence(root, evidence)
    except ValueError:
        raise
    except Exception:
        raise ValueError('ACTUAL_IDENTITY_UNAVAILABLE') from None


def release_status():
    approved = os.getenv('NOMAD_APPROVED_RELEASE_SHA', '')
    render = os.getenv('RENDER_GIT_COMMIT', '')
    actual = ''
    reason = ''
    try:
        actual = actual_identity()
        if render and (not SHA.fullmatch(render) or render != actual):
            reason = 'RENDER_IDENTITY_CONFLICT'
        elif not SHA.fullmatch(approved):
            reason = 'APPROVED_IDENTITY_INVALID' if approved else 'APPROVED_IDENTITY_UNAVAILABLE'
        elif actual != approved:
            reason = 'RELEASE_IDENTITY_MISMATCH'
    except ValueError as error:
        reason = str(error)
    return dict(status='BLOCKED' if reason else 'PASS', deployed_sha=actual,
                approved_sha=approved if SHA.fullmatch(approved) else '', reason=reason,
                render_sha=render if SHA.fullmatch(render) else ('INVALID' if render else 'UNAVAILABLE'))


def release_identity():
    result = release_status()
    if result['status'] != 'PASS':
        raise ValueError('RELEASE_IDENTITY_UNVERIFIED')
    return {key: result[key] for key in ('status', 'deployed_sha', 'approved_sha')}


if __name__ == '__main__':
    evidence = checkout_evidence(ROOT)
    sha = verify_evidence(ROOT, evidence)
    (ROOT / ARTIFACT).write_text(json.dumps(evidence, sort_keys=True), encoding='utf-8')
    print('Verified deployment code identity: ' + sha)
