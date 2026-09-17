"""Exact numeric and nullable review snapshots, using isolated local databases."""
import ast
from contextlib import closing
from decimal import Decimal
import hashlib
import math
import os
from pathlib import Path
import subprocess
import tempfile
from unittest.mock import patch

import numpy as np
import pandas as pd

BASE = '81d13ea71646ec7b8f83402a3e221293f5997482'
APPROVED = {'app.py': 'a7cb304ccab6fe145097267afa3feeb52ab8cf842f1703e431674a987bc32447',
            'db.py': 'df45517bd935ed3222f1769f4599a07732cf7bfdb733bb45e210f9e2e6ca6f9e'}


def without_remaining_save(name, source):
    source = source.replace(b'\r\n', b'\n')
    if name not in ('app.py', 'db.py'):
        return source
    from _qa_review_status import without_review_status
    source = without_review_status(name, source)
    prior = subprocess.check_output(['git', 'show', BASE + ':' + name]).replace(b'\r\n', b'\n')
    if source == prior:
        return source
    assert hashlib.sha256(source).hexdigest() == APPROVED[name], 'Unreviewed change beyond numeric/null Save correction'
    allowed = {'_save_income_charity_edits'} if name == 'app.py' else {'save_reviewed_rows', '_bool_from_value'}
    old, new = ast.parse(prior), ast.parse(source)
    old_nodes = {n.name:n for n in old.body if isinstance(n, ast.FunctionDef)}
    new.body = [old_nodes[n.name] if isinstance(n, ast.FunctionDef) and n.name in allowed else n for n in new.body]
    assert ast.dump(old) == ast.dump(new), 'Protected code changed'
    return prior


def exercise(db, kind):
    db.init_db()
    connect = db.get_connection
    if kind != 'SQLite':
        with closing(connect()) as conn:
            for field in ('amount','amount_usd','fx_rate'):
                conn.cursor().execute(f'ALTER TABLE classified_transactions ALTER COLUMN {field} TYPE {kind}')
            conn.commit()
    for cat, sub in [('Income','First'),('Income','Next'),('Charity','Gift'),('Charity','Other Gift')]:
        db.add_category(cat,sub,'Income' if cat=='Income' else 'Family expenses')

    def seed():
        with closing(connect()) as conn:
            cur=conn.cursor()
            cur.execute('DELETE FROM classified_transactions')
            for identity,cat,sub,amount in [(701,'Income','First','125.730000000000000001'),(702,'Charity','Gift','-12.573000000000000001')]:
                numeric = lambda value: Decimal(value) if kind.startswith('NUMERIC') else float(value)
                cur.execute('''INSERT INTO classified_transactions
                    (id,row_hash,txn_date,category,subcategory,reviewed,status,amount,amount_usd,fx_rate,currency)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?)''',
                    (identity,'synthetic-'+str(identity),'2026-01-01',cat,sub,1,'reviewed',numeric(amount),numeric(amount),numeric('1.234500000000000001'),'USD'))
            conn.commit()

    def raw():
        with closing(connect()) as conn:
            cur=conn.cursor()
            cur.execute('SELECT id,category,subcategory,amount,amount_usd,fx_rate,currency,txn_date,reviewed,status FROM classified_transactions ORDER BY id')
            return cur.fetchall()

    def payload():
        return pd.DataFrame([dict(id=701,category='Income',subcategory='Next',reviewed=True,
                                  _expected_category='Income',_expected_subcategory='First',_expected_reviewed=True),
                             dict(id=702,category='Charity',subcategory='Other Gift',reviewed=True,
                                  _expected_category='Charity',_expected_subcategory='Gift',_expected_reviewed=True)])
    seed()
    before=raw()
    assert db.save_reviewed_rows(payload())==2
    after=raw()
    assert [r[3:] for r in after]==[r[3:] for r in before], 'Save altered protected values'
    assert after[0][2]=='Next' and after[1][2]=='Other Gift'
    print('PASS',kind,'batch Save, fresh connection, exact financial and identity preservation')

    for field,column in [('amount',3),('amount_usd',4),('fx_rate',5)]:
        seed()
        before=raw()
        value=before[1][column]
        if kind.startswith('NUMERIC'):
            value += Decimal('0.000001') if kind=='NUMERIC(20,6)' else Decimal('0.000000000000000001')
        elif kind=='REAL':
            value=float(np.nextafter(np.float32(value),np.float32(math.inf)))
        else:
            value=math.nextafter(value,math.inf)
        raced=[False]
        class Cursor:
            def __init__(self,c): self.c=c
            def __getattr__(self,name): return getattr(self.c,name)
            def execute(self,sql,params=None):
                if 'UPDATE classified_transactions' in sql and 'IS NOT DISTINCT FROM' in sql and params[8]==702 and not raced[0]:
                    raced[0]=True
                    if kind=='SQLite':
                        self.c.execute(f'UPDATE classified_transactions SET {field}=? WHERE id=?',(value,702))
                    else:
                        with closing(connect()) as other:
                            other.cursor().execute(f'UPDATE classified_transactions SET {field}=? WHERE id=?',(value,702))
                            other.commit()
                return self.c.execute(sql,params) if params is not None else self.c.execute(sql)
        class Connection:
            def __init__(self): self.conn=connect()
            def __getattr__(self,name): return getattr(self.conn,name)
            def cursor(self): return Cursor(self.conn.cursor())
        with patch.object(db,'get_connection',Connection):
            try:
                db.save_reviewed_rows(payload())
            except db.ConcurrentTransactionEditError:
                pass
            else:
                raise AssertionError('Tiny genuine '+field+' change was accepted')
        after=raw()
        assert after[0]==before[0], 'Earlier batch row did not roll back'
        assert after[1][1:3]==before[1][1:3]
        if kind=='SQLite':
            assert after==before
        else:
            assert after[1][column]!=before[1][column]
        print('PASS',kind,field,'smallest representable concurrent change rejected, batch rolled back')

    from _qa_income_charity_edit import load_functions
    env={'pd':pd,'save_reviewed_rows':db.save_reviewed_rows,'_CATEGORY_PAIR_COLUMN':'category_subcategory',
         '_NO_SUBCATEGORY_LABEL':'No subcategory'}
    load_functions(Path('app.py').read_text(encoding='utf-8'),
                   {'_save_income_charity_edits','_category_pair_label','_parse_category_pair_label','_category_pair_options'},env)
    cats=db.get_categories(include_subcategories=True)
    for flag in (None,float('nan'),pd.NA,False,0,0.0,True,1,1.0):
        seed()
        truth=False if pd.isna(flag) else flag==1
        with closing(connect()) as conn:
            conn.cursor().execute('UPDATE classified_transactions SET reviewed=?,status=? WHERE id=?',(int(truth),'reviewed' if truth else 'pending',701))
            conn.commit()
        frame=pd.DataFrame([dict(id=701,category='Income',subcategory='First',reviewed=flag,status='reviewed' if truth else 'pending',report_group='Income')])
        edited=frame.assign(category_subcategory='Income / Next')
        assert env['_save_income_charity_edits'](frame,edited,cats)==1
        assert raw()[0][2]=='Next'
    print('PASS',kind,'actual Income Save handles nullable and numeric Reviewed values')


