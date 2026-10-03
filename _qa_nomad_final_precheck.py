"""Synthetic frozen-evidence, authorization, no-write and production UI tests."""
from contextlib import ExitStack
from copy import deepcopy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock,patch


def main():
    import nomad_precheck as n
    import nomad_runtime as runtime
    import existing_import_compare as auth
    data=b'%PDF-synthetic-only'
    hashes=[{'Scope':k[0],'Record ID':k[1],'Precondition SHA-256':hashlib.sha256(str(k).encode()).hexdigest()}
            for k in n.bindings.HASH_BINDINGS]
    expected={(r['Scope'],r['Record ID']):hashlib.sha256(r['Precondition SHA-256'].encode()).hexdigest() for r in hashes}
    diagnostics={'hashes':hashes,'schema':[{'Table':'synthetic','column_name':'amount','data_type':'numeric'}],
                 'fields':[],'audit_count':0}
    plan={'changes':[dict(table='synthetic',id=1,field='amount',old='1.01',new='1.02')]}
    schema=n.digest(diagnostics['schema']); catalog={}
    class Cursor:
        description=[]
        def __enter__(self): return self
        def __exit__(self,*a): pass
        def execute(self,q,params=None):
            assert q.startswith(('SELECT ','SHOW '))
        def fetchall(self): return []
        def fetchone(self): return ('postgres','127.0.0.1',5432)
    conn=Mock(); conn.cursor.return_value=Cursor()
    for state,ctx in [({},None),({'authenticated':True,'login_user':'Areti'},None),
            ({'authenticated':True,'login_user':'Other'},object()),
            ({'authenticated':True,'login_user':'areti'},object()),
            ({'authenticated':True,'login_user':'Areti','third_report_authenticated':True},object())]:
        with patch.object(auth.st,'session_state',state),patch.object(auth,'get_script_run_ctx',return_value=ctx),patch.object(n,'connection') as connect:
            try: n.run(data)
            except ValueError: pass
            else: raise AssertionError('Unauthorized direct call')
            try: n.export({})
            except ValueError: pass
            else: raise AssertionError('Unauthorized export')
            n.render(Mock()); connect.assert_not_called()
    with ExitStack() as stack:
        for target,value in [('HASH_BINDINGS',expected),('SCHEMA_DIGEST',schema),('PLAN_DIGEST',n.digest(plan['changes'])),('EXTENDED_CATALOG_DIGEST',n.digest(catalog))]:
            stack.enter_context(patch.object(n.bindings,target,value))
        stack.enter_context(patch.object(runtime,'derive',return_value=plan))
        stack.enter_context(patch.object(runtime,'PDF_SHA256',hashlib.sha256(data).hexdigest()))
        stack.enter_context(patch.object(n,'authorized',return_value=True))
        stack.enter_context(patch.object(auth,'authorized',return_value=True))
        # safe_export keeps its own server authorization check.
        stack.enter_context(patch('event_trigger_diagnostics.authorized',return_value=True))
        assert n.evaluate({},diagnostics,data,catalog)['overall']=='PASS'
        for scope in ('classified_transactions','statement_balances','category_list','statement_imports','complete import scope + schema + dependencies'):
            bad=deepcopy(diagnostics)
            next(r for r in bad['hashes'] if r['Scope']==scope)['Precondition SHA-256']='f'*64
            outcome=n.evaluate({},bad,data,catalog)
            assert outcome['overall']=='BLOCKED' and 'FROZEN_PRECONDITION_CHANGED' in outcome['mismatch_reasons']
        bad=deepcopy(diagnostics); bad['schema'][0]['data_type']='real'
        assert n.evaluate({},bad,data,catalog)['monetary_schema']=='BLOCKED'
        with patch.object(n.bindings,'PLAN_DIGEST','changed'):
            assert n.evaluate({},diagnostics,data,catalog)['approved_plan_match']=='BLOCKED'
        with patch.object(n.bindings,'EXTENDED_CATALOG_DIGEST',None):
            assert 'FROZEN_DEFAULTS_CONSTRAINTS_INDEXES_DEPENDENCY_BASELINE_MISSING' in n.evaluate({},diagnostics,data,catalog)['mismatch_reasons']
        assert n.evaluate({},diagnostics,data,{'changed':True})['overall']=='BLOCKED'
        stack.enter_context(patch.object(n,'release_identity',return_value={'status':'PASS','deployed_sha':'a'*40}))
        stack.enter_context(patch.object(n,'connection',return_value=conn))
        stack.enter_context(patch.object(n,'validate_catalog',return_value={}))
        stack.enter_context(patch.object(n,'collect_locked',return_value=({},diagnostics)))
        extended={name:[] for name in n.CATALOG_QUERIES}
        stack.enter_context(patch.object(n.bindings,'EXTENDED_CATALOG_DIGEST',n.digest(extended)))
        evidence=n.run(data)
        assert evidence['overall']=='PASS'
        assert len(evidence['hash_results'])==24
        assert json.loads(n.export(evidence))==evidence
        conn.commit.assert_not_called(); conn.rollback.assert_called_once(); conn.close.assert_called_once()
        conn.set_session.assert_called_once_with(readonly=True,autocommit=False,isolation_level='REPEATABLE READ')
        altered=deepcopy(evidence); altered['overall']='changed'
        try: n.export(altered)
        except ValueError: pass
        else: raise AssertionError('Tampered evidence export')
        with patch.object(n,'validate_catalog',side_effect=n.CatalogMismatch('REVIEWED_TRIGGER_DEPENDENCIES_CHANGED')):
            assert n.run(data)['overall']=='BLOCKED'
        with patch.object(n,'release_identity',side_effect=RuntimeError('password=private')):
            assert 'private' not in json.dumps(n.run(data))
        assert n.run(b'wrong-pdf')['overall']=='BLOCKED'
        with patch.object(conn,'rollback',side_effect=RuntimeError('private')):
            assert n.run(data)['overall']=='BLOCKED'
        class UI:
            def __init__(self): self.clicked=False; self.downloads=[]; self.errors=[]
            def subheader(self,*a): pass
            def info(self,*a): pass
            def file_uploader(self,*a,**k): return SimpleNamespace(getvalue=lambda:data)
            def button(self,*a,**k): return self.clicked
            def success(self,*a): pass
            def error(self,text): self.errors.append(text)
            def download_button(self,*a,**k): self.downloads.append(k['data'])
        ui=UI()
        with patch.object(n,'run',return_value=evidence) as run:
            n.render(ui); run.assert_not_called()
            ui.clicked=True; n.render(ui); run.assert_called_once_with(data)
            assert json.loads(ui.downloads[0])==evidence
    for query in ('UPDATE x SET y=1','INSERT INTO x VALUES(1)','DELETE FROM x','ALTER TABLE x','WITH x AS (DELETE FROM x) SELECT 1'):
        try: n.ReadOnlyCursor(Cursor()).execute(query)
        except ValueError: pass
        else: raise AssertionError('Mutation accepted')
    source=Path(n.__file__).read_text()
    assert not any(s in source for s in ('work.apply(','.commit(','.reverse(','.journal('))
    for sha in ('a'*40, 'b'*40):
        with patch('deployment_identity.actual_identity',return_value=sha), patch.dict(n.os.environ,{'RENDER_GIT_COMMIT':sha,'NOMAD_APPROVED_RELEASE_SHA':sha},clear=True):
            assert n.release_identity()=={'status':'PASS','deployed_sha':sha,'approved_sha':sha}
    for key in ('RENDER_GIT_COMMIT','NOMAD_APPROVED_RELEASE_SHA'):
        for bad in ('b'*40,'a'*39,'a'*41,'g'*40,' '+ 'a'*40,'a'*40+'\n','A'*40):
            env={'RENDER_GIT_COMMIT':'a'*40,'NOMAD_APPROVED_RELEASE_SHA':'a'*40}
            if bad is None: del env[key]
            else: env[key]=bad
            with patch('deployment_identity.actual_identity',return_value='a'*40), patch.dict(n.os.environ,env,clear=True):
                try: n.release_identity()
                except ValueError as exc: assert str(exc)=='RELEASE_IDENTITY_UNVERIFIED'
                else: raise AssertionError('Invalid or unapproved release accepted')
    with patch.dict(n.os.environ,{},clear=True),patch.object(n,'connection') as connect:
        with patch.object(n,'authorized',return_value=True):
            assert n.run(b'not-used')['overall']=='BLOCKED'
            connect.assert_not_called()
    print('PASS frozen hashes/schema/plan/metadata, authorization, read-only transaction, rollback, UI export and no repair')


if __name__=='__main__': main()
