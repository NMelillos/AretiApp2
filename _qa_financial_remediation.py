"""Isolated PostgreSQL migration/repair rehearsal. Synthetic values only."""
from contextlib import closing
from decimal import Decimal
import os
from pathlib import Path
import uuid


def main():
    import psycopg2
    from financial_remediation import inspect_local, rehearse, RemediationBlocked
    from financial_schema import FINANCIAL_COLUMNS
    from repair_readiness import state_hash
    options = dict(host='127.0.0.1', port=int(os.environ['ARETI_QA_PG_PORT']),
                   user='qa_local', sslmode='disable')
    name = 'qa_financial_' + uuid.uuid4().hex
    with closing(psycopg2.connect(dbname='postgres', **options)) as admin:
        admin.autocommit = True
        with admin.cursor() as cur:
            cur.execute('SHOW data_directory')
            assert Path(cur.fetchone()[0]).resolve() == Path(os.environ['ARETI_QA_PG_DATA']).resolve()
            cur.execute('CREATE DATABASE ' + name)
        try:
            with closing(psycopg2.connect(dbname=name, **options)) as connection:
                with connection.cursor() as cur:
                    for table, fields in FINANCIAL_COLUMNS.items():
                        columns = ','.join(f'{field} REAL' + (' NOT NULL DEFAULT 1' if table == 'rates' else '') for field in fields)
                        cur.execute(f'''CREATE TABLE {table} (id INTEGER PRIMARY KEY,
                            statement_hash TEXT, category TEXT, reviewed INTEGER,
                            created_at TEXT, {columns})''')
                        cur.execute(f'INSERT INTO {table} (id,statement_hash,category,reviewed,created_at) VALUES (1,%s,%s,1,%s)',
                                    ('synthetic-fingerprint', 'Synthetic category', 'synthetic-timestamp'))
                    cur.execute('UPDATE classified_transactions SET amount=%s, fx_rate=%s, amount_usd=%s',
                                ('-712345.30', '1.234567890123456789', '-100.25'))
                    cur.execute('UPDATE rates SET rate_value=%s', ('1.234567890123456789',))
                connection.commit()
                before, digest = inspect_local(connection)
                from financial_conversion import preservation_manifest
                conversions = preservation_manifest(before)
                assert {c['policy'] for c in conversions} == {'NULL_PRESERVE', 'LEGACY_BINARY_UNVERIFIED'}
                old = before['tables']['classified_transactions'][0]['amount']
                repair = dict(table='classified_transactions', id=1, field='amount',
                              old=str(old), new='-712345.30', statement_hash='synthetic-fingerprint')
                arguments = dict(expected_hash=digest, conversions=conversions, repairs=[repair],
                                 operation_id='synthetic-operation', actor='Synthetic local tester', backup_verified=True)
                connection.set_session(readonly=False)
                with connection.cursor() as cur:
                    cur.execute('''CREATE FUNCTION synthetic_trigger() RETURNS trigger
                        LANGUAGE plpgsql AS $$ BEGIN RETURN NEW; END $$''')
                    cur.execute('''CREATE TRIGGER synthetic_trigger BEFORE UPDATE ON classified_transactions
                        FOR EACH ROW EXECUTE FUNCTION synthetic_trigger()''')
                connection.commit()
                try:
                    rehearse(connection, **arguments)
                except RemediationBlocked:
                    assert inspect_local(connection)[1] == digest
                else:
                    raise AssertionError('Unreviewed trigger accepted')
                connection.set_session(readonly=False)
                with connection.cursor() as cur:
                    cur.execute('DROP TRIGGER synthetic_trigger ON classified_transactions')
                    cur.execute('DROP FUNCTION synthetic_trigger()')
                connection.commit()
                with closing(psycopg2.connect(dbname=name, **options)) as other:
                    with other.cursor() as cur:
                        from financial_remediation import LOCK_ID
                        cur.execute('SELECT pg_advisory_xact_lock(%s)', (LOCK_ID,))
                    try:
                        rehearse(connection, **arguments)
                    except RemediationBlocked:
                        assert inspect_local(connection)[1] == digest
                    else:
                        raise AssertionError('Concurrent migration accepted')
                    other.rollback()
                    with other.cursor() as cur:
                        cur.execute("UPDATE classified_transactions SET category='New synthetic edit' WHERE id=1")
                    other.commit()
                    try:
                        rehearse(connection, **arguments)
                    except RemediationBlocked:
                        pass
                    else:
                        raise AssertionError('Intervening user metadata change accepted')
                    with other.cursor() as cur:
                        cur.execute("UPDATE classified_transactions SET category='Synthetic category' WHERE id=1")
                    other.commit()
                # Full snapshot is the local rollback fixture. Production backup
                # confirmation cannot be supplied through this local-only API.
                for override in ({'expected_hash': 'stale'}, {'conversions': conversions[:-1]},
                                 {'conversions': [dict(c, policy='GUESS_CENTS') for c in conversions]},
                                 {'conversions': [dict(c, new='0') if c['old'] is not None else c for c in conversions]},
                                 {'backup_verified': False}, {'fail_after': 2},
                                 {'repairs': [dict(repair, field='fx_rate')]},
                                 {'repairs': [dict(repair, old='0')]},
                                 {'repairs': [dict(repair, statement_hash='other')]}):
                    try:
                        rehearse(connection, **dict(arguments, **override))
                    except RemediationBlocked:
                        assert inspect_local(connection)[1] == digest, 'Partial changes survived failure'
                    else:
                        raise AssertionError('Unsafe rehearsal accepted')
                assert rehearse(connection, **arguments) == {'changed': 1, 'repeated': False}
                after, after_hash = inspect_local(connection)
                assert after['tables']['classified_transactions'][0]['amount'] == Decimal('-712345.30')
                assert str(after['tables']['classified_transactions'][0]['amount']) == '-712345.30'
                for table, rows in after['tables'].items():
                    for row, prior in zip(rows, before['tables'][table]):
                        for field, value in row.items():
                            if (table, field) != ('classified_transactions', 'amount'):
                                assert value == prior[field], (table, field)
                assert all(row[2:5] == ('numeric', None, None) for row in after['schema'])
                def versions():
                    with connection.cursor() as cur:
                        result = []
                        for table in FINANCIAL_COLUMNS:
                            cur.execute('SELECT id, xmin::text FROM ' + table + ' ORDER BY id')
                            result.append(cur.fetchall())
                    connection.rollback()
                    return result
                prior_versions = versions()
                assert rehearse(connection, **arguments) == {'changed': 0, 'repeated': True}
                assert versions() == prior_versions, 'Repeated operation rewrote rows'
                assert rehearse(connection, expected_hash=after_hash,
                                conversions=preservation_manifest(after), repairs=[],
                                operation_id='new-no-op-operation', actor='Synthetic local tester',
                                backup_verified=True) == {'changed': 0, 'repeated': True}
                assert versions() == prior_versions, 'Already numeric migration rewrote rows'
                connection.set_session(readonly=False)
                for statement in ('DELETE FROM financial_rehearsal_audit',
                                  'TRUNCATE financial_rehearsal_audit',
                                  'UPDATE financial_rehearsal_audit SET actor=actor'):
                    try:
                        with connection.cursor() as cur:
                            cur.execute(statement)
                    except psycopg2.Error:
                        connection.rollback()
                    else:
                        raise AssertionError('Audit mutation accepted')
                assert inspect_local(connection)[1] == after_hash
                with connection.cursor() as cur:
                    cur.execute('SELECT count(*) FROM financial_rehearsal_audit')
                    assert cur.fetchone()[0] == 1
                connection.rollback()
                print('PASS local migration/repair: full manifest, old/hash checks, rollback, idempotency, exact read-back, metadata, append-only audit')
        finally:
            with admin.cursor() as cur:
                cur.execute('DROP DATABASE ' + name)


if __name__ == '__main__':
    main()
