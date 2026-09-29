"""Non-autostart server command. No UI, route, startup import or silent apply."""
import argparse
from contextlib import closing
import getpass
import hashlib
import hmac
import json
import os
from pathlib import Path
import sys

import financial_atomic as work

APPROVED_PROJECT = 'nlbqogwckdeqqasejxbf'
# Digest of private reviewed evidence, not its confidential contents.
APPROVED_MANIFEST = 'f916116ae13a70602a46d553d93ae8e0cf4b34f58531c5b709db25240d1e651e'


def source_digest():
    root = Path(__file__).resolve().parent
    files = sorted(p for p in root.glob('*.py') if not p.name.startswith('_qa_'))
    files += [root/'requirements.txt']
    return work.state_hash({p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in files})


def connection():
    import db
    import psycopg2
    from psycopg2.extensions import parse_dsn
    work.require(db.USING_POSTGRES and db.DATABASE_URL, 'AUTHORIZED_DATABASE_CONFIG_MISSING')
    parts = parse_dsn(db.DATABASE_URL)
    host = parts.get('host', '')
    import re
    pooler = re.fullmatch(r'aws-[0-9]+-[a-z0-9-]+\.pooler\.supabase\.com', host)
    direct = host == 'db.' + APPROVED_PROJECT + '.supabase.co'
    work.require(direct or (pooler and parts.get('user', '').endswith('.' + APPROVED_PROJECT)), 'PROJECT_BINDING_FAILED')
    work.require(parts.get('dbname') == 'postgres' and parts.get('port', '5432') == '5432', 'CONNECTION_MODE_UNAPPROVED')
    work.require(not any(k in parts for k in ('hostaddr', 'options', 'service', 'servicefile')), 'ROUTING_OVERRIDE_UNAPPROVED')
    work.require(parts.get('sslmode') in ('require', 'verify-ca', 'verify-full'), 'TLS_CONFIGURATION_UNVERIFIED')
    return psycopg2.connect(db.DATABASE_URL, connect_timeout=15)


def confirm(args, plan_bytes):
    work.require(args.execute and args.confirm_plan == hashlib.sha256(plan_bytes).hexdigest(), 'EXPLICIT_PLAN_CONFIRMATION_REQUIRED')
    token = os.environ.get('NOMAD_ATOMIC_TOKEN', '')
    work.require(len(token) >= 32 and sys.stdin.isatty(), 'SERVER_OPERATOR_TOKEN_REQUIRED')
    work.require(hmac.compare_digest(getpass.getpass('Operator token (hidden): '), token), 'OPERATOR_TOKEN_INVALID')


def main(argv=None):
    parser = argparse.ArgumentParser(description='Explicit snapshot-backed financial operator; default read-only.')
    parser.add_argument('mode', choices=('preflight', 'apply', 'verify', 'reverse'), nargs='?', default='preflight')
    parser.add_argument('--manifest', required=True)
    parser.add_argument('--pdf', required=True)
    parser.add_argument('--plan', required=True)
    parser.add_argument('--operation-id', required=True)
    parser.add_argument('--actor', required=True)
    parser.add_argument('--execute', action='store_true')
    parser.add_argument('--confirm-plan')
    parser.add_argument('--confirm-after')
    parser.add_argument('--reversed-state', action='store_true')
    args = parser.parse_args(argv)
    try:
        work.require(not args.reversed_state or args.mode == 'verify', 'REVERSE_VERIFY_FLAG_INVALID')
        manifest_bytes = Path(args.manifest).read_bytes()
        work.require(hashlib.sha256(manifest_bytes).hexdigest() == APPROVED_MANIFEST, 'UNAPPROVED_PRIVATE_MANIFEST')
        manifest = json.loads(manifest_bytes)
        content = Path(args.pdf).read_bytes()
        work.require(hashlib.sha256(content).hexdigest() == manifest['pdf_sha256'], 'PDF_FINGERPRINT_CHANGED')
        plan_path = Path(args.plan).resolve()
        root = Path(__file__).resolve().parent
        work.require(not plan_path.is_relative_to(root), 'PLAN_MUST_REMAIN_OUTSIDE_APPLICATION')
        if os.name != 'nt':
            work.require(plan_path.parent.stat().st_mode & 0o077 == 0, 'PRIVATE_DIRECTORY_REQUIRED')
        if args.mode == 'preflight':
            with closing(connection()) as conn:
                plan = work.prepare(conn, content, manifest)
            envelope = dict(plan=plan, source_digest=source_digest(), operation_id=args.operation_id, actor=args.actor)
            data = json.dumps(envelope, sort_keys=True, separators=(',', ':')).encode()
            descriptor = os.open(plan_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, 'wb') as handle: handle.write(data)
            print('PASS READ_ONLY_PREFLIGHT PLAN_SHA256=' + hashlib.sha256(data).hexdigest())
            return 0
        plan_bytes = plan_path.read_bytes()
        envelope = json.loads(plan_bytes)
        work.require(envelope['source_digest'] == source_digest(), 'APPLICATION_SOURCE_CHANGED')
        work.require(envelope['operation_id'] == args.operation_id and envelope['actor'] == args.actor, 'OPERATOR_BINDING_CHANGED')
        work.require(envelope['plan']['manifest_hash'] == work.state_hash(manifest), 'MANIFEST_CHANGED')
        if args.mode in ('apply', 'reverse'):
            confirm(args, plan_bytes)
        if args.mode == 'apply':
            work.apply(connection, content=content, manifest=manifest, plan=envelope['plan'],
                       operation_id=args.operation_id, actor=args.actor)
        elif args.mode == 'reverse':
            work.require(args.confirm_after, 'EXPLICIT_POST_STATE_CONFIRMATION_REQUIRED')
            work.reverse(connection, operation_id=args.operation_id, content=content, expected_after_hash=args.confirm_after)
        else:
            result = work.verify(connection, operation_id=args.operation_id, content=content, reversed_state=args.reversed_state)
            print('PASS VERIFY AFTER_SHA256=' + result['after_hash'])
            return 0
        print('PASS ' + args.mode.upper() + '; independent read-back passed; private values not emitted')
        return 0
    except work.Blocked as error:
        print('BLOCKED ' + str(error), file=sys.stderr)
        return 2
    except BaseException:
        print('BLOCKED OPERATION_FAILED_CHECK_STATE_BEFORE_RETRY', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