def main():
    root=Path(os.environ['TEMP']).resolve()
    assert root.drive.upper()=='E:'
    for key in list(os.environ):
        if any(p in key.upper() for p in ('DATABASE','POSTGRES','SUPABASE')) or key.upper().startswith('PG'):
            del os.environ[key]
    import db
    assert not db.USING_POSTGRES
    with tempfile.TemporaryDirectory(dir=root) as folder, patch.object(db,'DB_PATH',str(Path(folder)/'synthetic.sqlite')):
        exercise(db,'SQLite')
    import psycopg2
    kwargs=dict(host='127.0.0.1',port=int(os.environ['ARETI_QA_PG_PORT']),user='qa_local',sslmode='disable')
    admin=psycopg2.connect(dbname='postgres',**kwargs)
    admin.autocommit=True
    try:
        with admin.cursor() as cur:
            cur.execute('SHOW data_directory')
            assert Path(cur.fetchone()[0]).resolve()==Path(os.environ['ARETI_QA_PG_DATA']).resolve()
        for kind in ('REAL','DOUBLE PRECISION','NUMERIC','NUMERIC(20,6)','NUMERIC(30,18)'):
            name='qa_types_'+os.urandom(6).hex()
            with admin.cursor() as cur: cur.execute('CREATE DATABASE '+name)
            try:
                def connect(): return db.PostgresConnection(psycopg2.connect(dbname=name,**kwargs))
                with patch.object(db,'get_connection',connect), patch.object(db,'USING_POSTGRES',True):
                    exercise(db,kind)
            finally:
                with admin.cursor() as cur: cur.execute('DROP DATABASE '+name)
    finally:
        admin.close()
    for name in ('app.py','db.py'):
        without_remaining_save(name,Path(name).read_bytes())


if __name__=='__main__':
    main()
