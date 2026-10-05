"""Reduced-build dependency, no-write and UI regression evidence; no source guard bypass."""
import ast
from contextlib import closing,nullcontext
from decimal import Decimal
import os
from pathlib import Path
import subprocess,tempfile
from types import SimpleNamespace
from unittest.mock import patch
import pandas as pd

BASE='f3ea9ce2d0143ef3239871bb94791ffb39fd857e'
def old(name):return subprocess.check_output(['git','show',BASE+':'+name]).decode().replace('\r\n','\n')
def main():
    assert not any(os.getenv(k) for k in ('DATABASE_URL','POSTGRES_URL','SUPABASE_URL'))
    import db
    assert not db.USING_POSTGRES
    allowed={'app.py','review_state.py','latest_import_balances.py',
             'safra_balances_qa.py','_qa_latest_import_balances.py'}
    hook=("    if name == 'app.py':\n"
          "        from reduced_release_qa import historical_app_source\n"
          "        source = historical_app_source(source)\n")
    guard=Path('safra_balances_qa.py').read_text(encoding='utf-8')
    assert guard.count(hook)==1 and guard.replace(hook,'',1)==old('safra_balances_qa.py')
    preserved=[]
    for name in subprocess.check_output(['git','ls-tree','--name-only',BASE]).decode().splitlines():
        if name.endswith('.py') and name not in allowed:
            assert Path(name).read_text(encoding='utf-8')==old(name),name
            preserved.append(name)
    assert not Path('bank_units.py').exists() and not Path('statement_reconciliation.py').exists()
    assert not Path('setup_uploads.py').exists()
    prior=ast.parse(old('app.py'));current=ast.parse(Path('app.py').read_text(encoding='utf-8'))
    funcs=lambda tree:{n.name:n for n in tree.body if isinstance(n,ast.FunctionDef)}
    for name,node in funcs(prior).items():
        if name not in ('render_status_bar','render_setup_loader'):
            assert ast.dump(node)==ast.dump(funcs(current)[name]),name
    def body(tree,label):return next(n.body for n in ast.walk(tree) if isinstance(n,ast.If) and ast.unparse(n.test)==label)
    assert ast.dump(ast.Module(body=body(prior,"page == 'Import'"),type_ignores=[]))==ast.dump(ast.Module(body=body(current,"page == 'Import'"),type_ignores=[]))
    assert not any(isinstance(n,ast.ImportFrom) and n.module in ('bank_units','statement_reconciliation','setup_uploads') for n in ast.walk(current))
    for request in ('is_third_link_report_request()', 'is_executive_report_request()'):
        assert ast.dump(ast.Module(body=body(prior,request),type_ignores=[]))==ast.dump(ast.Module(body=body(current,request),type_ignores=[])),request
    print('PASS',len(preserved),'baseline Python modules unchanged; exact Import body/functions and financial/parser/duplicate/permission dependencies preserved; no new import guard')
    class Done(BaseException):pass
    class UI:
        def __init__(self):self.errors=[];self.session_state={};self.cache_data=SimpleNamespace(clear=lambda:None)
        def spinner(self,*a):return nullcontext()
        def error(self,text):self.errors.append(text)
        def rerun(self):raise Done()
    env={'st':UI()}
    exec(compile(ast.Module(body=[funcs(current)['_save_setup_upload']],type_ignores=[]),'app.py','exec'),env)
    calls=[]
    class Limited(Exception):status_code=429;headers={'Retry-After':'17 seconds'}
    def limited(value):calls.append(value);raise Limited()
    env['_save_setup_upload']('upload',limited,'synthetic setup')
    assert calls==['upload'] and '17 seconds' in env['st'].errors[0]
    def lost(value):calls.append(value);raise ConnectionError('lost acknowledgement')
    env['_save_setup_upload']('uncertain',lost,'synthetic setup')
    assert calls==['upload','uncertain'] and 'verify' in env['st'].errors[-1].lower()
    try:env['_save_setup_upload']('success',lambda value:3,'synthetic setup')
    except Done:pass
    assert env['st'].session_state['setup_save_message']=='Loaded 3 synthetic setup.'
    print('PASS Setup 429 guidance, unknown acknowledgement, successful rerun; one writer call and no replay')
    from review_state import match_buckets
    rows=pd.DataFrame({'match_type':['exact','similar','new','Rule','unexpected',None,' EXACT ']})
    counts=match_buckets(rows)
    assert counts==dict(exact=2,similar=1,new=1,rule=1,other=2) and sum(counts.values())==7
    assert match_buckets(rows.iloc[:0])==dict(exact=0,similar=0,new=0,rule=0,other=0)
    with tempfile.TemporaryDirectory(dir=os.environ['TEMP']) as folder,patch.object(db,'DB_PATH',str(Path(folder)/'reduced.sqlite')):
        db.init_db()
        with closing(db.get_connection()) as conn:
            for i,(status,reviewed,match) in enumerate([('pending',0,'exact'),('pending',0,'rule'),('pending',0,'custom'),('excluded',0,'new'),('reviewed',1,'similar'),('pending',1,'new')]):
                conn.execute('INSERT INTO classified_transactions(row_hash,txn_date,amount,amount_usd,currency,status,reviewed,match_type) VALUES(?,?,?,?,?,?,?,?)',(str(i),'2026-10-01',-1,-1,'USD',status,reviewed,match))
            conn.commit()
            before='\n'.join(conn.iterdump())
        pending=db.get_pending_transactions();assert len(pending)==3
        assert sum(match_buckets(pending).values())==3
        rendered=[]
        st=SimpleNamespace(markdown=lambda html,**kw:rendered.append(html),warning=lambda v:None)
        header={'st':st,'get_dashboard_counts':lambda:dict(pending=999,reviewed=0,categories=0,accounts=0,rates=0,memory=0,statements=0), '_db_get_pending_transactions':db.get_pending_transactions}
        exec(compile(ast.Module(body=[funcs(current)['render_status_bar']],type_ignores=[]),'app.py','exec'),header)
        header['render_status_bar']()
        assert len(header['_pending_run_snapshot'])==3 and '<div class="metric-label">Pending</div></div><div class="metric-value">3</div>' in rendered[0],rendered
        with closing(db.get_connection()) as conn:assert '\n'.join(conn.iterdump())==before
    print('PASS fresh header/Pending population and exhaustive selected match buckets; excluded/reviewed rows protected; no writes')
    with tempfile.TemporaryDirectory(dir=os.environ['TEMP']) as folder,patch.object(db,'DB_PATH',str(Path(folder)/'report-exact-bank.sqlite')):
        db.init_db()
        with closing(db.get_connection()) as conn:
            schema=conn.execute("SELECT sql FROM sqlite_master WHERE name='statement_balances'").fetchone()[0]
            conn.execute('DROP TABLE statement_balances')
            conn.execute(schema.replace('opening_balance REAL','opening_balance TEXT').replace('closing_balance REAL','closing_balance TEXT'))
            conn.execute("INSERT INTO account_list(account_name,bank,account_number,currency,rate_type) VALUES('Synthetic deposit','Citizens Bank','QA-EXACT-BANK','USD','USD/USD')")
            conn.execute("INSERT INTO statement_imports(statement_hash,imported_at,transaction_count) VALUES('qa-bank','2026-10-05T09:00:00+00:00',0)")
            conn.execute("INSERT INTO statement_balances(statement_hash,account_name,bank,account_number,currency,period_start,period_end,opening_balance,closing_balance) VALUES('qa-bank','Synthetic deposit','Citizens Bank','QA-EXACT-BANK','USD','2026-09-01','2026-09-30','2.50','2.50')")
            conn.commit();before='\n'.join(conn.iterdump())
        from latest_import_balances import snapshot
        rows,total,warnings=snapshot(db)
        assert rows[0]['Verification']=='SOURCE RECONCILIATION NOT VERIFIED' and total==Decimal('2.50') and not warnings
        with closing(db.get_connection()) as conn:assert '\n'.join(conn.iterdump())==before
        with closing(db.get_connection()) as conn:
            conn.execute("UPDATE account_list SET account_name='Renamed deposit' WHERE account_number='QA-EXACT-BANK'")
            conn.execute("INSERT INTO account_list(account_name,bank,account_number,currency,rate_type) VALUES('Separate currency','Citizens Bank','QA-EXACT-BANK','EUR','EUR/USD')")
            conn.execute("INSERT INTO account_list(account_name,bank,account_number,currency,rate_type) VALUES('New BOC label','BOC','QA-BOC','EUR','EUR/USD')")
            conn.execute("INSERT INTO statement_imports(statement_hash,imported_at,transaction_count) VALUES('qa-boc-alias','2026-10-05T09:00:00+00:00',0)")
            conn.execute("INSERT INTO statement_balances(statement_hash,account_name,bank,account_number,currency,period_start,period_end,opening_balance,closing_balance) VALUES('qa-boc-alias','Legacy BOC label','Bank of Cyprus','QA BOC','EUR','','2026-09-30','4.00','4.00')")
            conn.commit();before='\n'.join(conn.iterdump())
        rows,total,warnings=snapshot(db)
        renamed=next(r for r in rows if r['Bank']=='Citizens Bank' and r['Currency']=='USD')
        assert renamed['Closing balance']==Decimal('2.50') and renamed['Status']=='INCOMPLETE'
        assert renamed['Verification']=='SETUP/SOURCE LABEL MISMATCH' and 'source label: Synthetic deposit' in renamed['Account name']
        assert next(r for r in rows if r['Bank']=='Citizens Bank' and r['Currency']=='EUR')['Status']=='NO IMPORT'
        boc=next(r for r in rows if r['Bank']=='BOC')
        assert boc['Closing balance']==Decimal('4.00') and boc['Status']=='INCOMPLETE' and boc['Verification']=='SETUP/SOURCE LABEL MISMATCH'
        assert total==Decimal(0) and any('Legacy BOC label' in w for w in warnings)
        with closing(db.get_connection()) as conn:assert '\n'.join(conn.iterdump())==before
    print('PASS Citizens deposit is not misidentified as Citi liability; exact stored totals and no report writes')
    print('PASS renamed Setup/issuer alias/number formatting preserve uncertain latest source balances and both labels; different currency remains separate; no invented ownership or complete total')
    denied=UI();denied.stop=lambda:(_ for _ in ()).throw(Done())
    checks={'st':denied,'corrections_authorized':lambda:False}
    try:exec(compile(ast.Module(body=body(current,"page == 'Corrections'"),type_ignores=[]),'app.py','exec'),checks)
    except Done:pass
    else:raise AssertionError('Unauthorised Corrections did not stop')
    assert denied.errors==['Corrections require the authenticated main Areti session.']
    print('PASS Corrections authentication gate and unchanged early THIRD/Executive routes')

if __name__=='__main__':main()
