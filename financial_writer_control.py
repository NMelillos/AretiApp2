"""Cooperative writer barrier plus a durable, append-only recovery fence.

No initialization on import/startup. Only the authenticated repair controller may
install the ledger after validating the reviewed provider DDL catalog.
"""
import json
import re

KEY = 617294382
SCHEMA = 'financial_control'
STATES = frozenset(('NORMAL', 'REPAIR_IN_PROGRESS', 'REPAIR_COMMITTED_UNVERIFIED', 'RECOVERY_REQUIRED'))
BODY = " BEGIN RAISE EXCEPTION 'Financial control evidence is append-only'; END "


class WritersBlocked(RuntimeError):
    def __init__(self):
        super().__init__('Financial maintenance or recovery is active. No changes were saved.')


def require(ok):
    if not ok: raise WritersBlocked()


def _bounds(cur):
    cur.execute("SET LOCAL lock_timeout='3s'")
    cur.execute("SET LOCAL statement_timeout='120s'")


def _read(cur):
    cur.execute("SELECT to_regnamespace('financial_control'),to_regclass('financial_control.fence')")
    namespace, table = cur.fetchone()
    if namespace is None:
        require(table is None)
        return {'state': 'NORMAL', 'repair_id': None}
    require(table is not None)
    cur.execute('SELECT state,repair_id,binding,phase FROM financial_control.fence ORDER BY revision DESC LIMIT 1')
    row = cur.fetchone()
    require(row is not None and row[0] in STATES)
    return dict(state=row[0], repair_id=row[1], binding=row[2], phase=row[3])


def read(conn):
    with conn.cursor() as cur: return _read(cur)


def _fresh_normal(writer):
    # SERIALIZABLE/SPLIT must retain its isolation semantics. Its snapshot may
    # predate acquisition of the shared lock, so inspect the fence on a fresh
    # READ COMMITTED lease from the *same* existing application pool instead.
    import db
    require(db.USING_POSTGRES)
    owner = db.get_connection()
    try:
        reader = owner._connection
        require(reader is not writer and reader.get_backend_pid() != writer.get_backend_pid())
        from psycopg2.extensions import TRANSACTION_STATUS_IDLE
        require(reader.get_transaction_status() == TRANSACTION_STATUS_IDLE)
        expected, actual = writer.get_dsn_parameters(), reader.get_dsn_parameters()
        require(all(expected.get(k) == actual.get(k) for k in ('host','port','dbname','user')))
        with reader.cursor() as cur:
            cur.execute('BEGIN TRANSACTION ISOLATION LEVEL READ COMMITTED READ ONLY')
            require(_read(cur)['state'] == 'NORMAL')
    finally:
        owner.close()


def writer_guard(cur):
    # Read the fence *after* acquiring the shared lock, without changing the
    # caller's financial transaction isolation or relying on an old snapshot.
    try:
        cur.execute('SHOW transaction_isolation')
        isolation = cur.fetchone()[0]
        require(isolation in ('read committed','repeatable read','serializable'))
        cur.execute('SELECT pg_try_advisory_xact_lock_shared(%s)', (KEY,))
        require(cur.fetchone()[0] is True)
        if isolation == 'read committed':
            require(_read(cur)['state'] == 'NORMAL')
        else:
            _fresh_normal(cur.connection)
    except WritersBlocked:
        # A rejected writer must not retain a shared lock on a pooled session.
        cur.connection.rollback()
        raise


def needs_guard(query):
    if not isinstance(query, str): return True
    text = query.strip().rstrip(';').strip()
    if text.upper() in ('BEGIN', 'BEGIN TRANSACTION',
            'BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY',
            'SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY',
            'SET TRANSACTION ISOLATION LEVEL SERIALIZABLE',
            'SET LOCAL EXTRA_FLOAT_DIGITS = 3'):
        return False
    if re.fullmatch(r'SET\s+LOCAL\s+extra_float_digits\s*=\s*3', text, re.I):
        return False
    # Fail closed for CTEs, multiple statements, lock-taking reads, CALL and DO.
    # Ordinary catalog/report SELECT and SHOW stay available during maintenance.
    return not (re.match(r'^(SELECT|SHOW)\b', text, re.I)
                and ';' not in text
                and not re.search(r'\bFOR\s+(UPDATE|SHARE|NO\s+KEY\s+UPDATE|KEY\s+SHARE)\b|pg_(try_)?advisory', text, re.I))


def assert_owned(conn):
    with conn.cursor() as cur:
        cur.execute("SELECT 1 FROM pg_locks WHERE locktype='advisory' AND pid=pg_backend_pid() AND classid=0 AND objid=%s AND objsubid=1 AND mode='ExclusiveLock' AND granted", (KEY,))
        require(cur.fetchone() == (1,))


def acquire(conn):
    try:
        with conn.cursor() as cur:
            _bounds(cur)
            cur.execute('SELECT pg_try_advisory_lock(%s)', (KEY,))
            require(cur.fetchone()[0] is True)
        conn.commit()
        assert_owned(conn)
        conn.rollback()
    except BaseException:
        # An uncertain lock-acquisition COMMIT must not leak a session lock into
        # the application pool before the controller's main try/finally starts.
        getattr(conn, 'raw', conn).close()
        raise


