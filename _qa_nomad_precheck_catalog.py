"""Execute final-precheck catalog queries in real isolated read-only PostgreSQL."""
import os
from pathlib import Path


def main():
    import psycopg2
    from nomad_precheck import CATALOG_QUERIES,TABLES,ReadOnlyCursor
    conn=psycopg2.connect(host='127.0.0.1',port=int(os.environ['ARETI_QA_PG_PORT']),user='qa_local',dbname='postgres',sslmode='disable')
    try:
        conn.set_session(readonly=True,autocommit=False,isolation_level='REPEATABLE READ')
        with conn.cursor() as raw:
            cur=ReadOnlyCursor(raw)
            cur.execute('SHOW data_directory')
            assert Path(cur.fetchone()[0]).resolve()==Path(os.environ['ARETI_QA_PG_DATA']).resolve()
            cur.execute('SHOW transaction_read_only'); assert cur.fetchone()[0]=='on'
            for name,query in CATALOG_QUERIES.items():
                cur.execute(query,(TABLES,TABLES) if name in ('constraints','dependencies') else (TABLES,))
                cur.fetchall()
    finally: conn.rollback(); conn.close()
    print('PASS real PostgreSQL read-only catalog/default/constraint/index/FK/dependency queries; no DDL or DML')


if __name__=='__main__': main()
