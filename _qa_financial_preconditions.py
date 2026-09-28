"""Local PostgreSQL: actual PDF and deployed readiness hashes bind repairs."""
from contextlib import closing
from io import BytesIO
import os
from pathlib import Path
from unittest.mock import patch
import uuid


def main():
    import psycopg2
    import db
    import parsing
    from reportlab.pdfgen import canvas
    from _qa_safra_completion import fixture
    from _qa_safra_uat import labelled_accounts
    from _qa_safra_lifecycle import upload
    from financial_preconditions import collect_locked
    from financial_remediation import inspect_local, rehearse_existing_import, RemediationBlocked
    from financial_conversion import preservation_manifest

    options = dict(host='127.0.0.1', port=int(os.environ['ARETI_QA_PG_PORT']), user='qa_local', sslmode='disable')
    name = 'qa_financial_binding_' + uuid.uuid4().hex
    pages = fixture()
    buffer = BytesIO()
    pdf = canvas.Canvas(buffer)
    for page in pages:
        text = pdf.beginText(30, 810)
        text.setFont('Helvetica', 8)
        text.setLeading(10)
        for line in page.splitlines():
            text.textLine(line)
        pdf.drawText(text)
        pdf.showPage()
    pdf.save()
    content = buffer.getvalue()
    with closing(psycopg2.connect(dbname='postgres', **options)) as admin:
        admin.autocommit = True
        with admin.cursor() as cur:
            cur.execute('SHOW data_directory')
            assert Path(cur.fetchone()[0]).resolve() == Path(os.environ['ARETI_QA_PG_DATA']).resolve()
            cur.execute('CREATE DATABASE ' + name)
        def connect():
            return db.PostgresConnection(psycopg2.connect(dbname=name, **options))
        try:
            accounts = labelled_accounts(parsing._parse_safra_pages(pages))
            with patch.object(db, 'USING_POSTGRES', True), patch.object(db, 'get_connection', connect), patch.object(db, 'get_accounts', return_value=accounts):
                db.init_db()
                db.add_category('Synthetic', 'General', 'Synthetic')
                with closing(connect()) as rate_connection:
                    rate_connection.cursor().execute('INSERT INTO rates (rate_month,rate_type,rate_value) VALUES (?,?,?)',
                                                     ('2026-01-01', 'GBP/USD', '1.25'))
                    rate_connection.commit()
                imported = upload(db, pages, True, content)
                assert not imported.errors, imported.errors
                with closing(psycopg2.connect(dbname=name, **options)) as connection:
                    with connection.cursor() as cur:
                        cur.execute("UPDATE classified_transactions SET category='Synthetic',subcategory='General',reviewed=1")
                        cur.execute('UPDATE classified_transactions SET amount=amount+0.25 WHERE id=(SELECT min(id) FROM classified_transactions)')
                        cur.execute('UPDATE statement_balances SET closing_balance=closing_balance+0.25 WHERE id=(SELECT min(id) FROM statement_balances)')
                    connection.commit()
                    before, digest = inspect_local(connection)
                    with connection.cursor() as cur:
                        comparison, diagnosis = collect_locked(cur, content)
                    connection.rollback()
                    hashes = diagnosis['hashes']
                    sources = {(t, r['id']): r for t, rows in before['tables'].items() for r in rows}
                    repairs = [dict(table=r['Table'], id=r['Record ID'], field=r['Field'],
                                    old=r['Current database value'], new=r['Proposed PDF/parser value'],
                                    statement_hash=sources[r['Table'], r['Record ID']]['statement_hash'])
                               for r in diagnosis['fields'] if r['Disposition'] == 'PDF DIFFERENCE - DIAGNOSTIC ONLY']
                    assert repairs and len(comparison['sections']) == 6 and len(comparison['transactions']) == 9
                    arguments = dict(content=content, exported_hashes=hashes, expected_hash=digest,
                                     conversions=preservation_manifest(before), repairs=repairs,
                                     actor='Synthetic tester', operation_id='synthetic-bound-operation', backup_verified=True)
                    from financial_preconditions import validate_locked
                    mutations = [
                        "UPDATE classified_transactions SET category='Other' WHERE id=(SELECT min(id) FROM classified_transactions)",
                        "UPDATE classified_transactions SET subcategory='Other' WHERE id=(SELECT min(id) FROM classified_transactions)",
                        'UPDATE classified_transactions SET reviewed=0 WHERE id=(SELECT min(id) FROM classified_transactions)',
                        'UPDATE classified_transactions SET amount_usd=amount_usd+1 WHERE id=(SELECT min(id) FROM classified_transactions)',
                        'UPDATE classified_transactions SET fx_rate=2 WHERE id=(SELECT min(id) FROM classified_transactions)',
                        "UPDATE classified_transactions SET split_group_id='synthetic-group' WHERE id=(SELECT min(id) FROM classified_transactions)",
                        'UPDATE statement_imports SET duplicate_attempts=duplicate_attempts+1',
                        "UPDATE category_list SET report_group='Other'",
                        "UPDATE statement_balances SET notes='{}' WHERE id=(SELECT min(id) FROM statement_balances)",
                        "INSERT INTO transaction_change_log (transaction_id,field_name,old_value,new_value,source,changed_at) SELECT min(id),'category','Synthetic','Other','Synthetic','2026-01-01' FROM classified_transactions",
                    ]
                    for mutation in mutations:
                        connection.set_session(readonly=False)
                        try:
                            with connection.cursor() as cur:
                                cur.execute(mutation)
                                validate_locked(cur, content, hashes, repairs)
                        except ValueError:
                            pass
                        else:
                            raise AssertionError('Changed dependency accepted')
                        finally:
                            connection.rollback()
                    for changed in ([dict(h, **{'Precondition SHA-256': '0' * 64}) for h in hashes], hashes[:-1]):
                        try:
                            rehearse_existing_import(connection, **dict(arguments, exported_hashes=changed))
                        except (RemediationBlocked, ValueError):
                            assert inspect_local(connection)[1] == digest
                        else:
                            raise AssertionError('Stale or incomplete exported hashes accepted')
                    # Same values with a newer xmin must also block the old export.
                    connection.set_session(readonly=False)
                    with connection.cursor() as cur:
                        cur.execute('UPDATE category_list SET category=category')
                    connection.commit()
                    try:
                        rehearse_existing_import(connection, **arguments)
                    except RemediationBlocked:
                        assert inspect_local(connection)[1] == digest
                    else:
                        raise AssertionError('Changed taxonomy version accepted')
                    with connection.cursor() as cur:
                        _, diagnosis = collect_locked(cur, content)
                    connection.rollback()
                    arguments['exported_hashes'] = diagnosis['hashes']
                    try:
                        rehearse_existing_import(connection, **dict(arguments, fail_after=2))
                    except RemediationBlocked:
                        assert inspect_local(connection)[1] == digest
                    else:
                        raise AssertionError('Injected failure did not abort')
                    result = rehearse_existing_import(connection, **arguments)
                    assert result['changed'] == len(repairs)
                    assert rehearse_existing_import(connection, **arguments) == {'changed': 0, 'repeated': True}
                    with connection.cursor() as cur:
                        comparison, _ = collect_locked(cur, content)
                    connection.rollback()
                    assert all(r['Status'] == 'MATCH' for r in comparison['transactions'] + comparison['sections'])
                    assert all(r['Reviewed'] == 1 for r in comparison['transactions'])
                    print('PASS actual synthetic PDF, six accounts, exported scope hashes, taxonomy xmin, rollback, exact reconciliation, Reviewed and repeat')
        finally:
            with admin.cursor() as cur:
                cur.execute('DROP DATABASE ' + name)


if __name__ == '__main__':
    main()
