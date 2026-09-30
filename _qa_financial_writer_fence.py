"""Isolated PostgreSQL tests; never reads application connection environment."""
from contextlib import closing
import os
from pathlib import Path
import uuid
from unittest.mock import patch


def main():
    import psycopg2
    import financial_writer_control as gate
    import db
    options = dict(host='127.0.0.1', port=int(os.environ['ARETI_QA_PG_PORT']),
                   user='qa_local', sslmode='disable')
    name = 'qa_financial_fence_' + uuid.uuid4().hex
    with closing(psycopg2.connect(dbname='postgres', **options)) as admin:
        admin.autocommit = True
        with admin.cursor() as cur:
            cur.execute('SHOW data_directory')
            assert Path(cur.fetchone()[0]).resolve() == Path(os.environ['ARETI_QA_PG_DATA']).resolve()
            cur.execute('CREATE DATABASE ' + name)
        def connect(): return psycopg2.connect(dbname=name, **options)
        controller = connect()
        stale = connect()
        try:
            with controller.cursor() as cur:
                cur.execute('CREATE TABLE sample (id integer PRIMARY KEY, value integer)')
                cur.execute('INSERT INTO sample VALUES (1,0)')
            controller.commit()
            stale.set_session(isolation_level='SERIALIZABLE')
            with stale.cursor() as cur:
                cur.execute('SELECT value FROM sample'); assert cur.fetchone() == (0,)
            gate.acquire(controller)
            gate.install(controller)
            gate.begin(controller, 'synthetic', 'a'*40, 'b'*64, 'Areti')
            controller.commit()
            gate.assert_owned(controller)
            controller.rollback()
            def blocked_writer():
                with closing(db.PostgresConnection(connect())) as writer:
                    try:
                        writer.cursor().execute('UPDATE sample SET value=value+1 WHERE id=1')
                    except gate.WritersBlocked:
                        writer.rollback()
                    else:
                        raise AssertionError('Writer passed a blocking fence')
            blocked_writer()
            with closing(connect()) as reader:
                with reader.cursor() as cur:
                    cur.execute('SELECT value FROM sample'); assert cur.fetchone() == (0,)
            controller.close()
            blocked_writer()
            # The financial snapshot predates the fence. A fresh independent
            # read must still reject it after the controller has disconnected.
            with patch.object(db,'USING_POSTGRES',True),patch.object(db,'get_connection',side_effect=lambda:db.PostgresConnection(connect())):
                try: db.PostgresCursor(stale.cursor()).execute('UPDATE sample SET value=99')
                except gate.WritersBlocked: stale.rollback()
                else: raise AssertionError('Stale SERIALIZABLE snapshot bypassed durable fence')
            stale.close()
            controller = connect()
            gate.acquire(controller)
            assert gate.read(controller)['state'] == 'REPAIR_IN_PROGRESS'
            controller.rollback()
            gate.transition(controller, 'synthetic', 'REPAIR_IN_PROGRESS', 'REPAIR_COMMITTED_UNVERIFIED', 'reconciled')
            with controller.cursor() as cur:
                cur.execute('UPDATE sample SET value=10 WHERE id=1')
            controller.rollback()
            assert gate.read(controller)['state'] == 'REPAIR_IN_PROGRESS'
            controller.rollback()
            # Fence and changes share one commit, including an uncertain client outcome.
            gate.transition(controller, 'synthetic', 'REPAIR_IN_PROGRESS', 'REPAIR_COMMITTED_UNVERIFIED', 'reconciled')
            with controller.cursor() as cur:
                cur.execute('UPDATE sample SET value=10 WHERE id=1')
            controller.commit()
            controller.close()
            blocked_writer()
            controller = connect()
            gate.acquire(controller)
            assert gate.read(controller)['state'] == 'REPAIR_COMMITTED_UNVERIFIED'
            controller.rollback()
            gate.transition(controller, 'synthetic', 'REPAIR_COMMITTED_UNVERIFIED', 'RECOVERY_REQUIRED', 'verification_failed')
            controller.commit()
            blocked_writer()
            gate.transition(controller, 'synthetic', 'RECOVERY_REQUIRED', 'NORMAL', 'independently_verified')
            controller.commit()
            gate.release(controller)
            with closing(db.PostgresConnection(connect())) as writer:
                writer.cursor().execute('UPDATE sample SET value=value+1 WHERE id=1')
                writer.commit()
            with controller.cursor() as cur:
                cur.execute('SELECT value FROM sample'); assert cur.fetchone() == (11,)
            controller.rollback()
            gate.acquire(controller)
            try: gate.begin(controller, 'synthetic', 'a'*40, 'b'*64, 'Areti')
            except gate.WritersBlocked: controller.rollback()
            else: raise AssertionError('Repeated operation accepted')
            gate.release(controller)
            for query in ('DELETE FROM financial_control.fence',
                          'UPDATE financial_control.fence SET state=\'NORMAL\'',
                          'TRUNCATE financial_control.fence'):
                try:
                    with controller.cursor() as cur: cur.execute(query)
                except psycopg2.Error: controller.rollback()
                else: raise AssertionError('Durable evidence mutation accepted')
            print('PASS durable fence, commit/disconnect/restart, reads, blocked writers, rollback, recovery and repeat refusal')
        finally:
            stale.close()
            controller.close()
            with admin.cursor() as cur: cur.execute('DROP DATABASE ' + name)


if __name__ == '__main__': main()
