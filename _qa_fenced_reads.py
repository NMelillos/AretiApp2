"""Real PostgreSQL reads and mutations under every durable blocking state."""
from contextlib import closing
from pathlib import Path
from unittest.mock import patch
import os
import uuid


def main():
    import psycopg2
    import db
    import financial_writer_control as gate
    import nomad_recovery_status as recovery
    reads = (
        'WITH first_tx AS (SELECT 1 AS id) SELECT * FROM first_tx',
        '/* read */ WITH RECURSIVE n AS (SELECT 1 AS v UNION ALL SELECT v+1 FROM n WHERE v<2) SELECT * FROM n',
        "SELECT 'DELETE; UPDATE; FOR UPDATE' AS label",
        'SELECT 1; -- trailing comment',
        'WITH a AS (WITH b AS (SELECT 1) SELECT * FROM b) SELECT * FROM a',
        'SELECT * FROM statement_imports WHERE id=?',
        'SHOW transaction_read_only',
    )
    for query in reads:
        assert not gate.needs_guard(query), 'Read incorrectly guarded: ' + query
    mutations = (
        'WITH x AS (DELETE FROM statement_imports RETURNING *) SELECT * FROM x',
        'WITH x AS (SELECT 1) UPDATE statement_imports SET transaction_count=0',
        'WITH x AS (SELECT 1) SELECT * INTO unwanted_table FROM x',
        'SELECT * INTO unwanted_table FROM statement_imports',
        'SELECT * FROM statement_imports FOR UPDATE',
        'WITH x AS (SELECT * FROM statement_imports FOR SHARE) SELECT * FROM x',
        'SELECT 1; DELETE FROM statement_imports',
        "SELECT nextval('some_sequence')",
        'SELECT pg_advisory_lock(1)',
        'INSERT INTO statement_imports(statement_hash) VALUES (\'blocked\')',
        'UPDATE classified_transactions SET reviewed=1',
        'DELETE FROM statement_balances',
        'CREATE TABLE unwanted_table(id integer)',
        'ALTER TABLE rates ADD COLUMN unwanted integer',
        'TRUNCATE rates',
    )
    for query in mutations:
        assert gate.needs_guard(query), 'Mutation escaped guard: ' + query
    options = dict(host='127.0.0.1', port=int(os.environ['ARETI_QA_PG_PORT']), user='qa_local', sslmode='disable')
    name = 'qa_financial_reads_' + uuid.uuid4().hex
    with closing(psycopg2.connect(dbname='postgres', **options)) as admin:
        admin.autocommit = True
        with admin.cursor() as cur:
            cur.execute('SHOW data_directory')
            assert Path(cur.fetchone()[0]).resolve() == Path(os.environ['ARETI_QA_PG_DATA']).resolve()
            cur.execute('CREATE DATABASE ' + name)
        def raw(): return psycopg2.connect(dbname=name, **options)
        def app(): return db.PostgresConnection(raw())
        controller = raw()
        try:
            with patch.object(db, 'USING_POSTGRES', True), patch.object(db, 'get_connection', app):
                db.init_db()
                gate.acquire(controller)
                gate.install(controller)
                gate.begin(controller, 'synthetic', 'a'*40, 'b'*64, 'Areti')
                controller.commit()
                for state in ('REPAIR_IN_PROGRESS', 'REPAIR_COMMITTED_UNVERIFIED', 'RECOVERY_REQUIRED'):
                    if state != 'REPAIR_IN_PROGRESS':
                        old = gate.read(controller)['state']
                        gate.transition(controller, 'synthetic', old, state, 'synthetic')
                        controller.commit()
                    assert db.get_import_history().empty
                    assert db.get_import_transaction_audit().empty
                    assert db.get_exact_duplicate_audit().empty
                    assert db.get_cross_statement_duplicate_audit().empty
                    with controller.cursor() as cur:
                        cur.execute('SELECT count(*) FROM financial_control.fence')
                        fence_count = cur.fetchone()[0]
                    controller.rollback()
                    with patch.object(recovery.precheck, 'require_auth'), patch.object(recovery, 'connection', raw):
                        assert recovery.read_status()['state'] == state
                        assert recovery.read_status()['state'] == state
                    with controller.cursor() as cur:
                        cur.execute('SELECT count(*) FROM financial_control.fence')
                        assert cur.fetchone()[0] == fence_count
                    controller.rollback()
                    with closing(app()) as conn:
                        for query in reads[:-1]:
                            cursor = conn.cursor()
                            cursor.execute(query, (1,) if '?' in query else None)
                            cursor.fetchall()
                        conn.rollback()
                    # Independent read-only verification never asks for a writer lock.
                    with closing(raw()) as reader:
                        reader.set_session(readonly=True, isolation_level='REPEATABLE READ')
                        assert gate.read(reader)['state'] == state
                        reader.rollback()
                    for query in mutations:
                        with closing(app()) as writer:
                            try: writer.cursor().execute(query)
                            except gate.WritersBlocked: writer.rollback()
                            else: raise AssertionError('Blocked mutation executed')
                    assert gate.read(controller)['state'] == state
                    controller.rollback()
                controller.close()
                with closing(raw()) as reader:
                    assert gate.read(reader)['state'] == 'RECOVERY_REQUIRED'
                    with reader.cursor() as cur:
                        cur.execute("SELECT to_regclass('public.unwanted_table')")
                        assert cur.fetchone() == (None,)
            print('PASS read-only CTE/history/report/verification reads; all writer states stay fenced; mutation/DDL rejection; no auto-clear')
        finally:
            controller.close()
            with admin.cursor() as cur: cur.execute('DROP DATABASE ' + name)


if __name__ == '__main__': main()
