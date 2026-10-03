"""Dedicated fresh READ ONLY PostgreSQL connection; fixed SELECT/SHOW keys only."""
from contextlib import contextmanager
import os
from types import MappingProxyType

FIELDS = (('classified_transactions', 'amount'), ('classified_transactions', 'amount_usd'),
          ('classified_transactions', 'fx_rate'), ('classified_transactions', 'split_original_amount'),
          ('rates', 'rate_value'), ('statement_balances', 'opening_balance'),
          ('statement_balances', 'money_out'), ('statement_balances', 'money_in'),
          ('statement_balances', 'closing_balance'))
QUERIES = MappingProxyType({
    'read_only': ('SHOW transaction_read_only', None, 1),
    'timeouts': ("SELECT pg_catalog.current_setting('statement_timeout'), pg_catalog.current_setting('lock_timeout') LIMIT 1", None, 1),
    'version': ("SELECT pg_catalog.current_setting('server_version') LIMIT 1", None, 1),
    'engine': ('SELECT pg_catalog.version() LIMIT 1', None, 1),
    'ping': ('SELECT 1 LIMIT 1', None, 1),
    'role': ("""SELECT NOT (r.rolsuper OR r.rolcreaterole OR r.rolcreatedb OR r.rolreplication OR r.rolbypassrls),
        NOT EXISTS (SELECT 1 FROM pg_catalog.pg_auth_members m WHERE m.member=r.oid),
        NOT pg_catalog.has_database_privilege(pg_catalog.current_database(),'CREATE'),
        NOT EXISTS (SELECT 1 FROM pg_catalog.pg_namespace n WHERE n.nspname !~ '^pg_'
            AND n.nspname<>'information_schema' AND pg_catalog.has_schema_privilege(n.oid,'CREATE')),
        NOT EXISTS (SELECT 1 FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
            WHERE n.nspname !~ '^pg_' AND n.nspname<>'information_schema'
            AND c.relkind IN ('r','p','v','m','f') AND
            (pg_catalog.has_table_privilege(c.oid,'INSERT,UPDATE,DELETE,TRUNCATE,TRIGGER,REFERENCES')
             OR pg_catalog.has_any_column_privilege(c.oid,'INSERT,UPDATE,REFERENCES')))
        FROM pg_catalog.pg_roles r WHERE r.rolname=CURRENT_USER LIMIT 1""", None, 1),
    'schema': ("""SELECT table_name,column_name,data_type,udt_name,numeric_precision,numeric_scale,domain_name
        FROM information_schema.columns WHERE table_schema=%s AND
        (table_name,column_name) IN ((%s,%s),(%s,%s),(%s,%s),(%s,%s),(%s,%s),(%s,%s),(%s,%s),(%s,%s),(%s,%s))
        ORDER BY table_name,column_name LIMIT 10""", ('public',) + tuple(v for pair in FIELDS for v in pair), 10),
    'fence_contract': ("""SELECT c.relkind,c.relrowsecurity,c.relforcerowsecurity
        FROM pg_catalog.pg_class c JOIN pg_catalog.pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname=%s AND c.relname=%s LIMIT 1""", ('financial_control', 'fence'), 1),
    'fence': ('SELECT state,revision,repair_id,phase,recorded_at FROM financial_control.fence ORDER BY revision DESC LIMIT 1', None, 1),
})


class ReadOnlyUnavailable(ValueError):
    def __init__(self): super().__init__('Verified read-only PostgreSQL connection unavailable.')


class Reader:
    __slots__ = ('__cursor',)
    def __init__(self, cursor): self.__cursor = cursor
    def read(self, key):
        if not isinstance(key, str) or key not in QUERIES: raise ReadOnlyUnavailable()
        sql, params, limit = QUERIES[key]
        self.__cursor.execute(sql, params)
        rows = self.__cursor.fetchmany(limit + 1)
        if len(rows) > limit: raise ReadOnlyUnavailable()
        return rows


@contextmanager
def connection():
    from ops_auth import require_owner
    require_owner()
    dsn = os.getenv('OPS_DATABASE_URL', '')
    if not dsn: raise ReadOnlyUnavailable()
    conn = None
    try:
        import psycopg2
        from psycopg2.extensions import parse_dsn
        settings = parse_dsn(dsn)
        if not settings.get('host') or not settings.get('dbname') or not settings.get('user'):
            raise ReadOnlyUnavailable()
        if any(k in settings for k in ('options', 'service', 'hostaddr')): raise ReadOnlyUnavailable()
        sslmode = settings.get('sslmode', 'require')
        if sslmode not in ('require', 'verify-ca', 'verify-full'): raise ReadOnlyUnavailable()
        conn = psycopg2.connect(dsn, sslmode=sslmode, connect_timeout=5,
            application_name='areti-ops-read-only',
            options='-c default_transaction_read_only=on -c statement_timeout=15000 -c lock_timeout=3000 -c search_path=pg_catalog')
        if not conn.info.ssl_in_use or conn.server_version < 120000: raise ReadOnlyUnavailable()
        conn.set_session(readonly=True, autocommit=False, isolation_level='REPEATABLE READ')
        with conn.cursor() as cursor:
            reader = Reader(cursor)
            if reader.read('read_only') != [('on',)]: raise ReadOnlyUnavailable()
            if reader.read('timeouts') != [('15s', '3s')]: raise ReadOnlyUnavailable()
            engine = reader.read('engine')
            if len(engine) != 1 or not isinstance(engine[0][0], str) or not engine[0][0].startswith('PostgreSQL '):
                raise ReadOnlyUnavailable()
            if reader.read('role') != [(True, True, True, True, True)]: raise ReadOnlyUnavailable()
            yield reader
    except Exception:
        raise ReadOnlyUnavailable() from None
    finally:
        if conn is not None:
            try: conn.rollback()
            except Exception: pass
            finally:
                try: conn.close()
                except Exception: pass
