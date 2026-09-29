"""Synthetic driver evidence only; never contacts a production database."""
from types import SimpleNamespace
from unittest.mock import Mock, patch
import os


def main():
    import db
    import financial_atomic_command as command
    import financial_atomic as work
    from psycopg2.extensions import TRANSACTION_STATUS_IDLE
    host='aws-0-eu-west-1.pooler.supabase.com'
    user='postgres.'+command.APPROVED_PROJECT
    config=f'host={host} port=5432 dbname=postgres user={user}'
    params=dict(host=host,port='5432',dbname='postgres',user=user,sslmode='require')
    class Raw:
        def __init__(self):
            self.info=SimpleNamespace(ssl_in_use=True)
            self.closed=False
            self.autocommit=False; self.isolation_level=None; self.readonly=None; self.deferrable=None
            self.rollback=Mock(); self.commit=Mock(); self.cursor=Mock(side_effect=AssertionError('Transport check issued SQL'))
            self.set_session=Mock(); self.parameters=dict(params)
        def get_dsn_parameters(self): return self.parameters
        def get_transaction_status(self): return TRANSACTION_STATUS_IDLE
        def close(self): self.closed=True
    def attempt(raw, expected=True, dsn=config):
        lease=SimpleNamespace(_connection=raw,close=Mock())
        with patch.object(db,'USING_POSTGRES',True),patch.object(db,'DATABASE_URL',dsn), \
             patch.object(db,'get_connection',return_value=lease) as get:
            try: conn=command.connection()
            except work.Blocked:
                assert not expected
                if get.called: lease.close.assert_called_once()
            else:
                assert expected
                assert conn.get_transaction_status() == TRANSACTION_STATUS_IDLE
                conn.close(); conn.close()
                lease.close.assert_called_once()
                raw.set_session.assert_called_once_with(autocommit=False,isolation_level='DEFAULT',readonly='DEFAULT',deferrable='DEFAULT')
            raw.cursor.assert_not_called(); raw.commit.assert_not_called()
    # URL omits sslmode; the existing application connection supplies require.
    attempt(Raw())
    direct=Raw(); direct.parameters.update(host='db.'+command.APPROVED_PROJECT+'.supabase.co',user='postgres',sslmode='verify-full')
    attempt(direct,dsn='host='+direct.parameters['host']+' port=5432 dbname=postgres user=postgres')
    for mode in ('disable','allow','prefer','',None):
        raw=Raw(); raw.parameters['sslmode']=mode; attempt(raw,False)
    for ssl in (False,None):
        raw=Raw(); raw.info.ssl_in_use=ssl; attempt(raw,False)
    raw=Raw(); raw.info=object(); attempt(raw,False)
    for key,value in (('host','unknown.example'),('port','6543'),('dbname','other'),('user','postgres.wrong'),('hostaddr','127.0.0.1'),('options','-c search_path=other')):
        raw=Raw(); raw.parameters[key]=value; attempt(raw,False)
    raw=Raw(); raw.set_session.side_effect=RuntimeError('synthetic restore failure')
    attempt(raw); assert raw.closed, 'Unrestored session returned to app pool'
    for dsn in ('host=unknown.example port=5432 dbname=postgres user=postgres',
                config.replace('5432','6543')):
        with patch.object(db,'USING_POSTGRES',True),patch.object(db,'DATABASE_URL',dsn),patch.object(db,'get_connection') as get:
            try: command.connection()
            except work.Blocked: pass
            else: raise AssertionError('Wrong configured resource accepted')
            get.assert_not_called()
    with patch.object(db,'USING_POSTGRES',False),patch.object(db,'get_connection') as get:
        try: command.connection()
        except work.Blocked: pass
        else: raise AssertionError('Missing PostgreSQL accepted')
        get.assert_not_called()
    for mode in ('disable','allow','prefer',''):
        with patch.object(db,'USING_POSTGRES',True),patch.object(db,'DATABASE_URL',config), \
             patch.dict(os.environ,{'POSTGRES_SSLMODE':mode}),patch.object(db,'get_connection') as get:
            try: command.connection()
            except work.Blocked: pass
            else: raise AssertionError('Unsafe app SSL policy accepted')
            get.assert_not_called()
    print('PASS app-pool reuse; URL-free TLS policy; negotiated TLS; direct/session pooler; unsafe/missing evidence denied; zero SQL/writes; session restored')


if __name__ == '__main__': main()
