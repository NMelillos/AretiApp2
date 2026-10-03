"""A-X owner/read-only/query-boundary QA and isolated PostgreSQL integration."""
import ast
from contextlib import contextmanager, closing
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import Mock, patch
import uuid

HOOK = (b'    if st.query_params.get("ops") == "1":\n'
        b'        from ops_console import render_ops_console\n'
        b'        render_ops_console()\n'
        b'        st.stop()\n')


def without_ops_hook(source):
    source = source.replace(b'\r\n', b'\n')
    if HOOK in source:
        assert source.count(HOOK) == 1
        assert source.count(b'else:\n    require_login()\n' + HOOK) == 1
        assert source.index(HOOK) < source.index(b'import pandas as pd')
        source = source.replace(HOOK, b'', 1)
    return source


def without_ops_auth_cleanup(source):
    source = source.replace(b'\r\n', b'\n')
    hook = (b'    for key in ("_ops_owner_proof", "_ops_result", "_ops_owner_password", "_ops_owner_attempts"):\n'
            b'        st.session_state.pop(key, None)\n')
    if hook in source:
        assert source.count(hook) == 1
        anchor = (b'def sign_out():\n'
                  b'    for key in ["authenticated", "login_user", "login_error", "login_username", "login_password"]:\n'
                  b'        st.session_state.pop(key, None)\n')
        assert source.count(anchor + hook) == 1
        source = source.replace(hook,b'',1)
    return source


def owner_config():
    salt = '12' * 16
    digest = hashlib.pbkdf2_hmac('sha256', b'synthetic-owner-only', bytes.fromhex(salt), 600000).hex()
    return {'OPS_OWNER_USERNAME': 'George', 'OPS_OWNER_PASSWORD_HASH': 'pbkdf2_sha256$600000$' + salt + '$' + digest}


def cold_route():
    import streamlit as st
    import auth
    import ops_auth
    class Stopped(BaseException): pass
    state = dict(authenticated=True, login_user='George')
    with patch.dict(os.environ, owner_config()):
        state[ops_auth.PROOF] = ops_auth.proof_value()
        with patch.object(st, 'session_state', state), patch.object(st, 'query_params', {'ops': '1'}), \
             patch.object(st, 'stop', side_effect=Stopped), patch.object(st, 'set_page_config'), \
             patch.object(st, 'title') as title, patch.object(st, 'caption'), patch.object(st, 'info'), \
             patch.object(st, 'form', return_value=Mock(__enter__=Mock(), __exit__=Mock(return_value=False))), \
             patch.object(st, 'text_input', return_value=''), patch.object(st, 'form_submit_button', return_value=False), \
             patch.object(st, 'subheader'), patch.object(st, 'columns', side_effect=lambda n: [Mock(button=Mock(return_value=False)) for _ in range(n)]):
            try: exec(compile(Path('app.py').read_text(encoding='utf-8'), 'app.py', 'exec'), {})
            except Stopped: pass
            else: raise AssertionError('Ops did not stop the application')
            title.assert_called_once_with('ARETI OPS CONSOLE')
    assert not {'pandas', 'db', 'financial_atomic', 'financial_atomic_command', 'nomad_controlled_repair', 'parsing'}.intersection(sys.modules)
    assert 'psycopg2' not in sys.modules, 'Opening console connected to database'
    print('PASS cold Ops route: no business/repair/database imports or page-load diagnostics')


def blocked(call, error):
    try: call()
    except error: return
    raise AssertionError('Unsafe operation accepted')


