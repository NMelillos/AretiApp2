"""Synthetic Safra currency validation and actual Upload/Import branch regression."""
import ast
from contextlib import closing, nullcontext
from decimal import Decimal
from io import BytesIO
import os
from pathlib import Path
import tempfile
import subprocess
from types import SimpleNamespace
from unittest.mock import patch

import pandas as pd
import parsing
from safra_page_qa import fixture
from _qa_safra_uat import labelled_accounts


def without_booking_currency(source):
    source=source.replace(b'\r\n',b'\n')
    old=b'''            if re.search(r"\\b(?:EUR|USD|CHF|GBP)\\b", description):
                fail("explicit booking currency requires manual validation")'''
    new=b'''            booking_currencies = set(re.findall(r"\\b(?:EUR|USD|CHF|GBP)\\b", description))
            if booking_currencies - {section["currency"]}:
                fail("booking currency conflicts with page account currency; verify the statement account before importing")'''
    baseline=subprocess.check_output(['git','show','81d13ea71646ec7b8f83402a3e221293f5997482:parsing.py']).replace(b'\r\n',b'\n')
    assert source.count(new)==1
    restored=source.replace(new,old,1)
    assert restored==baseline, 'Parser changed outside exact Safra currency agreement check'
    return restored


def pages_for(year='2026', currency='USD'):
    return [p.replace('2026',year).replace('2025',str(int(year)-1)).replace('Synthetic fee A','Synthetic fee A '+currency) for p in fixture()]


class PDF:
    def __init__(self,pages): self.pages=[SimpleNamespace(extract_text=lambda p=p:p) for p in pages]
    def __enter__(self): return self
    def __exit__(self,*args): pass


class Stop(BaseException):
    pass


class UI:
    def __init__(self,action=False,content=b'synthetic A'):
        self.action=action
        self.file=BytesIO(content)
        self.file.name='synthetic.pdf'
        self.errors=[]
        self.messages=[]
        self.tables=[]
        self.cleared=0
        self.cache_data=SimpleNamespace(clear=lambda:None)
    def file_uploader(self,*args,**kwargs): return self.file
    def button(self,label,**kwargs):
        assert label=='Import to pending review'
        return self.action
    def empty(self):
        self.cleared+=1
        return self
    def error(self,text): self.errors.append(str(text))
    def success(self,text): self.messages.append(str(text))
    def warning(self,text): self.messages.append(str(text))
    def info(self,text): self.messages.append(str(text))
    def dataframe(self,frame,**kwargs): self.tables.append(frame.copy())
    def columns(self,n): return [self]*n
    def expander(self,*args,**kwargs): return nullcontext()
    def subheader(self,*args,**kwargs): pass
    def markdown(self,*args,**kwargs): pass
    def metric(self,*args,**kwargs): pass
    def stop(self): raise Stop()


def upload(db,pages,action=False,content=b'synthetic A'):
    ui=UI(action,content)
    tree=ast.parse(Path('app.py').read_text(encoding='utf-8'))
    branch=next(n for n in tree.body if isinstance(n,ast.If) and ast.unparse(n.test)=="page == 'Import'")
    env=dict(db.__dict__,st=ui,pd=pd,BytesIO=BytesIO)
    names={'parse_statement','parse_statement_balance','classify_statement_rows','flag_duplicates','missing_setup_items'}
    nodes=[n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name in names]
    for node in nodes: node.decorator_list=[]
    exec(compile(ast.Module(body=nodes,type_ignores=[]),'app.py','exec'),env)
    # Setup rate presence is independent of the USD rows' exact 1:1 conversion.
    env['get_rates']=lambda:pd.DataFrame([{'rate_type':'USD/USD'}])
    with patch.object(parsing.pdfplumber,'open',return_value=PDF(pages)):
        try:
            exec(compile(ast.Module(body=branch.body,type_ignores=[]),'app.py','exec'),env)
        except Stop:
            assert any('already exists' in x for x in ui.messages)
            assert ui.cleared==0, 'Completed duplicate should stop before processing'
        else:
            assert ui.cleared==2, 'Processing message was not cleared'
    return ui


def main():
    root=Path(os.environ['TEMP']).resolve()
    assert root.drive.upper()=='E:'
    for key in list(os.environ):
        if any(p in key.upper() for p in ('DATABASE','POSTGRES','SUPABASE')) or key.upper().startswith('PG'):
            del os.environ[key]
    import db
    assert not db.USING_POSTGRES
    pages=pages_for()
    rows=parsing._parse_safra_pages(pages)
    assert len(rows)==6 and rows.source_page.eq(2).all() and rows.statement_currency.eq('USD').all()
    assert sum((Decimal(str(x)) for x in rows.Amount),Decimal(0))==Decimal('-900')
    assert rows.Description.iloc[0].startswith('800000 Synthetic fee A USD ')
    for currency in ('EUR','CHF','GBP','USD EUR'):
        try:
            parsing._parse_safra_pages(pages_for(currency=currency))
        except parsing.SafraParseError:
            pass
        else:
            raise AssertionError('Contradictory booking currency accepted')
    accounts=labelled_accounts(rows)
    with tempfile.TemporaryDirectory(dir=root) as folder, patch.object(db,'DB_PATH',str(Path(folder)/'synthetic.sqlite')), patch.object(db,'get_accounts',return_value=accounts):
        db.init_db()
        db.add_category('Synthetic','General','Synthetic')
        def counts():
            with closing(db.get_connection()) as conn:
                return tuple(conn.execute('SELECT COUNT(*) FROM '+t).fetchone()[0] for t in ('classified_transactions','statement_balances','statement_imports'))
        for _ in range(3):
            ui=upload(db,pages)
            assert not ui.errors,ui.errors
            assert counts()==(0,0,0) and db.get_import_history().empty
            assert any('Prepared 6' in message for message in ui.messages)
            sections=ui.tables[0]
            assert sections.Currency.tolist()==['EUR','USD','CHF','GBP']
            assert sections.IBAN.tolist()==[s['source_iban'] for s in rows.attrs['safra_sections']]
            assert sections.Transactions.tolist()==[0,6,0,0]
        print('PASS actual Upload branch, abandoned/rerun preview: zero financial/history writes, exact page accounts')
        real=db.save_statement_balance
        calls=[]
        def fail_final(*args,**kwargs):
            calls.append(1)
            if len(calls)==4: raise ValueError('Synthetic persistence failure')
            return real(*args,**kwargs)
        with patch.object(db,'save_statement_balance',side_effect=fail_final):
            ui=upload(db,pages,True)
        assert ui.errors==['Synthetic persistence failure']
        assert counts()==(0,0,0) and db.get_import_history().empty
        ui=upload(db,pages,True)
        assert not ui.errors,ui.errors
        assert counts()==(6,4,4)
        assert len(db.get_import_history())==4
        protected=db.get_all_transactions().copy()
        for content in (b'synthetic A',b'renamed same statement'):
            ui=upload(db,pages,True,content)
            assert not ui.errors,ui.errors
            assert any('already exists' in x for x in ui.messages)
            assert counts()==(6,4,4)
        pd.testing.assert_frame_equal(protected,db.get_all_transactions())
        ui=upload(db,pages_for('2027'),False,b'synthetic B')
        assert not ui.errors and counts()==(6,4,4)
        ui=upload(db,pages_for('2027'),True,b'synthetic B')
        assert not ui.errors,ui.errors
        assert counts()==(12,8,8)
        assert len(db.get_pending_transactions())==12
        print('PASS actual explicit Import, rollback/retry, duplicate clicks, distinct statement, balances and completed-only history')


if __name__=='__main__': main()
