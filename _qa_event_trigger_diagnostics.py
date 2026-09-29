"""Synthetic authorization, deny-by-default SQL and real render-path checks."""
from unittest.mock import patch, Mock
from types import SimpleNamespace
import json


def main():
    import event_trigger_diagnostics as d
    import existing_import_compare as auth
    trigger = dict(trigger_oid=1, trigger_name='synthetic_guard', event='ddl_command_end',
        enabled='O', owner='synthetic_owner',function_oid=2,function_name='public.synthetic_guard',
        definition='CREATE FUNCTION public.synthetic_guard() RETURNS event_trigger LANGUAGE plpgsql AS $$ BEGIN RETURN; END $$',
        language='plpgsql',volatility='v',security_definer=False,settings=None,command_tags=['ALTER TABLE'],
        function_owner='synthetic_owner',function_acl=None)
    class Cursor:
        def __init__(self): self.calls=[]; self.description=[]; self.result=[]
        def __enter__(self): return self
        def __exit__(self,*args): pass
        def execute(self,query,args=None):
            assert query.startswith(('SELECT ','SHOW ')), 'Non-read SQL'
            assert query in (d.CATALOG,d.DEPENDENCIES,'SHOW transaction_read_only','SHOW server_version','SHOW session_replication_role')
            self.calls.append(query)
            if query == d.CATALOG:
                self.description=[(key,) for key in trigger]; self.result=[tuple(trigger.values())]
            elif query == d.DEPENDENCIES:
                assert args==([1],[2]); self.description=[('extension',)]; self.result=[('synthetic_extension',)]
            else: self.result=[({'SHOW transaction_read_only':'on','SHOW server_version':'17.6','SHOW session_replication_role':'origin'}[query],)]
        def fetchone(self): return self.result[0]
        def fetchall(self): return self.result
    for state,ctx in [({},None), ({'authenticated':True,'login_user':'Areti'},None),
        ({'authenticated':True,'login_user':'Other'},object()),
        ({'authenticated':True,'login_user':'areti'},object()),
        ({'authenticated':True,'login_user':'Areti','third_report_authenticated':True},object()),
        ({'authenticated':False,'login_user':'Areti'},object())]:
        with patch.object(auth.st,'session_state',state),patch.object(auth,'get_script_run_ctx',return_value=ctx),patch.object(d,'connection') as connect:
            try: d.collect()
            except d.DiagnosticBlocked: pass
            else: raise AssertionError('Unauthorized collection')
            d.render(Mock())
            try: d.safe_export({})
            except d.DiagnosticBlocked: pass
            else: raise AssertionError('Unauthorized export')
            connect.assert_not_called()
    cursor=Cursor(); conn=Mock(); conn.cursor.return_value=cursor
    with patch.object(auth.st,'session_state',{'authenticated':True,'login_user':'Areti'}),patch.object(auth,'get_script_run_ctx',return_value=object()),patch.object(d,'connection',return_value=conn):
        payload=d.collect()
        assert payload['triggers'][0]['alter_table_relevance'].startswith('POSSIBLE')
        assert json.loads(d.safe_export(payload))['triggers'][0]['definition']==trigger['definition']
        conn.commit.assert_not_called(); conn.rollback.assert_called_once(); conn.close.assert_called_once()
        conn.set_session.assert_called_once_with(readonly=True,autocommit=False,isolation_level='REPEATABLE READ')
        for secret in ('DATABASE_URL','password=private','https://private.invalid','Bearer private','api_key=private'):
            try: d.safe_export({'definition':secret})
            except d.DiagnosticBlocked: pass
            else: raise AssertionError('Sensitive output')
        class UI:
            def __init__(self): self.clicked=False; self.downloads=[]; self.errors=[]
            def subheader(self,*args): pass
            def button(self,*args,**kwargs): return self.clicked
            def dataframe(self,*args,**kwargs): pass
            def caption(self,*args): pass
            def download_button(self,*args,**kwargs): self.downloads.append(kwargs)
            def error(self,text): self.errors.append(text)
        ui=UI()
        with patch.object(d,'collect',return_value=payload) as collect:
            d.render(ui); collect.assert_not_called()
            ui.clicked=True; d.render(ui); collect.assert_called_once()
            assert json.loads(ui.downloads[0]['data'])==payload
            assert not ui.errors
        with patch.object(d,'collect',side_effect=RuntimeError('password=private')):
            d.render(ui)
            assert 'private' not in ui.errors[-1]
        for event,enabled,tags,possible in [('login','O',None,False),('table_rewrite','O',None,True),
             ('sql_drop','A',['ALTER TABLE'],True),('ddl_command_start','R',None,False),
             ('ddl_command_end','O',['CREATE TABLE'],False)]:
            trigger.update(event=event,enabled=enabled,command_tags=tags)
            assert d.collect()['triggers'][0]['alter_table_relevance'].startswith('POSSIBLE')==possible
        with patch.object(cursor,'execute',side_effect=RuntimeError('private')):
            before=conn.rollback.call_count
            try: d.collect()
            except RuntimeError: pass
            else: raise AssertionError('Query failure hidden')
            assert conn.rollback.call_count==before+1
    print('PASS authorization, direct-call refusal, SELECT/SHOW allowlist, rollback, safe output, real UI renderer, full JSON')


if __name__=='__main__': main()
