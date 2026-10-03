"""Synthetic real PostgreSQL integration of the durable atomic controller."""
from contextlib import closing
from io import BytesIO
from pathlib import Path
from unittest.mock import patch
import hashlib
import os
import uuid


def main():
    import psycopg2
    import db
    import parsing
    import financial_atomic as work
    import financial_writer_control as fence
    import nomad_controlled_repair as controlled
    from financial_preconditions import collect_locked
    from reportlab.pdfgen import canvas
    from _qa_safra_completion import fixture
    from _qa_safra_uat import labelled_accounts
    from _qa_safra_lifecycle import upload
    options = dict(host='127.0.0.1', port=int(os.environ['ARETI_QA_PG_PORT']), user='qa_local', sslmode='disable')
    name = 'qa_financial_controlled_' + uuid.uuid4().hex
    pages = fixture()
    buf = BytesIO(); pdf = canvas.Canvas(buf)
    for page in pages:
        body = pdf.beginText(30, 810); body.setFont('Helvetica', 8); body.setLeading(10)
        for line in page.splitlines(): body.textLine(line)
        pdf.drawText(body); pdf.showPage()
    pdf.save(); content = buf.getvalue()
    with closing(psycopg2.connect(dbname='postgres', **options)) as admin:
        admin.autocommit = True
        with admin.cursor() as cur:
            cur.execute('SHOW data_directory')
            assert Path(cur.fetchone()[0]).resolve() == Path(os.environ['ARETI_QA_PG_DATA']).resolve()
            cur.execute('CREATE DATABASE ' + name)
        def raw(): return psycopg2.connect(dbname=name, **options)
        def app(): return db.PostgresConnection(raw())
        try:
            accounts = labelled_accounts(parsing._parse_safra_pages(pages))
            with patch.object(db, 'USING_POSTGRES', True), patch.object(db, 'get_connection', app), patch.object(db, 'get_accounts', return_value=accounts):
                db.init_db()
                for sub in ('First','Second','Third','Fourth'):
                    db.add_category('Synthetic',sub,'Synthetic')
                with closing(app()) as rates:
                    rates.cursor().execute('INSERT INTO rates (rate_month,rate_type,rate_value) VALUES (?,?,?)', ('2026-01-01','GBP/USD','1.234567891'))
                    rates.commit()
                result = upload(db, pages, True, content)
                assert not result.errors, result.errors
            with closing(raw()) as setup:
                with setup.cursor() as cur:
                    cur.execute('SELECT id FROM classified_transactions ORDER BY id'); ids=[r[0] for r in cur.fetchall()]
                    for i in ids[:7]: cur.execute('UPDATE classified_transactions SET amount=amount+0.25 WHERE id=%s',(i,))
                    cur.execute('SELECT id FROM statement_balances ORDER BY id'); balances=[r[0] for r in cur.fetchall()]
                    for j in (0,2,3,5):
                        for field in (('opening_balance','closing_balance') if j==3 else ('opening_balance','money_out','closing_balance')):
                            cur.execute('UPDATE statement_balances SET '+field+'='+field+'+0.25 WHERE id=%s',(balances[j],))
                setup.commit()
                with setup.cursor() as cur:
                    before = work.capture(cur)
                    _, diagnostics = collect_locked(cur, content)
                setup.rollback()
            links = {(t,r['id']):r.get('statement_hash') for t,rs in before['money']['tables'].items() for r in rs}
            changes = [dict(table=r['Table'],id=r['Record ID'],field=r['Field'],old=r['Current database value'],new=r['Proposed PDF/parser value'],statement_hash=links[r['Table'],r['Record ID']])
                       for r in diagnostics['fields'] if r['Disposition']=='PDF DIFFERENCE - DIAGNOSTIC ONLY']
            manifest = dict(pdf_sha256=hashlib.sha256(content).hexdigest(),changes=changes,preconditions=diagnostics['hashes'])
            assert len(changes)==18
            class SyntheticReview:
                # Synthetic catalog approvals are injected ONLY in isolated QA.
                def execution_catalog(self,cur):
                    cur.execute("SELECT 1 FROM pg_event_trigger WHERE evtenabled<>'D'")
                    work.require(not cur.fetchall(),'SYNTHETIC_TRIGGER_CHANGED')
                def public_functions(self,functions): work.require(not functions,'SYNTHETIC_FUNCTION_CHANGED')
                def security(self,cur,catalog):
                    work.require(not any(r[2] or r[3] for r in catalog['security'] if r[0] in work.TABLES),'SYNTHETIC_RLS_CHANGED')
                    work.require(not catalog['policies'],'SYNTHETIC_POLICY_CHANGED')
                def preconditions(self,cur,pdf,plan):
                    work.require(plan==manifest,'SYNTHETIC_PLAN_CHANGED')
                def derive(self,cur,pdf): return manifest
            review=SyntheticReview()
            release={'approved_sha':controlled.RECOVERY_SOURCE_RELEASE}
            def state():
                with closing(raw()) as conn:
                    return fence.read(conn)
            def same_before():
                with closing(raw()) as conn:
                    with conn.cursor() as cur: actual=work.capture(cur)
                    assert work.state_hash(actual)==work.state_hash(before)
            def writers_blocked():
                mutations=[
                    'UPDATE classified_transactions SET amount=amount',
                    'UPDATE classified_transactions SET reviewed=reviewed',
                    'UPDATE classified_transactions SET split_group_id=split_group_id',
                    'INSERT INTO statement_imports(statement_hash) VALUES (\'blocked-import\')',
                    'UPDATE statement_balances SET closing_balance=closing_balance',
                    'UPDATE rates SET rate_value=rate_value']
                for query in mutations:
                    with closing(app()) as writer:
                        try: writer.cursor().execute(query)
                        except fence.WritersBlocked: writer.rollback()
                        else: raise AssertionError('Unprotected mutation: '+query.split()[0])
                with closing(app()) as reader:
                    reader.cursor().execute('SELECT COUNT(*) FROM classified_transactions').fetchone()
            def attempt(operation, connect=raw):
                with patch.object(controlled,'OPERATION',operation):
                    return controlled._execute(connect,review,content,release)
            def expect_failure(call):
                try: call()
                except BaseException as error:
                    if isinstance(error,AssertionError): raise
                    return
                raise AssertionError('Injected failure did not abort')
            # A frozen hash mismatch must leave no repair DDL/data/audit behind.
            with patch.object(work,'source',side_effect=work.Blocked('STALE_HASH')):
                expect_failure(lambda:attempt('stale'))
            same_before(); assert state()['state']=='NORMAL'
            first_attempt = state()['binding'].get('attempt_id')
            assert first_attempt, 'Execution attempt must have a unique identity'
            with patch.object(work,'source',side_effect=work.Blocked('STALE_HASH')):
                expect_failure(lambda:attempt('stale'))
            same_before(); assert state()['state']=='NORMAL'
            assert state()['binding']['attempt_id'] != first_attempt
            failure = state()['binding']['failure']
            assert failure['transaction_classification'] == 'ROLLED_BACK'
            assert failure['phase'] == 'prepare'
            assert failure['exception_class'] == 'Blocked'
            assert failure['commit_attempted'] is False
            # Fail after DDL inside the same transaction; control event rolls back too.
            with patch.object(work,'write_values',side_effect=work.Blocked('EXPECTED_OLD_VALUE_CHANGED')):
                expect_failure(lambda:attempt('old-value'))
            same_before(); assert state()['state']=='NORMAL'
            # Crash immediately before commit leaves no partial migration.
            def database_failure(cur, values):
                cur.execute('SELECT 1/0')
            with patch.object(work,'write_values',side_effect=database_failure):
                expect_failure(lambda:attempt('sql-failure'))
            same_before(); assert state()['state']=='NORMAL'
            failure = state()['binding']['failure']
            assert failure['phase']=='approved_corrections'
            assert failure['exception_class']=='DivisionByZero'
            assert failure['sqlstate']=='22012'
            assert failure['transaction_begun'] and not failure['commit_attempted']
            assert 'private payload' not in str(failure)
            real_transition=fence.transition
            def crash_before_commit(conn,op,old,new,phase):
                real_transition(conn,op,old,new,phase)
                if new=='REPAIR_COMMITTED_UNVERIFIED':
                    conn.close()
                    raise ConnectionError('synthetic controller lost')
            with patch.object(fence,'transition',side_effect=crash_before_commit):
                expect_failure(lambda:attempt('before-commit'))
            same_before(); assert state()['state']=='REPAIR_IN_PROGRESS'; writers_blocked()
            # Explicit isolated recovery only after proving the entire preimage.
            with closing(raw()) as conn:
                fence.acquire(conn)
                fence.transition(conn,'before-commit','REPAIR_IN_PROGRESS','NORMAL','test_confirmed_rollback')
                conn.commit(); fence.release(conn)
            class CommitFault:
                def __init__(self): self.inner=raw(); self.armed=False
                def __getattr__(self,k): return getattr(self.inner,k)
                def commit(self):
                    if self.armed:
                        self.inner.commit(); self.inner.close()
                        raise ConnectionError('lost COMMIT acknowledgement')
                    self.inner.commit()
            controller=CommitFault()
            def arm(conn,op,old,new,phase):
                real_transition(conn,op,old,new,phase)
                if new=='REPAIR_COMMITTED_UNVERIFIED': conn.armed=True
            with patch.object(fence,'transition',side_effect=arm):
                expect_failure(lambda:attempt('uncertain',lambda:controller))
            assert state()['state']=='REPAIR_COMMITTED_UNVERIFIED'; writers_blocked()
            assert work.verify(raw,operation_id='uncertain',content=content,review=review)['verified']
            # Independent verification failure must not reopen writers.
            with closing(raw()) as conn:
                fence.acquire(conn)
                fence.transition(conn,'uncertain','REPAIR_COMMITTED_UNVERIFIED','RECOVERY_REQUIRED','test_verify_failed')
                conn.commit()
            assert state()['state']=='RECOVERY_REQUIRED'; writers_blocked()
            with patch.object(controlled,'connection',raw),patch.object(controlled,'OPERATION','uncertain'), \
                 patch.object(controlled,'ReviewedNomad',return_value=review), \
                 patch('nomad_runtime.PDF_SHA256',hashlib.sha256(content).hexdigest()), \
                 patch.object(controlled.precheck,'require_auth'),patch.object(controlled.precheck,'release_identity',return_value={'approved_sha':'b'*40}):
                verified=controlled.verify_and_release(content,'VERIFY COMMITTED NOMAD REPAIR')
                assert verified['verified']
            assert state()['state']=='NORMAL'
            assert state()['phase']=='independently_verified:'+verified['after_hash']
            with closing(app()) as writer:
                writer.cursor().execute('UPDATE classified_transactions SET reviewed=reviewed'); writer.rollback()
            expect_failure(lambda:attempt('uncertain'))
            verified = work.verify(raw,operation_id='uncertain',content=content,review=review)
            work.reverse(raw,operation_id='uncertain',content=content,expected_after_hash=verified['after_hash'])
            # New local synthetic scenario after reverse; production frozen
            # expectations are never refreshed by this test or application code.
            with closing(raw()) as conn:
                with conn.cursor() as cur:
                    before = work.capture(cur)
                    _, fresh = collect_locked(cur,content)
            manifest = dict(manifest,preconditions=fresh['hashes'])
            with patch.object(work,'verify',side_effect=work.Blocked('SYNTHETIC_VERIFY_FAILURE')):
                expect_failure(lambda:attempt('verify-failure'))
            assert state()['state']=='RECOVERY_REQUIRED'; writers_blocked()
            protected_state = state()
            with patch.object(controlled,'connection',raw),patch.object(controlled,'OPERATION','verify-failure'), \
                 patch.object(controlled,'ReviewedNomad',return_value=review), \
                 patch('nomad_runtime.PDF_SHA256',hashlib.sha256(content).hexdigest()), \
                 patch.object(controlled.precheck,'require_auth'),patch.object(controlled.precheck,'release_identity',return_value={'approved_sha':'b'*40}), \
                 patch.object(work,'verify',side_effect=work.Blocked('POST_STATE_CHANGED')):
                expect_failure(lambda:controlled.verify_and_release(content,'VERIFY COMMITTED NOMAD REPAIR'))
            assert state() == protected_state
            writers_blocked()
            with patch.object(controlled,'connection',raw),patch.object(controlled,'OPERATION','verify-failure'), \
                 patch.object(controlled,'ReviewedNomad',return_value=review), \
                 patch('nomad_runtime.PDF_SHA256',hashlib.sha256(content).hexdigest()), \
                 patch.object(controlled.precheck,'require_auth'),patch.object(controlled.precheck,'release_identity',return_value={'approved_sha':'b'*40}):
                verified=controlled.verify_and_release(content,'VERIFY COMMITTED NOMAD REPAIR')
            assert state()['state']=='NORMAL'
            assert state()['phase']=='independently_verified:'+verified['after_hash']
            work.reverse(raw,operation_id='verify-failure',content=content,expected_after_hash=verified['after_hash'])
            with closing(raw()) as conn:
                with conn.cursor() as cur:
                    _, fresh = collect_locked(cur,content)
            manifest = dict(manifest,preconditions=fresh['hashes'])
            actual_verify=work.verify
            def verify_while_closed(*args,**kwargs):
                assert state()['state']=='REPAIR_COMMITTED_UNVERIFIED'
                writers_blocked()
                return actual_verify(*args,**kwargs)
            with patch.object(work,'verify',side_effect=verify_while_closed):
                assert attempt('verified-success')['changed']==18
            assert state()['state']=='NORMAL'
            print('PASS actual controller atomic migration/18 corrections, stale/old abort, precommit crash, uncertain committed outcome, persistent fence, writer coverage, independent recovery')
        finally:
            with admin.cursor() as cur: cur.execute('DROP DATABASE '+name)


if __name__=='__main__': main()
