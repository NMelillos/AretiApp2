"""Synthetic authorization, credential-redaction and SELECT-only diagnostic QA."""
from contextlib import ExitStack
import os
from unittest.mock import patch


def without_database_identity(name, source):
    if name != 'app.py':
        return source
    block = b'from database_identity import render_database_identity\nif render_database_identity():\n    st.stop()\n\n'
    source = source.replace(b'\r\n', b'\n')
    if block in source:
        assert source.count(block) == 1
        anchor = b'else:\n    require_login()\n\n' + block
        assert source.count(anchor) == 1, 'Identity screen must follow the main login gate'
        source = source.replace(block, b'', 1)
    return source


def main():
    import subprocess
    from pathlib import Path
    baseline = subprocess.check_output(['git','show','48a6efd9cdc4b58972e87887a7f7af7d613aeca6:app.py']).replace(b'\r\n',b'\n')
    assert without_database_identity('app.py',Path('app.py').read_bytes()) == baseline
    import database_identity as identity
    import existing_import_compare as authz
    import db
    secret = 'SYNTHETIC_PASSWORD_MARKER'
    username = 'SYNTHETIC_LOGIN_MARKER'
    dsn = f'postgresql://{username}:{secret}@db.synthetic.supabase.co:5432/synthetic_db?application_name=SYNTHETIC_QUERY_MARKER'
    queries = []

    class Cursor:
        def execute(self, query):
            assert query == identity.IDENTITY_QUERY
            assert query.startswith('SELECT ') and ';' not in query
            queries.append(query)
        def fetchone(self): return ('17.2', 'synthetic_db', '127.0.0.1', 5432, username, username)
        def close(self): pass
    class Connection:
        def cursor(self): return Cursor()
        def rollback(self): pass
        def close(self): pass
        def commit(self): raise AssertionError('Commit forbidden')
        def set_session(self, **kwargs): raise AssertionError('Connection changes forbidden')

    with ExitStack() as stack:
        stack.enter_context(patch.dict(os.environ, {'DATABASE_URL':dsn,'POSTGRES_URL':'ignored'}, clear=True))
        stack.enter_context(patch.object(db,'DATABASE_URL',dsn))
        stack.enter_context(patch.object(db,'USING_POSTGRES',True))
        get = stack.enter_context(patch.object(db,'get_connection',return_value=Connection()))
        ctx = stack.enter_context(patch.object(authz,'get_script_run_ctx',return_value=object()))
        state = stack.enter_context(patch.object(authz.st,'session_state',{'authenticated':True,'login_user':'Areti'}))
        result = identity.read_identity()
        assert result['Active variable']=='DATABASE_URL'
        assert result['Hostname']=='db.synthetic.supabase.co'
        assert result['Provider hostname hint']=='Supabase (hostname hint only)'
        assert result['Resource/reference from hostname']=='synthetic'
        assert not any(s in repr(result) for s in (secret,username,'SYNTHETIC_QUERY_MARKER',dsn))
        for bad in ({}, {'authenticated':True,'login_user':'Other'}, {'authenticated':True,'login_user':'areti'},
                    {'authenticated':True,'login_user':'Areti','third_report_authenticated':True},
                    {'authenticated':False,'login_user':'Areti'}, {'login_user':'Areti','authenticated':'true'}):
            with patch.object(authz.st,'session_state',bad):
                before=get.call_count
                try: identity.read_identity()
                except identity.IdentityBlocked: pass
                else: raise AssertionError('Unauthorized access accepted')
                assert get.call_count==before
        ctx.return_value=None
        try: identity.read_identity()
        except identity.IdentityBlocked: pass
        else: raise AssertionError('Non-Streamlit direct call accepted')
        ctx.return_value=object()
        with patch.dict(os.environ,{'DATABASE_URL':'','POSTGRES_URL':dsn}):
            assert identity.read_identity()['Active variable']=='POSTGRES_URL'
        with patch.object(db,'DATABASE_URL','different'):
            before=get.call_count
            try: identity.read_identity()
            except identity.IdentityBlocked: pass
            else: raise AssertionError('Changed runtime configuration accepted')
            assert get.call_count==before
        get.side_effect=RuntimeError(secret+dsn)
        try: identity.read_identity()
        except identity.IdentityBlocked as error:
            assert secret not in str(error) and dsn not in str(error)
        else: raise AssertionError('Connection error not blocked')

    for host, provider in [('db.fake.supabase.co','Supabase'),('aws-0-test.pooler.supabase.com','Supabase'),
                           ('ep-test-pooler.us-east-2.aws.neon.tech','Neon'),('dpg-test-a.oregon-postgres.render.com','Render'),
                           ('dpg-test-a','Render'),('sample.proxy.rlwy.net','Railway'),('db.railway.internal','Railway'),
                           ('db.supabase.co.attacker.invalid','Unknown'),('127.0.0.1','Unknown')]:
        assert identity.provider_hint(host)[0].startswith(provider), host
    assert identity.provider_hint('aws-0-test.pooler.supabase.com')[1]=='Not available from hostname'
    for bad in ('bad/host','host@secret','host\nsecret','host,other'):
        try: identity.safe_hostname(bad)
        except identity.IdentityBlocked: pass
        else: raise AssertionError('Unsafe hostname accepted')

    # Real application entry: diagnostic intercepts before init_db/backfills.
    from streamlit.testing.v1 import AppTest
    import auth
    def login():
        import streamlit as st
        st.session_state.authenticated=True
        st.session_state.login_user='Areti'
    with patch.object(auth,'require_login',side_effect=login), patch.object(db,'init_db',side_effect=AssertionError('Startup write path reached')), \
         patch.object(db,'get_connection',return_value=Connection()), patch.object(db,'DATABASE_URL',dsn), patch.object(db,'USING_POSTGRES',True), \
         patch.dict(os.environ,{'DATABASE_URL':dsn}):
        app=AppTest.from_file('app.py')
        app.query_params['page']='DatabaseIdentity'
        app.run(timeout=30)
        assert not app.exception
        assert not app.table
        app.button[0].click().run()
        assert not app.exception and len(app.table)==1
        assert secret not in str(app.table[0].value) and username not in str(app.table[0].value)
        app.run()
        assert not app.exception
    for user, third in [('Other',False),('Areti',True)]:
        def other_login():
            import streamlit as st
            st.session_state.authenticated=True
            st.session_state.login_user=user
            st.session_state.third_report_authenticated=third
        with patch.object(auth,'require_login',side_effect=other_login), patch.object(db,'get_connection',side_effect=AssertionError('Unauthorized connection')):
            app=AppTest.from_file('app.py')
            app.query_params.update(page='DatabaseIdentity',username='Areti',authenticated='true')
            app.run(timeout=30)
            assert not app.exception and not app.button and not app.table and len(app.error)==1
    with patch.object(db,'get_connection',side_effect=AssertionError('Anonymous connection')):
        app=AppTest.from_file('app.py')
        app.query_params['page']='DatabaseIdentity'
        app.run(timeout=30)
        assert not app.exception and not app.table
        assert not any(b.label=='Read database identity' for b in app.button)

    # Actual isolated PostgreSQL; only the operator-owned local server is allowed.
    import psycopg2
    from contextlib import closing
    from pathlib import Path
    options=dict(host='127.0.0.1',port=int(os.environ['ARETI_QA_PG_PORT']),user='qa_local',dbname='postgres',sslmode='disable')
    with closing(psycopg2.connect(**options)) as local:
        with local.cursor() as cur:
            cur.execute('SHOW data_directory')
            assert Path(cur.fetchone()[0]).resolve()==Path(os.environ['ARETI_QA_PG_DATA']).resolve()
    local_dsn=f"postgresql://qa_local@127.0.0.1:{options['port']}/postgres"
    def local_connection():
        return db.PostgresConnection(psycopg2.connect(**options,options='-c default_transaction_read_only=on'))
    with patch.object(authz,'get_script_run_ctx',return_value=object()), patch.object(authz.st,'session_state',{'authenticated':True,'login_user':'Areti'}), \
         patch.object(db,'USING_POSTGRES',True), patch.object(db,'DATABASE_URL',local_dsn), patch.object(db,'get_connection',side_effect=local_connection), \
         patch.dict(os.environ,{'DATABASE_URL':local_dsn}):
        actual=identity.read_identity()
        assert actual['current_database()']=='postgres' and actual['inet_server_port()']==options['port']
        assert 'qa_local' not in repr(actual)
    print('PASS identity authorization, strict output allowlist, safe errors, provider hints, SELECT-only access and real app headless startup isolation')


if __name__=='__main__': main()
