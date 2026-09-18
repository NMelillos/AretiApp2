"""Synthetic Database review-state persistence and canonical presentation."""
from contextlib import closing
import os
from pathlib import Path
import tempfile
from unittest.mock import patch
import ast
import hashlib
import subprocess

import pandas as pd


def without_review_status(name, source):
    from _qa_income_conflict_diagnostic import without_conflict_diagnostic
    source = without_conflict_diagnostic(name, source)
    source=source.replace(b'\r\n',b'\n')
    prior=subprocess.check_output(['git','show','0a169dc28663ea5a66c47a8641d5f9260bc15f3a:'+name]).replace(b'\r\n',b'\n')
    if source==prior:
        return source
    approved={'app.py':'7d92f903eb68d0059cfd8fd2b17fdcd7199b5aa1e657e6e3da167a67f3ead0b0',
              'db.py':'51cc101bfc54c68e693b6c4b09bc183890b0a5c660d927200c4651c5ece96d2e'}
    assert hashlib.sha256(source).hexdigest()==approved[name], 'Unreviewed change beyond Database review state'
    old,new=ast.parse(prior),ast.parse(source)
    if name=='db.py':
        original=next(n for n in old.body if isinstance(n,ast.FunctionDef) and n.name=='update_database_rows')
        new.body=[original if isinstance(n,ast.FunctionDef) and n.name=='update_database_rows' else n for n in new.body]
    else:
        original=next(n for n in ast.walk(old) if isinstance(n,ast.If) and ast.unparse(n.test)=="page == 'Database'")
        changed=next(n for n in ast.walk(new) if isinstance(n,ast.If) and ast.unparse(n.test)=="page == 'Database'")
        changed.body=original.body
    assert ast.dump(old)==ast.dump(new), 'Protected code outside Database editing changed'
    return prior


def postgres():
    import db
    import psycopg2
    kwargs=dict(host='127.0.0.1',port=int(os.environ['ARETI_QA_PG_PORT']),user='qa_local',sslmode='disable')
    admin=psycopg2.connect(dbname='postgres',**kwargs)
    admin.autocommit=True
    name='qa_review_'+os.urandom(6).hex()
    try:
        with admin.cursor() as cur:
            cur.execute('SHOW data_directory')
            assert Path(cur.fetchone()[0]).resolve()==Path(os.environ['ARETI_QA_PG_DATA']).resolve()
            cur.execute('CREATE DATABASE '+name)
        try:
            def connect(): return db.PostgresConnection(psycopg2.connect(dbname=name,**kwargs))
            with patch.object(db,'get_connection',connect),patch.object(db,'USING_POSTGRES',True):
                db.init_db()
                with closing(connect()) as conn:
                    conn.cursor().execute("INSERT INTO classified_transactions (id,row_hash,status,reviewed,amount,amount_usd) VALUES (801,'synthetic','pending',0,-12.34,-12.34)")
                    conn.commit()
                for status,flag,expected in [('pending',True,('reviewed',1)),('reviewed',False,('pending',0))]:
                    assert db.update_database_rows(pd.DataFrame([dict(id=801,status=status,reviewed=flag)]))==1
                    with closing(connect()) as conn:
                        cur=conn.cursor()
                        cur.execute('SELECT status,reviewed FROM classified_transactions WHERE id=801')
                        assert tuple(cur.fetchone())==expected
                try:
                    db.update_database_rows(pd.DataFrame([dict(id=801,status='reviewed',reviewed=True,_expected_reviewed=0,_expected_status='reviewed')]))
                except db.ConcurrentTransactionEditError: pass
                else: raise AssertionError('Concurrent status-only change accepted')
        finally:
            with admin.cursor() as cur: cur.execute('DROP DATABASE '+name)
    finally: admin.close()
    print('PASS PostgreSQL checkbox/status persistence and independent status concurrency')


