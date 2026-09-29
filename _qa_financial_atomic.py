"""Synthetic PostgreSQL acceptance for the snapshot-backed atomic workflow."""
from contextlib import closing
from decimal import Decimal
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
    from financial_preconditions import collect_locked
    from reportlab.pdfgen import canvas
    from _qa_safra_completion import fixture
    from _qa_safra_uat import labelled_accounts
    from _qa_safra_lifecycle import upload
    from repair_readiness import state_hash
    from report_money import decimal_sum
    import pandas as pd
    import openpyxl

    options = dict(host='127.0.0.1', port=int(os.environ['ARETI_QA_PG_PORT']), user='qa_local', sslmode='disable')
    name = 'qa_financial_atomic_' + uuid.uuid4().hex
    pages = fixture()
    # Synthetic cents example with a reconciled large opening/closing balance.
    text = pages[2].replace('1 000,00', '1 000 000,00')
    balance = Decimal('1000000.00')
    for j in range(4):
        amount = Decimal('148351.30') if j == 0 else Decimal('30.00')
        balance -= amount
        def money(value): return f'{value:.2f}'.replace('.', ',')
        text = text.replace(f'30,00 {970-30*j},00', f'{money(amount)} {money(balance)}')
    pages[2] = text.replace('Balance in your favour 880,00', 'Balance in your favour ' + money(balance))
    buffer = BytesIO()
    pdf = canvas.Canvas(buffer)
    for page in pages:
        body = pdf.beginText(30, 810)
        body.setFont('Helvetica', 8)
        body.setLeading(10)
        for line in page.splitlines(): body.textLine(line)
        pdf.drawText(body)
        pdf.showPage()
    pdf.save()
    content = buffer.getvalue()
    with closing(psycopg2.connect(dbname='postgres', **options)) as admin:
        admin.autocommit = True
        with admin.cursor() as cur:
            cur.execute('SHOW data_directory')
            assert Path(cur.fetchone()[0]).resolve() == Path(os.environ['ARETI_QA_PG_DATA']).resolve()
            cur.execute('CREATE DATABASE ' + name)
        def raw(): return psycopg2.connect(dbname=name, **options)
        def app_connection(): return db.PostgresConnection(raw())
        try:
            accounts = labelled_accounts(parsing._parse_safra_pages(pages))
            with patch.object(db, 'USING_POSTGRES', True), patch.object(db, 'get_connection', app_connection), patch.object(db, 'get_accounts', return_value=accounts):
                db.init_db()
                for sub in ('First', 'Second', 'Third', 'Fourth'):
                    db.add_category('Synthetic', sub, 'Synthetic')
                with closing(app_connection()) as rates:
                    rates.cursor().execute('INSERT INTO rates (rate_month,rate_type,rate_value) VALUES (?,?,?)', ('2026-01-01', 'GBP/USD', '1.234567891'))
                    rates.commit()
                result = upload(db, pages, True, content)
                assert not result.errors, result.errors
            with closing(raw()) as conn:
                with conn.cursor() as cur:
                    cur.execute("UPDATE classified_transactions SET category='Synthetic',subcategory='First',reviewed=1")
                    cur.execute('SELECT id FROM classified_transactions ORDER BY id')
                    ids = [r[0] for r in cur.fetchall()]
                    for row_id in ids[:7]:
                        cur.execute('UPDATE classified_transactions SET amount=amount+0.25 WHERE id=%s', (row_id,))
                    cur.execute('SELECT id FROM statement_balances ORDER BY id')
                    balances = [r[0] for r in cur.fetchall()]
                    for index in (0, 2, 3, 5):
                        fields = ('opening_balance', 'closing_balance') if index == 3 else ('opening_balance', 'money_out', 'closing_balance')
                        for field in fields:
                            cur.execute('UPDATE statement_balances SET '+field+'='+field+'+0.25 WHERE id=%s', (balances[index],))
                    # Unrelated rows, including split and sub-cent/large/NULL values,
                    # must survive apply/reverse without logical changes.
                    cur.execute("INSERT INTO classified_transactions (statement_hash,amount,amount_usd,fx_rate,split_original_amount,split_group_id,reviewed) VALUES ('unrelated',%s,%s,%s,%s,'synthetic-group',1)",
                                ('-9007199254740991.37', '0.0000000123456789', '1.234567890123', '-9007199254740991.37'))
                    cur.execute("INSERT INTO rates (rate_month,rate_type,rate_value) VALUES ('2026-02-01','Synthetic zero',%s)", ('-0',))
                    cur.execute('ALTER TABLE classified_transactions ADD CONSTRAINT synthetic_amount_required CHECK(amount IS NOT NULL)')
                conn.commit()
                def capture():
                    with conn.cursor() as cur: result = work.capture(cur)
                    conn.rollback()
                    return result
                before = capture()
                with conn.cursor() as cur:
                    _, diagnostics = collect_locked(cur, content)
                conn.rollback()
                rows = {(t, r['id']): r for t, rs in before['money']['tables'].items() for r in rs}
                changes = [dict(table=r['Table'], id=r['Record ID'], field=r['Field'], old=r['Current database value'],
                                new=r['Proposed PDF/parser value'], statement_hash=rows[r['Table'], r['Record ID']]['statement_hash'])
                           for r in diagnostics['fields'] if r['Disposition'] == 'PDF DIFFERENCE - DIAGNOSTIC ONLY']
                manifest = dict(pdf_sha256=hashlib.sha256(content).hexdigest(), changes=changes, preconditions=diagnostics['hashes'])
                assert len(changes) == 18 and len({(r['table'], r['id']) for r in changes}) == 11
                assert len(diagnostics['hashes']) == 24
                plan = work.prepare(conn, content, manifest)
                assert state_hash(capture()) == state_hash(before), 'Read-only preflight mutated state'
                conn.set_session(readonly=False)
                with conn.cursor() as cur:
                    cur.execute('''CREATE FUNCTION public.unreviewed_synthetic() RETURNS integer
                        LANGUAGE sql AS 'SELECT 1' ''')
                    try: work.catalog(cur)
                    except work.Blocked: pass
                    else: raise AssertionError('Unreviewed public function accepted')
                conn.rollback()
                with conn.cursor() as cur:
                    cur.execute('CREATE SCHEMA synthetic_event')
                    cur.execute("CREATE FUNCTION synthetic_event.watch() RETURNS event_trigger LANGUAGE plpgsql AS 'BEGIN RETURN; END'")
                    cur.execute('CREATE EVENT TRIGGER synthetic_watch ON ddl_command_start EXECUTE FUNCTION synthetic_event.watch()')
                    try: work.catalog(cur)
                    except work.Blocked as error:
                        assert str(error) == 'DDL_EVENT_TRIGGERS_REQUIRE_SEPARATE_REVIEW'
                    else: raise AssertionError('Unreviewed DDL event trigger accepted')
                conn.rollback()
                arguments = dict(content=content, manifest=manifest, plan=plan, operation_id='synthetic-atomic', actor='Synthetic operator')
                def blocked(call):
                    try: call()
                    except (work.Blocked, ValueError): return
                    raise AssertionError('Unsafe operation accepted')
                for stage in ('snapshot', 'ddl', 'repair', 'audit'):
                    blocked(lambda: work.apply(raw, **arguments, fail_at=stage))
                    assert state_hash(capture()) == state_hash(before), stage
                    with conn.cursor() as cur:
                        cur.execute("SELECT to_regnamespace('financial_recovery')")
                        assert cur.fetchone()[0] is None, 'Snapshot/audit survived rolled-back operation'
                    conn.rollback()
                blocked(lambda: work.apply(raw, **dict(arguments, content=content+b'wrong')))
                blocked(lambda: work.apply(raw, **dict(arguments, plan=dict(plan, state_hash='0'*64))))
                stale = dict(manifest, preconditions=[dict(h, **{'Precondition SHA-256': '0'*64}) for h in manifest['preconditions']])
                blocked(lambda: work.prepare(conn, content, stale))
                with closing(raw()) as other:
                    with other.cursor() as cur: cur.execute('SELECT pg_advisory_xact_lock(%s)', (work.LOCK_ID,))
                    blocked(lambda: work.apply(raw, **arguments))
                    other.rollback()
                class CommitFault:
                    def __init__(self, committed=False):
                        self.inner = raw()
                        self.committed = committed
                    def __getattr__(self, key): return getattr(self.inner, key)
                    def commit(self):
                        if self.committed: self.inner.commit()
                        raise ConnectionError('Synthetic uncertain commit')
                blocked(lambda: work.apply(lambda: CommitFault(False), **arguments))
                assert state_hash(capture()) == state_hash(before)
                with patch.object(work, 'event', side_effect=KeyboardInterrupt):
                    try: work.apply(raw, **arguments)
                    except KeyboardInterrupt: pass
                    else: raise AssertionError('Interrupted operation accepted')
                assert state_hash(capture()) == state_hash(before)
                assert work.apply(raw, **arguments) == {'changed': 18, 'repeated': False}
                after = capture()
                assert all(r[2:5] == ('numeric', None, None) for r in after['money']['schema'])
                assert work.apply(raw, **arguments) == {'changed': 0, 'repeated': True}
                assert state_hash(capture()) == state_hash(after), 'No-op rewrote rows'
                assert work.verify(raw, operation_id=arguments['operation_id'], content=content)['verified']
                corrected = next(r['amount'] for r in after['money']['tables']['classified_transactions'] if r['id'] == ids[4])
                assert str(corrected) == '-148351.30'
                assert decimal_sum([corrected, Decimal('148351.30')]) == Decimal('0.00')
                xlsx = db.dataframe_to_excel_bytes({'Money': pd.DataFrame({'amount': [corrected]})})
                workbook = openpyxl.load_workbook(BytesIO(xlsx), data_only=False)
                assert Decimal(str(workbook['Money']['A2'].value)) == corrected
                workbook.close()
                assert after['dependencies'] == before['dependencies']
                with patch.object(db, 'USING_POSTGRES', True), patch.object(db, 'get_connection', app_connection):
                    for imported in after['dependencies']['statement_imports']:
                        assert db.statement_already_imported(imported['statement_hash'])
                with conn.cursor() as cur:
                    saved = work.stored(cur, arguments['operation_id'])
                    assert state_hash(saved['before']) == state_hash(before)
                    assert saved['manifest'] == manifest
                conn.rollback()
                conn.set_session(readonly=False)
                for table in ('snapshots', 'events'):
                    for action in ('DELETE FROM ', 'TRUNCATE ', 'UPDATE '):
                        statement = action + 'financial_recovery.' + table
                        if action == 'UPDATE ': statement += ' SET payload=payload'
                        try:
                            with conn.cursor() as cur: cur.execute(statement)
                        except psycopg2.Error: conn.rollback()
                        else: raise AssertionError('Immutable evidence mutation accepted')
                try:
                    with conn.cursor() as cur:
                        cur.execute('SET LOCAL session_replication_role=replica')
                        cur.execute('DELETE FROM financial_recovery.snapshots')
                except psycopg2.Error: conn.rollback()
                else: raise AssertionError('Replica mode bypassed append-only guard')
                reverse_args = dict(operation_id=arguments['operation_id'], content=content, expected_after_hash=state_hash(after))
                blocked(lambda: work.reverse(raw, **dict(reverse_args, expected_after_hash='0'*64)))
                blocked(lambda: work.reverse(raw, **reverse_args, fail_at='reverse'))
                assert state_hash(capture()) == state_hash(after), 'Reverse failure did not roll back'
                assert work.reverse(raw, **reverse_args) == {'repeated': False, 'reversed': True}
                restored = capture()
                for key in ('money', 'dependencies', 'catalog', 'bits'):
                    assert work.encode(restored[key]) == work.encode(before[key]), key
                assert work.reverse(raw, **reverse_args) == {'repeated': True, 'reversed': True}
                assert state_hash(capture()) == state_hash(restored)
                blocked(lambda: work.apply(raw, **arguments))
                # A fresh synthetic scenario requires newly reviewed hashes because
                # DDL/reverse changed xmin. The original manifest is never updated.
                with conn.cursor() as cur: _, fresh_diagnostics = collect_locked(cur, content)
                conn.rollback()
                fresh = dict(manifest, preconditions=fresh_diagnostics['hashes'])
                fresh_plan = work.prepare(conn, content, fresh)
                second = dict(arguments, manifest=fresh, plan=fresh_plan, operation_id='synthetic-second')
                original_locks = work.locks
                def race_before_table_locks(cur):
                    cur.execute('SELECT pg_try_advisory_xact_lock(%s)', (work.LOCK_ID,))
                    assert cur.fetchone()[0]
                    with closing(raw()) as writer:
                        with writer.cursor() as other:
                            other.execute('UPDATE category_list SET category=category')
                        writer.commit()
                    original_locks(cur)
                with patch.object(work, 'locks', side_effect=race_before_table_locks), \
                     patch.object(work, 'targets', side_effect=AssertionError('Stale precheck reached target planning')):
                    blocked(lambda: work.apply(raw, **second))
                with conn.cursor() as cur:
                    assert work.stored(cur, 'synthetic-second') is None
                    _, fresh_diagnostics = collect_locked(cur, content)
                conn.rollback()
                fresh = dict(manifest, preconditions=fresh_diagnostics['hashes'])
                second = dict(second, manifest=fresh, plan=work.prepare(conn, content, fresh))
                blocked(lambda: work.apply(lambda: CommitFault(True), **second))
                assert work.verify(raw, operation_id='synthetic-second', content=content)['verified']
                second_after = capture()
                conn.set_session(readonly=False)
                with conn.cursor() as cur:
                    cur.execute('UPDATE classified_transactions SET category=category WHERE id=%s', (ids[0],))
                conn.commit()
                changed = capture()
                blocked(lambda: work.reverse(raw, operation_id='synthetic-second', content=content, expected_after_hash=state_hash(second_after)))
                assert state_hash(capture()) == state_hash(changed), 'New legitimate edit lost'
                print('PASS atomic PDF workflow: 18/11/24, snapshot, all nine columns, exact cents, metadata, locks, rollback, fresh verify, immutable audit, idempotency and exact IEEE reverse')
        finally:
            with admin.cursor() as cur: cur.execute('DROP DATABASE ' + name)


if __name__ == '__main__':
    main()
