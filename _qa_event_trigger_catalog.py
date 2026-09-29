"""Real isolated PostgreSQL catalog query validation; no fixture DDL or DML."""
import os
from pathlib import Path
from unittest.mock import patch, Mock


def main():
    import psycopg2
    import db
    import event_trigger_diagnostics as d
    from financial_atomic_command import _ApplicationLease
    conn=psycopg2.connect(host='127.0.0.1',port=int(os.environ['ARETI_QA_PG_PORT']),
                          user='qa_local',dbname='postgres',sslmode='disable')
    try:
        with conn.cursor() as cur:
            cur.execute('SHOW data_directory')
            assert Path(cur.fetchone()[0]).resolve()==Path(os.environ['ARETI_QA_PG_DATA']).resolve()
        conn.rollback()
        with patch.object(d,'authorized',return_value=True),patch.object(d,'connection',
            return_value=_ApplicationLease(db.PostgresConnection(conn,Mock()))):
            evidence=d.collect()
            assert set(evidence['targets'])=={'classified_transactions','statement_balances','rates'}
            assert sum(map(len,evidence['targets'].values()))==9
            assert isinstance(d.safe_export(evidence),bytes)
        with conn.cursor() as cur:
            cur.execute('SHOW transaction_read_only'); assert cur.fetchone()[0]=='off'
        conn.rollback()
    finally: conn.close()
    print('PASS real PostgreSQL catalog queries, read-only retrieval, rollback and lease restoration')


if __name__=='__main__': main()