def postgres_check(db):
    import psycopg2
    data = os.environ['ARETI_QA_PG_DATA']
    opts = dict(host='127.0.0.1', port=int(os.environ['ARETI_QA_PG_PORT']), user='qa_local', sslmode='disable')
    name = 'qa_ops_' + uuid.uuid4().hex
    role = name + '_reader'
    with closing(psycopg2.connect(dbname='postgres', **opts)) as admin:
        admin.autocommit = True
        with admin.cursor() as cur:
            cur.execute('SHOW data_directory')
            assert Path(cur.fetchone()[0]).resolve() == Path(data).resolve()
            cur.execute('CREATE DATABASE ' + name)
            cur.execute('CREATE ROLE ' + role + ' LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS NOINHERIT')
        try:
            with closing(psycopg2.connect(dbname=name, **opts)) as setup:
                with setup.cursor() as cur:
                    cur.execute('REVOKE CREATE ON SCHEMA public FROM PUBLIC')
                    cur.execute('CREATE TABLE classified_transactions(amount numeric,amount_usd numeric,fx_rate numeric,split_original_amount numeric)')
                    cur.execute('CREATE TABLE rates(rate_value numeric)')
                    cur.execute('CREATE TABLE statement_balances(opening_balance numeric,money_out numeric,money_in numeric,closing_balance numeric)')
                    cur.execute('CREATE SCHEMA financial_control')
                    cur.execute('CREATE TABLE financial_control.fence(revision bigint PRIMARY KEY,state text,repair_id text,phase text,recorded_at timestamptz)')
                    cur.execute('INSERT INTO financial_control.fence VALUES (1,%s,%s,%s,CURRENT_TIMESTAMP)',
                                ('NORMAL','nomad-frozen-final-v1','independently_verified:'+'a'*64))
                    cur.execute('GRANT USAGE ON SCHEMA public,financial_control TO ' + role)
                    cur.execute('GRANT SELECT ON ALL TABLES IN SCHEMA public,financial_control TO ' + role)
                setup.commit()
            raw_connect = psycopg2.connect
            connections = []
            class LocalTLSFixture:
                # Only isolated QA simulates TLS; production always requires real TLS.
                def __init__(self, conn): self.inner = conn; self.info = SimpleNamespace(ssl_in_use=True)
                def __getattr__(self, key): return getattr(self.inner, key)
            def connect_local(dsn, **kwargs):
                assert kwargs['sslmode'] == 'require'
                conn = raw_connect(dsn, **dict(kwargs, sslmode='disable'))
                connections.append(conn)
                return LocalTLSFixture(conn)
            dsn = 'host=127.0.0.1 port=' + str(opts['port']) + ' dbname=' + name + ' user=' + role
            with patch.dict(os.environ, {'OPS_DATABASE_URL': dsn}), patch('ops_auth.require_owner'), \
                 patch.object(psycopg2, 'connect', side_effect=connect_local):
                with db.connection() as reader:
                    assert reader.read('read_only') == [('on',)]
                    assert len(reader.read('schema')) == 9
                    assert reader.read('fence')[0][0] == 'NORMAL'
                    assert reader.read('timeouts') == [('15s','3s')]
                    blocked(lambda: reader.read('UPDATE rates SET rate_value=0'), db.ReadOnlyUnavailable)
                    # Bypass the wrapper only in QA to prove PostgreSQL itself rejects writes.
                    cur = connections[-1].cursor()
                    try: cur.execute('UPDATE public.rates SET rate_value=0')
                    except (psycopg2.errors.ReadOnlySqlTransaction, psycopg2.errors.InsufficientPrivilege): pass
                    else: raise AssertionError('Database transaction permitted a mutation')
                assert connections[-1].closed
                # Writer role is refused even though its transaction is READ ONLY.
                with patch.dict(os.environ, {'OPS_DATABASE_URL': dsn.replace('user='+role, 'user=qa_local')}):
                    blocked(lambda: db.connection().__enter__(), db.ReadOnlyUnavailable)
        finally:
            with admin.cursor() as cur:
                cur.execute('DROP DATABASE ' + name)
                cur.execute('DROP ROLE ' + role)