def release(conn):
    require(read(conn)['state'] == 'NORMAL')
    assert_owned(conn)
    with conn.cursor() as cur:
        cur.execute('SELECT pg_advisory_unlock(%s)', (KEY,))
        require(cur.fetchone()[0] is True)
    conn.commit()


def install(conn):
    assert_owned(conn)
    with conn.cursor() as cur:
        _bounds(cur)
        cur.execute("SELECT to_regnamespace('financial_control')")
        if cur.fetchone()[0] is None:
            cur.execute('CREATE SCHEMA financial_control')
            cur.execute('REVOKE ALL ON SCHEMA financial_control FROM PUBLIC')
            cur.execute('''CREATE TABLE financial_control.fence (
                revision bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
                state text NOT NULL CHECK(state IN ('NORMAL','REPAIR_IN_PROGRESS','REPAIR_COMMITTED_UNVERIFIED','RECOVERY_REQUIRED')),
                repair_id text, binding jsonb NOT NULL, phase text NOT NULL,
                recorded_at timestamptz NOT NULL DEFAULT CURRENT_TIMESTAMP)''')
            cur.execute('REVOKE ALL ON financial_control.fence FROM PUBLIC')
            cur.execute('CREATE FUNCTION financial_control.reject_mutation() RETURNS trigger LANGUAGE plpgsql AS %s', (BODY,))
            cur.execute('REVOKE ALL ON FUNCTION financial_control.reject_mutation() FROM PUBLIC')
            cur.execute('CREATE TRIGGER immutable BEFORE UPDATE OR DELETE OR TRUNCATE ON financial_control.fence FOR EACH STATEMENT EXECUTE FUNCTION financial_control.reject_mutation()')
            cur.execute('ALTER TABLE financial_control.fence ENABLE ALWAYS TRIGGER immutable')
            cur.execute("INSERT INTO financial_control.fence(state,binding,phase) VALUES ('NORMAL','{}','installed')")
        integrity(cur)


def integrity(cur):
    cur.execute("SELECT prosrc,prosecdef FROM pg_proc WHERE oid='financial_control.reject_mutation()'::regprocedure")
    require(cur.fetchone() == (BODY, False))
    cur.execute("SELECT t.tgtype,t.tgenabled,t.tgfoid='financial_control.reject_mutation()'::regprocedure FROM pg_trigger t WHERE tgrelid='financial_control.fence'::regclass AND NOT tgisinternal")
    require(cur.fetchall() == [(58, 'A', True)])
    cur.execute("SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace CROSS JOIN LATERAL aclexplode(c.relacl) a WHERE n.nspname='financial_control' AND a.grantee<>c.relowner")
    require(not cur.fetchall())
    cur.execute("SELECT 1 FROM pg_namespace n CROSS JOIN LATERAL aclexplode(n.nspacl) a WHERE n.nspname='financial_control' AND a.grantee<>n.nspowner")
    require(not cur.fetchall())


def begin(conn, repair_id, release_sha, evidence_digest, actor):
    assert_owned(conn)
    require(actor == 'Areti' and re.fullmatch('[0-9a-fA-F]{40}', release_sha)
            and re.fullmatch('[0-9a-f]{64}', evidence_digest))
    with conn.cursor() as cur:
        _bounds(cur)
        integrity(cur)
        require(_read(cur)['state'] == 'NORMAL')
        cur.execute('SELECT 1 FROM financial_control.fence WHERE repair_id=%s', (repair_id,))
        require(not cur.fetchall())
        cur.execute("INSERT INTO financial_control.fence(state,repair_id,binding,phase) VALUES ('REPAIR_IN_PROGRESS',%s,%s::jsonb || jsonb_build_object('started_at',CURRENT_TIMESTAMP),'precheck')",
                    (repair_id, json.dumps(dict(release_sha=release_sha, evidence_digest=evidence_digest, actor=actor))))


def transition(conn, repair_id, expected, target, phase):
    assert_owned(conn)
    allowed = {('REPAIR_IN_PROGRESS','NORMAL'), ('REPAIR_IN_PROGRESS','REPAIR_COMMITTED_UNVERIFIED'),
               ('REPAIR_IN_PROGRESS','RECOVERY_REQUIRED'), ('REPAIR_COMMITTED_UNVERIFIED','RECOVERY_REQUIRED'),
               ('REPAIR_COMMITTED_UNVERIFIED','NORMAL'), ('RECOVERY_REQUIRED','NORMAL')}
    require((expected, target) in allowed)
    with conn.cursor() as cur:
        _bounds(cur)
        integrity(cur)
        previous = _read(cur)
        require(previous['state'] == expected and previous['repair_id'] == repair_id)
        cur.execute('INSERT INTO financial_control.fence(state,repair_id,binding,phase) VALUES (%s,%s,%s,%s)',
                    (target, repair_id, json.dumps(previous['binding']), phase))
