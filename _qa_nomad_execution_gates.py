"""Execution policy and actual controlled UI authorization/confirmation tests."""
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock, patch
import hashlib


def main():
    import ast
    from pathlib import Path
    from financial_storage_qa import protected_source
    import financial_atomic as work
    import financial_writer_control as fence
    import nomad_execution_review as review
    import nomad_controlled_repair as controlled
    import nomad_runtime as runtime
    import existing_import_compare as auth
    policy=review.ReviewedNomad()
    untrusted_cursor=Mock()
    untrusted_cursor.connection.get_dsn_parameters.return_value={'host':'production.invalid','dbname':'postgres'}
    try: work.catalog(untrusted_cursor,Mock())
    except work.Blocked as error: assert str(error)=='REVIEWED_PRODUCTION_POLICY_REQUIRED'
    else: raise AssertionError('Arbitrary execution review accepted for production')
    untrusted_cursor.execute.assert_not_called()
    catalog={'security':[(t,'r',True,False,'synthetic',None) for t in sorted(work.TABLES)],'policies':[]}
    cur=Mock(); cur.fetchone.return_value=(True,True)
    policy.security(cur,catalog)
    def blocked(call):
        try: call()
        except (ValueError,work.Blocked,fence.WritersBlocked): return
        raise AssertionError('Unsafe request accepted')
    for field in (1,2,3):
        bad=deepcopy(catalog); row=list(bad['security'][0]); row[field]='changed'; bad['security'][0]=tuple(row)
        blocked(lambda:policy.security(cur,bad))
    bad=deepcopy(catalog); bad['policies']=[('classified_transactions','unexpected_policy')]
    blocked(lambda:policy.security(cur,bad))
    cur.fetchone.return_value=(False,True); blocked(lambda:policy.security(cur,catalog))
    cur.fetchone.return_value=(True,False); blocked(lambda:policy.security(cur,catalog))
    policy.public_functions([(17170,)])
    for functions in ([],[(17170,),(99999,)],[(99999,)]): blocked(lambda:policy.public_functions(functions))
    with patch.object(review,'validate_execution_catalog',side_effect=ValueError('changed trigger')) as validate:
        blocked(lambda:policy.execution_catalog(cur)); validate.assert_called_once_with(cur)
    for query in ('UPDATE x SET v=1','WITH changed AS (DELETE FROM x RETURNING *) SELECT * FROM changed',
                  'SELECT 1; DELETE FROM x','SELECT * FROM x FOR UPDATE','CALL mutate()', 'DO $$ BEGIN END $$',
                  'ALTER TABLE x ADD v int','TRUNCATE x','INSERT INTO x VALUES(1)'):
        assert fence.needs_guard(query),query
    for query in ('SELECT * FROM classified_transactions','SHOW transaction_read_only',
                  'BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY'):
        assert not fence.needs_guard(query)
    tree=ast.parse(Path('db.py').read_text(encoding='utf-8'))
    cursor=next(n for n in tree.body if isinstance(n,ast.ClassDef) and n.name=='PostgresCursor')
    method=next(n for n in cursor.body if isinstance(n,ast.FunctionDef) and n.name=='execute')
    method.body=method.body[2:]
    try: protected_source('db.py',ast.unparse(tree).encode())
    except AssertionError: pass
    else: raise AssertionError('Source guard accepted removal of writer protection')
    # Exercise the real authorization predicate rather than trusting a widget.
    for username,primary,third,context,expected in (
        ('Areti',True,False,True,True),('Other',True,False,True,False),
        ('Areti',False,False,True,False),('Areti',True,True,True,False),
        ('Areti',True,False,False,False)):
        state={'authenticated':primary,'login_user':username,'third_report_authenticated':third}
        with patch.object(auth.st,'session_state',state),patch.object(auth,'get_script_run_ctx',return_value=object() if context else None), \
             patch.object(controlled,'connection',side_effect=AssertionError('Unauthorized database access')):
            assert auth.authorized() is expected
            if not expected: blocked(lambda:controlled.execute(b'wrong',controlled.CONFIRMATION))
    with patch.object(controlled.precheck,'require_auth'),patch.object(controlled,'connection',side_effect=AssertionError('Unexpected database access')):
        blocked(lambda:controlled.execute(b'wrong',''))
        blocked(lambda:controlled.execute(b'wrong',controlled.CONFIRMATION))
    content=b'synthetic-owner-upload'
    uploaded=SimpleNamespace(getvalue=lambda:content)
    state={'nomad_runtime_pdf':uploaded}
    class UI:
        confirmed=False
        clicked=False
        def info(self,*a): pass
        def error(self,*a): raise AssertionError('Unexpected safe UI error')
        def success(self,*a): pass
        def checkbox(self,*a,**kw): return self.confirmed
        def button(self,*a,disabled=False,**kw): return self.clicked and not disabled
    ui=UI()
    with patch.object(controlled.precheck,'authorized',return_value=True),patch.object(controlled.precheck,'require_auth'), \
         patch.object(controlled.precheck,'release_identity',return_value={'approved_sha':'a'*40}), \
         patch.object(runtime,'session_id',return_value='same-server-session'), \
         patch.object(runtime,'PDF_SHA256',hashlib.sha256(content).hexdigest()), \
         patch.object(auth.st,'session_state',state),patch.object(controlled,'execute') as execute, \
         patch('nomad_recovery_status.read_status',return_value={'state':'NORMAL','transaction_status':'NOT STARTED'}):
        controlled.render(ui,{'overall':'PASS'}); execute.assert_not_called()
        ui.clicked=True
        controlled.render(ui); execute.assert_not_called()
        ui.confirmed=True
        controlled.render(ui); execute.assert_called_once_with(content,controlled.CONFIRMATION)
        controlled.render(ui); assert execute.call_count==1
        controlled.render(ui,{'overall':'BLOCKED'}); assert execute.call_count==1
    print('PASS exact trigger/RLS/identity gates, writer SQL classification, main Areti authorization, no page-load or unconfirmed write, no repeated UI execution')


if __name__=='__main__': main()
