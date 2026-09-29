"""Synthetic reference extraction, confidential-output and actual entry-point QA."""
from contextlib import ExitStack, redirect_stdout, redirect_stderr
from io import StringIO
import os
from unittest.mock import patch


def main():
    import database_identity as identity
    import existing_import_compare as authz
    import db
    from psycopg2.extensions import make_dsn
    ref = 'abcdefghijklmnopqrst'
    other = 'tsrqponmlkjihgfedcba'
    missing = 'PROJECT REFERENCE NOT DETERMINED'
    params = dict(host='aws-0-eu-west-1.pooler.supabase.com', port='5432',
                  dbname='postgres', user='postgres.' + ref,
                  password='SYNTHETIC_PASSWORD_MARKER', application_name='SYNTHETIC_QUERY_MARKER')
    dsn = make_dsn(**params)
    queries, events = [], []
    roles = ['postgres', 'postgres']
    live = dict(params)
    live.pop('password')

    class Cursor:
        def execute(self, query):
            assert query == identity.IDENTITY_QUERY and query.startswith('SELECT ') and ';' not in query
            queries.append(query)
        def fetchone(self):
            fields = ('17.6', 'postgres', '127.0.0.1', 5432)
            return fields + tuple(roles) if 'current_user' in identity.IDENTITY_QUERY else fields
        def close(self): events.append('cursor_close')
    class Connection:
        def get_dsn_parameters(self): return dict(live)
        def cursor(self): return Cursor()
        def rollback(self): events.append('rollback')
        def close(self): events.append('close')
        def commit(self): raise AssertionError('Commit forbidden')
        def set_session(self, **kwargs): raise AssertionError('Connection changes forbidden')

    def assert_private(result):
        text = repr(result)
        for value in (dsn, params['user'], params['password'], params['application_name'],
                      'DATABASE_URL=', 'POSTGRES_URL='):
            assert value not in text, 'Confidential field escaped output allowlist'

    with ExitStack() as stack:
        stack.enter_context(patch.dict(os.environ, {'DATABASE_URL': dsn}, clear=True))
        stack.enter_context(patch.object(db, 'DATABASE_URL', dsn))
        stack.enter_context(patch.object(db, 'USING_POSTGRES', True))
        get = stack.enter_context(patch.object(db, 'get_connection', return_value=Connection()))
        stack.enter_context(patch.object(authz, 'get_script_run_ctx', return_value=object()))
        stack.enter_context(patch.object(authz.st, 'session_state', {'authenticated': True, 'login_user': 'Areti'}))
        output, error = StringIO(), StringIO()
        with redirect_stdout(output), redirect_stderr(error):
            result = identity.read_identity()
        assert result.get('Supabase project reference') == ref, 'Missing safe live pooler project reference'
        assert_private(result)
        assert output.getvalue() == error.getvalue() == ''
        assert events[-3:] == ['cursor_close', 'rollback', 'close']
        with patch.object(Connection, 'get_dsn_parameters', side_effect=RuntimeError('SYNTHETIC_SECRET')):
            assert identity.read_identity()['Supabase project reference'] == missing
        for pair in (['postgres.' + ref] * 2, ['postgres', 'postgres.' + ref],
                     ['postgres.' + ref, 'postgres']):
            roles[:] = pair
            assert identity.read_identity()['Supabase project reference'] == ref
        for pair in (['postgres.' + other, 'postgres'], ['postgres', 'SYNTHETIC_ROLE_SECRET'],
                     ['postgres.' + ref + '.extra', 'postgres'], [None, 'postgres']):
            roles[:] = pair
            result = identity.read_identity()
            assert result['Supabase project reference'] == missing
            assert_private(result)
            assert 'SYNTHETIC_ROLE_SECRET' not in repr(result)
        roles[:] = ['postgres', 'postgres']
        for key, value in [('user', 'postgres.' + other), ('host', 'elsewhere.invalid'), ('host', None),
                           ('port', '6543'), ('dbname', 'other')]:
            with patch.dict(live, {key: value}):
                assert identity.read_identity()['Supabase project reference'] == missing
        for user in ('postgres', 'postgres.' + ref + '.extra', 'postgres.' + ref + ':secret',
                     'postgres.' + ref.upper(), 'prefix.postgres.' + ref, 'postgres.' + ref + '\n'):
            modified = dict(params, user=user)
            alternate = make_dsn(**modified)
            with patch.dict(os.environ, {'DATABASE_URL': alternate}), patch.object(db, 'DATABASE_URL', alternate), \
                 patch.dict(live, user=user):
                assert identity.read_identity()['Supabase project reference'] == missing
        for changes in ({'host': 'aws-0-eu-west-1.pooler.supabase.com.attacker.invalid'},
                        {'port': '6543'}, {'hostaddr': '127.0.0.1'}, {'options': '-c role=postgres'}):
            alternate = make_dsn(**dict(params, **changes))
            with patch.dict(os.environ, {'DATABASE_URL': alternate}), patch.object(db, 'DATABASE_URL', alternate):
                assert identity.read_identity()['Supabase project reference'] == missing
        for state in ({}, {'authenticated': True, 'login_user': 'Other'},
                      {'authenticated': True, 'login_user': 'areti'},
                      {'authenticated': True, 'login_user': 'Areti', 'third_report_authenticated': True}):
            with patch.object(authz.st, 'session_state', state):
                before = get.call_count
                try: identity.read_identity()
                except identity.IdentityBlocked: pass
                else: raise AssertionError('Unauthorized read accepted')
                assert get.call_count == before

    from streamlit.testing.v1 import AppTest
    import auth
    def login():
        import streamlit as st
        st.session_state.authenticated = True
        st.session_state.login_user = 'Areti'
    with patch.object(auth, 'require_login', side_effect=login), \
         patch.object(db, 'init_db', side_effect=AssertionError('Startup mutation reached')), \
         patch.object(db, 'get_connection', return_value=Connection()), \
         patch.object(db, 'DATABASE_URL', dsn), patch.object(db, 'USING_POSTGRES', True), \
         patch.dict(os.environ, {'DATABASE_URL': dsn}):
        app = AppTest.from_file('app.py')
        app.query_params['page'] = 'DatabaseIdentity'
        before = len(queries)
        app.run(timeout=30)
        assert not app.exception and not app.table and len(queries) == before
        app.button[0].click().run()
        assert not app.exception and len(app.table) == 1
        rows = app.table[0].value
        assert rows.loc[rows['Field'] == 'Supabase project reference', 'Value'].tolist() == [ref]
        assert_private(rows)
        before = len(queries)
        app.run()
        assert not app.exception and not app.table and len(queries) == before
        with patch.dict(live, user='postgres.' + other):
            app.button[0].click().run()
            assert not app.exception and missing in str(app.table[0].value)
            assert other not in str(app.table[0].value)
    assert queries and all(q == identity.IDENTITY_QUERY for q in queries)
    assert events.count('rollback') == events.count('close') == len(queries)
    print('PASS project identity: deterministic extraction, live/session consistency, fail-closed ambiguity, no secrets, SELECT-only, headless app')


if __name__ == '__main__': main()
