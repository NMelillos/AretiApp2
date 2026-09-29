"""Read-only, isolated PostgreSQL proof of application-pool session restoration."""
import os
from pathlib import Path
from unittest.mock import Mock


def main():
    import psycopg2
    import db
    from financial_atomic_command import _ApplicationLease
    conn=psycopg2.connect(host='127.0.0.1',port=int(os.environ['ARETI_QA_PG_PORT']),
                          user='qa_local',dbname='postgres',sslmode='disable')
    try:
        with conn.cursor() as cur:
            cur.execute('SHOW data_directory')
            assert Path(cur.fetchone()[0]).resolve() == Path(os.environ['ARETI_QA_PG_DATA']).resolve()
        conn.rollback()
        original=(conn.autocommit,conn.isolation_level,conn.readonly,conn.deferrable)
        pool=Mock()
        owner=db.PostgresConnection(conn,pool)
        lease=_ApplicationLease(owner)
        lease.set_session(readonly=True,autocommit=False,isolation_level='REPEATABLE READ')
        with lease.cursor() as cur:
            cur.execute('SHOW transaction_read_only'); assert cur.fetchone()[0] == 'on'
            cur.execute('SELECT 1'); assert cur.fetchone()[0] == 1
        lease.rollback(); lease.close(); lease.close()
        pool.putconn.assert_called_once_with(conn,close=False)
        assert (conn.autocommit,conn.isolation_level,conn.readonly,conn.deferrable)==original
        with conn.cursor() as cur:
            cur.execute('SHOW transaction_read_only'); assert cur.fetchone()[0] == 'off'
        conn.rollback()
    finally: conn.close()
    print('PASS isolated PostgreSQL read-only lease, raw cursor contract, exact pool settings restored, no DDL/DML')


if __name__ == '__main__': main()