def main():
    import db
    tree=ast.parse(Path('app.py').read_text(encoding='utf-8'))
    branch=next(n for n in ast.walk(tree) if isinstance(n,ast.If) and ast.unparse(n.test)=="page == 'Database'")
    save_call=next(n for n in ast.walk(branch) if isinstance(n,ast.Call) and isinstance(n.func,ast.Name) and n.func.id=='_add_transaction_edit_expectations')
    draft=pd.DataFrame([dict(id=801,category='Synthetic',subcategory='General',status='reviewed',reviewed=True,amount=-12.34,amount_usd=-15.67,fx_rate=1.27,account_name='Synthetic')])
    payload=eval(compile(ast.Expression(save_call.args[0]),'app.py','eval'),{'changed_rows':draft,'_apply_category_pair_values':lambda rows:rows.copy()})
    assert set(payload.columns)=={'id','category','subcategory','status','reviewed'}, 'Review-only UI Save resubmits financial/account fields and triggers FX recalculation'
    assert not db.USING_POSTGRES
    assert Path(os.environ['TEMP']).drive.upper() == 'E:'
    with tempfile.TemporaryDirectory(dir=os.environ['TEMP']) as root, patch.object(db, 'DB_PATH', str(Path(root)/'review.sqlite')):
        db.init_db()
        with closing(db.get_connection()) as conn, conn:
            for identity, status, flag in [(801,'pending',0),(802,'reviewed',1),(803,'pending',1),(804,'reviewed',None),(805,None,None),(806,'excluded',1),(807,'pending','true')]:
                conn.execute('''INSERT INTO classified_transactions
                    (id,row_hash,txn_date,category,subcategory,status,reviewed,amount,amount_usd,currency)
                    VALUES (?,?,'2026-01-01','Synthetic','General',?,?,-12.34,-12.34,'USD')''',
                    (identity,'synthetic-review-'+str(identity),status,flag))
        protected=db.get_all_transactions().copy()
        def save(identity, status, flag, **expected):
            return db.update_database_rows(pd.DataFrame([dict(id=identity,status=status,reviewed=flag,**expected)]))
        assert save(801,'pending',True)==1
        with closing(db.get_connection()) as conn:
            assert tuple(conn.execute('SELECT status,reviewed FROM classified_transactions WHERE id=801').fetchone())==('reviewed',1), 'Checked Reviewed retained stale pending Status'
        assert save(801,'reviewed',False)==1
        assert save(801,'reviewed',False)==1  # Explicit Status change also synchronizes checkbox.
        from review_state import display_rows, edit_values, counts
        frame=display_rows(db.get_all_transactions())
        assert frame.set_index('id').loc[803,'status']=='reviewed'
        assert bool(frame.set_index('id').loc[804,'reviewed'])
        assert frame.set_index('id').loc[805,'status']=='pending'
        assert frame.set_index('id').loc[806,'status']=='excluded'
        raw=db.get_all_transactions()
        assert counts(db.filter_financially_active_transactions(frame))==counts(db.filter_financially_active_transactions(raw))
        for flag in (None,float('nan'),pd.NA,'',0,False,'false'):
            sample=display_rows(pd.DataFrame([dict(status='pending',reviewed=flag)]))
            assert sample.status.iloc[0]=='pending' and not sample.reviewed.iloc[0]
        for flag in (1,1.0,True,'true','checked','1.0'):
            sample=display_rows(pd.DataFrame([dict(status='pending',reviewed=flag)]))
            assert sample.status.iloc[0]=='reviewed' and sample.reviewed.iloc[0]
        before=dict(status='pending',reviewed=False)
        assert edit_values(dict(status='pending',reviewed=True),before)==dict(status='reviewed',reviewed=True)
        assert edit_values(dict(status='reviewed',reviewed=False),before)==dict(status='reviewed',reviewed=True)
        for legacy in (dict(status='pending',reviewed=1),dict(status='reviewed',reviewed=None)):
            assert edit_values(dict(legacy),legacy)==dict(status='reviewed',reviewed=True), 'Unchanged legacy payload must retain effective Reviewed state'
        assert save(803,'pending',True)==1
        assert save(804,'reviewed',None)==1
        assert save(807,'reviewed',True,_expected_reviewed='true',_expected_status='pending')==1
        with closing(db.get_connection()) as conn:
            assert [tuple(row) for row in conn.execute('SELECT status,reviewed FROM classified_transactions WHERE id IN (803,804) ORDER BY id').fetchall()]==[('reviewed',1),('reviewed',1)]
        assert save(801,'pending',True,_expected_category='Synthetic',_expected_subcategory='General',_expected_reviewed=1,_expected_status='reviewed')==1
        current=db.get_all_transactions().copy()
        stale=pd.DataFrame([dict(id=802,status='pending',reviewed=False,_expected_category='Synthetic',_expected_subcategory='General',_expected_reviewed=1,_expected_status='reviewed'),
                            dict(id=801,status='reviewed',reviewed=True,_expected_category='Synthetic',_expected_subcategory='General',_expected_reviewed=1,_expected_status='reviewed')])
        try:
            db.update_database_rows(stale)
        except db.ConcurrentTransactionEditError:
            pass
        else:
            raise AssertionError('Stale batch accepted')
        pd.testing.assert_frame_equal(current,db.get_all_transactions())
        financial=[c for c in protected if c not in ('status','reviewed','reviewed_at')]
        pd.testing.assert_frame_equal(protected[financial],db.get_all_transactions()[financial])
        assert len(db.get_pending_transactions())==counts(db.filter_financially_active_transactions(db.get_all_transactions()))['pending']
    print('PASS review status, checkbox, legacy presentation, fresh persistence, counters, stale batch rollback and financial preservation')
    postgres()
    for name in ('app.py','db.py'):
        without_review_status(name,Path(name).read_bytes())


if __name__=='__main__': main()
