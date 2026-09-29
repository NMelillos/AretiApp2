"""Synthetic six-page PDF -> live runtime plan -> atomic PostgreSQL verification."""
from contextlib import closing
from io import BytesIO
import hashlib
import os
from pathlib import Path
from unittest.mock import patch
import uuid


def main():
    import psycopg2
    import db
    import parsing
    import financial_atomic as work
    import nomad_runtime as runtime
    from _qa_safra_completion import fixture
    from _qa_safra_uat import labelled_accounts
    from _qa_safra_lifecycle import upload
    from reportlab.pdfgen import canvas
    options = dict(host='127.0.0.1',port=int(os.environ['ARETI_QA_PG_PORT']),user='qa_local',sslmode='disable')
    name = 'qa_nomad_runtime_' + uuid.uuid4().hex
    pages = fixture()
    stream=BytesIO(); pdf=canvas.Canvas(stream)
    for page in pages:
        text=pdf.beginText(30,810); text.setFont('Helvetica',8); text.setLeading(10)
        for line in page.splitlines(): text.textLine(line)
        pdf.drawText(text); pdf.showPage()
    pdf.save(); content=stream.getvalue()
    with closing(psycopg2.connect(dbname='postgres',**options)) as admin:
        admin.autocommit=True
        with admin.cursor() as cur:
            cur.execute('SHOW data_directory')
            assert Path(cur.fetchone()[0]).resolve() == Path(os.environ['ARETI_QA_PG_DATA']).resolve()
            cur.execute('CREATE DATABASE '+name)
        def connect(): return psycopg2.connect(dbname=name,**options)
        try:
            accounts=labelled_accounts(parsing._parse_safra_pages(pages))
            with patch.object(db,'USING_POSTGRES',True), patch.object(db,'get_connection',lambda:db.PostgresConnection(connect())), \
                 patch.object(db,'get_accounts',return_value=accounts):
                db.init_db()
                for sub in ('First','Second','Third','Fourth'): db.add_category('Synthetic',sub,'Synthetic')
                with closing(connect()) as conn:
                    with conn.cursor() as cur:
                        for table,start in (('classified_transactions',5910),('statement_balances',165),('statement_imports',169)):
                            cur.execute("SELECT setval(pg_get_serial_sequence(%s,'id'),%s,false)",(table,start))
                        cur.execute("INSERT INTO rates(rate_month,rate_type,rate_value) VALUES ('2026-01-01','GBP/USD',1.23456789)")
                    conn.commit()
                assert not upload(db,pages,True,content).errors
            with closing(connect()) as conn:
                with conn.cursor() as cur:
                    cur.execute("UPDATE classified_transactions SET category='Synthetic',subcategory='First',reviewed=1")
                    for table,row_id,field in sorted(runtime.APPROVED_FIELDS):
                        cur.execute('UPDATE '+table+' SET '+field+'='+field+'+0.25 WHERE id=%s',(row_id,))
                conn.commit()
            def capture():
                with closing(connect()) as conn:
                    work.session(conn,True)
                    with conn.cursor() as cur: result=work.capture(cur)
                    conn.rollback()
                    return result
            before=capture()
            with patch.object(runtime,'authorized',return_value=True), patch.object(runtime,'session_id',return_value='synthetic'), \
                 patch.object(runtime,'PDF_SHA256',hashlib.sha256(content).hexdigest()), patch.object(runtime,'connection',connect):
                evidence=runtime.precheck(content)
                assert work.state_hash(capture()) == work.state_hash(before), 'Precheck wrote to database'
                assert len(evidence['manifest']['changes']) == 18
                assert len(evidence['manifest']['preconditions']) == 24
                # Failure after DDL must also roll back the snapshot/journal.
                try:
                    work.apply(connect,content=content,manifest=evidence['manifest'],plan=evidence['plan'],
                               operation_id=runtime.OPERATION,actor='Areti',fail_at='ddl')
                except work.Blocked: pass
                else: raise AssertionError('Forced failure accepted')
                assert work.state_hash(capture()) == work.state_hash(before)
                result=work.apply(connect,content=content,manifest=evidence['manifest'],plan=evidence['plan'],
                                  operation_id=runtime.OPERATION,actor='Areti')
                assert result['changed'] == 18 and not result['repeated']
                assert work.verify(connect,operation_id=runtime.OPERATION,content=content)['verified']
                after=capture()
                try: runtime.precheck(content)
                except work.Blocked: pass
                else: raise AssertionError('Already repaired scope accepted')
                assert work.state_hash(capture()) == work.state_hash(after)
        finally:
            with admin.cursor() as cur:
                cur.execute('DROP DATABASE '+name+' WITH (FORCE)')
    print('PASS isolated PostgreSQL runtime derivation, zero-write precheck, rollback, apply, fresh verification, repeat refusal')


if __name__ == '__main__': main()