def main():
    base = subprocess.check_output(['git','show','dfa218f944bb7204df179b9a7e4ce0f31d5d8c74:app.py'])
    assert without_ops_hook(Path('app.py').read_bytes()) == base.replace(b'\r\n',b'\n')
    for name in ('auth.py','nomad_controlled_repair.py','nomad_recovery_page.py','db.py','parsing.py','reporting.py'):
        actual = Path(name).read_bytes().replace(b'\r\n',b'\n')
        if name == 'auth.py': actual = without_ops_auth_cleanup(actual)
        assert actual == subprocess.check_output(['git','show','dfa218f944bb7204df179b9a7e4ce0f31d5d8c74:'+name]).replace(b'\r\n',b'\n')
    for name in ('ops_auth.py','ops_console.py','ops_db.py','ops_diagnostics.py'):
        tree = ast.parse(Path(name).read_text(encoding='utf-8'))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                assert node.func.id not in ('eval','exec','compile','getattr','__import__')
            if isinstance(node, (ast.Import,ast.ImportFrom)):
                modules = [n.name for n in node.names] if isinstance(node,ast.Import) else [node.module]
                assert not any(m.split('.')[0] in {'subprocess','db','pandas','financial_atomic','nomad_runtime','nomad_controlled_repair'} for m in modules)
    subprocess.run([sys.executable,'-B',__file__,'--cold'],check=True,timeout=90)
    import streamlit as st
    import ops_auth as auth
    import ops_db as db
    import ops_diagnostics as diag
    import deployment_identity as identity
    import auth as main_auth
    config = owner_config()
    with patch.dict(os.environ,config):
        owner = dict(authenticated=True,login_user='George')
        owner[auth.PROOF] = auth.proof_value()
        for bad in ({},dict(owner,authenticated=False),dict(owner,login_user='Areti'),dict(owner,third_report_authenticated=True),dict(owner,login_user='george')):
            blocked(lambda:auth.require_owner(bad),auth.AccessDenied)
        auth.require_owner(owner)
        logout_state = dict(owner, _ops_result={'status':'PASS'}, _ops_owner_password='synthetic')
        with patch.object(st,'session_state',logout_state): main_auth.sign_out()
        assert auth.PROOF not in logout_state and '_ops_result' not in logout_state
        logout_state.update(authenticated=True,login_user='George')
        blocked(lambda:auth.require_owner(logout_state),auth.AccessDenied)
        with patch.dict(os.environ,{'OPS_OWNER_USERNAME':''}): blocked(lambda:auth.require_owner(owner),auth.AccessDenied)
        with patch.dict(os.environ,{'OPS_OWNER_PASSWORD_HASH':''}): blocked(lambda:auth.require_owner(owner),auth.AccessDenied)
        alias = dict(authenticated=True,login_user='Areti')
        with patch.dict(os.environ,{'OPS_OWNER_USERNAME':'Areti'}):
            alias[auth.PROOF]=auth.proof_value(); auth.require_owner(alias)
        with patch.object(st,'session_state',dict(authenticated=True,login_user='George',**{auth.PASSWORD:'wrong'})):
            auth.unlock(); assert auth.PROOF not in st.session_state and auth.PASSWORD not in st.session_state
            st.session_state[auth.PASSWORD]='synthetic-owner-only'; auth.unlock(); auth.require_owner()
        with patch.dict(os.environ,{'OPS_OWNER_PASSWORD_HASH':config['OPS_OWNER_PASSWORD_HASH'][:-1]+'0'}):
            blocked(lambda:auth.require_owner(owner),auth.AccessDenied)
        with patch.object(st,'session_state',owner):
            cursor = Mock(); reader = db.Reader(cursor)
            for query in ('INSERT','UPDATE','DELETE','CREATE','DROP','COMMIT','SELECT 1','SELECT pg_sleep(10)','WITH t AS (DELETE FROM x RETURNING *) SELECT * FROM t'):
                blocked(lambda:reader.read(query),db.ReadOnlyUnavailable)
            cursor.execute.assert_not_called()
            for method in ('execute','commit','cursor','connection'): assert not hasattr(reader,method)
            for key,(sql,params,limit) in db.QUERIES.items():
                assert sql.startswith(('SELECT','SHOW'))
                assert sql.startswith('SHOW') or 'LIMIT' in sql
                assert limit <= 10 and 'SELECT *' not in sql
            schema = [(t,c,'numeric','numeric',None,None,None) for t,c in db.FIELDS]
            fence = [('NORMAL',1,'nomad-frozen-final-v1','independently_verified:'+'a'*64,datetime.now(timezone.utc))]
            answers = dict(ping=[(1,)],version=[('17.6',)],schema=schema,fence_contract=[('r',False,False)],fence=fence)
            fake_reader = SimpleNamespace(read=lambda key:answers[key])
            @contextmanager
            def fake_connection(): yield fake_reader
            good_release = dict(status='PASS',deployed_sha='a'*40,approved_sha='a'*40,render_sha='a'*40,reason='')
            with patch.object(db,'connection',fake_connection),patch.object(identity,'release_status',return_value=good_release) as release,patch.object(diag.AUDIT,'info') as audit:
                assert diag.run('status')['status']=='PASS'
                assert diag.run('health')['external_render_health']=='NOT AVAILABLE'
                with patch.object(db,'connection',side_effect=db.ReadOnlyUnavailable):
                    failed=diag.run('status')
                    assert failed['status']=='BLOCKED' and failed['database']['database']=='FAILED'
                    assert failed['financial_schema']['financial_schema']=='NOT AVAILABLE'
                    assert diag.run('health')['app_process']=='REACHABLE'
                assert diag.run('deployment')['status']=='PASS'; release.assert_called_with(allow_git=False)
                assert diag.run('release-identity')['status']=='PASS'
                assert diag.run('db-connection-test')['read_only']=='on'
                assert diag.run('financial-schema')['exact_numeric_fields']==9
                assert diag.run('writer-status')['writers']=='NORMAL'
                assert diag.run('repair-status')['repair_id']=='nomad-frozen-final-v1'
                assert diag.run('nomad-status')['nomad']=='VERIFIED'
                assert diag.run('recent-errors')['message']=='External runtime logs are not available from this read-only console.'
                assert set(diag.run('help')['commands'])==set(diag.COMMANDS)
                for command in ('SELECT * FROM secrets','status; DROP TABLE rates','!whoami','python eval(1)','__import__("os")','STATUS','health extra'):
                    assert diag.run(command)['message']=='Unknown Ops command. Type help.'
                assert all('SQL' not in str(call) and 'DROP TABLE' not in str(call) for call in audit.call_args_list)
                for index in range(9):
                    answers['schema']=schema[:index]+[tuple(list(schema[index][:2])+['double precision','float8',None,None,None])]+schema[index+1:]
                    assert diag.run('financial-schema')['status']=='BLOCKED'
                for precision,scale,domain in ((20,2,None),(None,None,'unexpected_domain')):
                    answers['schema']=[schema[0][:4]+(precision,scale,domain)]+schema[1:]
                    assert diag.run('financial-schema')['status']=='BLOCKED'
                answers['schema']=schema
                answers['fence']=[('NORMAL',1,'SECRET','SECRET',datetime.now(timezone.utc))]
                assert 'SECRET' not in json.dumps(diag.run('writer-status'))
                assert diag.run('nomad-status')['nomad']=='NOT AVAILABLE'
                with patch.object(identity,'release_status',side_effect=RuntimeError('SECRET DSN')):
                    assert 'SECRET' not in json.dumps(diag.run('deployment'))
            # Startup contract, read-only checks, no fallback, refusal of writer privileges/TLS.
            import psycopg2
            raw = Mock(server_version=170006, info=SimpleNamespace(ssl_in_use=True))
            raw.cursor.return_value.__enter__=Mock(return_value=cursor)
            raw.cursor.return_value.__exit__=Mock(return_value=False)
            cursor.fetchmany.side_effect=[[('on',)],[('15s','3s')],[('PostgreSQL 17.6',)],[(True,True,True,True,True)]]
            with patch.dict(os.environ,{'OPS_DATABASE_URL':'host=example.invalid dbname=postgres user=readonly'}),patch.object(psycopg2,'connect',return_value=raw) as connect:
                with db.connection(): pass
                kwargs=connect.call_args.kwargs
                assert '-c default_transaction_read_only=on' in kwargs['options']
                assert 'statement_timeout=15000' in kwargs['options'] and 'lock_timeout=3000' in kwargs['options']
                raw.set_session.assert_called_once_with(readonly=True,autocommit=False,isolation_level='REPEATABLE READ')
                raw.commit.assert_not_called(); raw.rollback.assert_called_once_with(); raw.close.assert_called_once_with()
                for rows in ([('off',)], [('on',)],[('1s','3s')]):
                    cursor.fetchmany.side_effect=[rows]
                    blocked(lambda:db.connection().__enter__(),db.ReadOnlyUnavailable)
            with patch.dict(os.environ,{'OPS_DATABASE_URL':'','DATABASE_URL':'SECRET WRITER URL'}),patch.object(psycopg2,'connect') as connect:
                blocked(lambda:db.connection().__enter__(),db.ReadOnlyUnavailable); connect.assert_not_called()
            with patch.object(identity,'ROOT',Path('missing-ops-artifact')),patch.object(identity,'checkout_evidence',side_effect=AssertionError('Subprocess fallback')):
                assert identity.release_status(allow_git=False)['status']=='BLOCKED'
            postgres_check(db)
    print('PASS A-X owner step-up, unchanged routes/business code, allowlist/injection rejection, fixed bounded catalog queries, schema/fence/NOMAD sanitization, real PostgreSQL READ ONLY and writer-role rejection')


if __name__=='__main__':
    if sys.argv[1:]==['--cold']: cold_route()
    else: main()
