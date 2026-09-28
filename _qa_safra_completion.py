"""Synthetic six-account Safra description and unchanged lifecycle regression."""
import ast
from contextlib import closing
from decimal import Decimal
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
from unittest.mock import patch

import pandas as pd
import parsing

BASE = '300a07eaeb8e1fdfa29aab48cc640f0c8663344c'


def without_completion(name, source):
    from financial_storage_qa import protected_source
    source = protected_source(name, source)
    from _qa_safra_duplicate_preview import without_duplicate_preview
    source = without_duplicate_preview(name, source)
    source=source.replace(b'\r\n',b'\n')
    prior=subprocess.check_output(['git','show',BASE+':'+name]).replace(b'\r\n',b'\n')
    if source==prior:
        return source
    assert name=='parsing.py', 'Non-parser source changed'
    old = b'''            booking_currencies = set(re.findall(r"\\b(?:EUR|USD|CHF|GBP)\\b", description))
            if booking_currencies - {section["currency"]}:
                fail("booking currency conflicts with page account currency; verify the statement account before importing")'''
    new = b'''            # Currency comes from the validated table header, not free-text descriptions.'''
    assert source.count(new)==1 and prior.count(old)==1
    restored=source.replace(new,old,1)
    assert restored==prior, 'Change outside exact Safra description-currency correction'
    assert ast.dump(ast.parse(restored))==ast.dump(ast.parse(prior))
    return prior


def fixture(empty=False):
    from _qa_safra_import import fake_iban
    pages=[]
    for i,currency in enumerate(('USD','EUR','GBP','USD','EUR','GBP')):
        count=0 if empty else (4,0,4,0,0,1)[i]
        text=(f'Bank J. Safra Sarasin AG\nAccount statement in {currency} 01.01.2026 to 31.07.2026\n'
              f'Current account {currency} / IBAN {fake_iban(i+21)}\n'
              'Synthetic Holding Company acting as trustee of the\nSynthetic Demonstration Trust\n'
              f'Client number Date Ref. no. Transaction Value date Debit Credit Balance in {currency}\n'
              f'9.99999.9\nAccount number\n9.99999.9 {9100+i}\n')
        if count:
            text+='31.12.2025 Balance carried forward in your favour 1 000,00\n'
            balance=1000
            for j in range(count):
                magnitude=10*(i+1)
                balance-=magnitude
                description='Synthetic USD acc in favor of Synthetic recipient - Distribution' if i==2 and j==0 else f'Synthetic fee {i}-{j}'
                text+=f'12.03.2026 {810000+i*10+j} {description} 13.03.2026 {magnitude},00 {balance},00\n'
            text+=f'31.07.2026 Balance in your favour {balance},00\n'
        else:
            text+='No bookings were carried out during the period stated.\n31.07.2026 Balance in your favour '+('50,00' if i in (0,3) else '0,00')+'\n'
        pages.append(text)
    return pages


def main():
    root=Path(os.environ['TEMP']).resolve()
    assert root.drive.upper()=='E:'
    for key in list(os.environ):
        if any(p in key.upper() for p in ('DATABASE','POSTGRES','SUPABASE')) or key.upper().startswith('PG'):
            del os.environ[key]
    pages=fixture()
    old=ast.parse(subprocess.check_output(['git','show',BASE+':parsing.py']))
    fn=next(n for n in old.body if isinstance(n,ast.FunctionDef) and n.name=='_parse_safra_pdf_text')
    namespace=dict(parsing.__dict__)
    exec(compile(ast.Module(body=[fn],type_ignores=[]),'baseline','exec'),namespace)
    try:
        namespace['_parse_safra_pdf_text'](pages[2])
    except parsing.SafraParseError as error:
        assert 'booking currency conflicts with page account currency' in str(error)
    else:
        raise AssertionError('Baseline false conflict not reproduced')
    if '--baseline' in sys.argv:
        print('PASS untouched baseline rejects counterparty currency description')
        return
    rows=parsing._parse_safra_pages(pages)
    assert len(rows)==9
    assert [s['transaction_count'] for s in rows.attrs['safra_sections']]==[4,0,4,0,0,1]
    assert sum((Decimal(str(v)) for v in rows.Amount),Decimal(0))==Decimal('-220')
    assert rows[rows.source_page.eq(3)].statement_currency.eq('GBP').all()
    assert 'USD acc' in rows[rows.source_page.eq(3)].Description.iloc[0]
    from _qa_safra_uat import labelled_accounts
    from safra_page_qa import must_reject
    from _qa_safra_lifecycle import upload, UI
    import db
    accounts=labelled_accounts(rows)
    accounts['account_name']='Synthetic Holding Company acting as trustee of the Synthetic Demonstration Trust'
    for altered in (pages[2].replace('Current account GBP','Current account USD'),
                    pages[2].replace('Balance in GBP','Balance in USD'),
                    pages[2].replace('Balance in GBP','Balance in'),
                    pages[2].replace('Balance in GBP','Balance in GBP USD'),
                    pages[2].replace(' / IBAN ',' / INVALID ')):
        must_reject(lambda:parsing._parse_safra_pages([altered]))
    for bad in (accounts.iloc[1:],pd.concat([accounts,accounts.iloc[[2]]],ignore_index=True),
                accounts.assign(account_number=accounts.account_number.str[:-1])):
        must_reject(lambda:db._safra_page_accounts(rows,bad))
    mapped=db._safra_page_accounts(rows,accounts.sample(frac=1,random_state=3))
    assert len(mapped)==6 and len({v['account_number'] for v in mapped.values()})==6
    for section in rows.attrs['safra_sections']:
        assert Decimal(section['opening_balance'])+Decimal(section['money_in'])-Decimal(section['money_out'])==Decimal(section['closing_balance'])
        group=rows[rows.source_page.eq(section['source_page'])]
        assert len(group)==section['transaction_count']
        assert group.statement_currency.eq(section['statement_currency']).all()
        assert group.source_iban.eq(section['source_iban']).all()
    # The real evidence disproved the historical assumption that description tokens
    # are currency fields, including text that happens to resemble a field label.
    for token in ('EUR','USD','GBP','CHF','JPY','Booking currency: USD'):
        variant=parsing._parse_safra_pages([p.replace('Synthetic USD acc',token+' counterparty account') for p in pages])
        assert variant.Amount.tolist()==rows.Amount.tolist()
        assert variant.source_iban.tolist()==rows.source_iban.tolist()
        assert variant.statement_currency.tolist()==rows.statement_currency.tolist()
    with tempfile.TemporaryDirectory(dir=root) as folder, patch.object(db,'DB_PATH',str(Path(folder)/'test.sqlite')), patch.object(db,'get_accounts',return_value=accounts):
        db.init_db()
        db.add_category('Synthetic','General','Synthetic')
        with closing(db.get_connection()) as conn:
            conn.execute('INSERT INTO rates (rate_month,rate_type,rate_value) VALUES (?,?,?)',('2026-01-01','GBP/USD',1.25))
            conn.commit()
        def counts():
            with closing(db.get_connection()) as conn:
                return tuple(conn.execute('SELECT COUNT(*) FROM '+t).fetchone()[0] for t in ('classified_transactions','statement_balances','statement_imports'))
        preview=upload(db,pages)
        assert not preview.errors and counts()==(0,0,0),preview.errors
        result=upload(db,pages,True)
        assert not result.errors,result.errors
        assert counts()==(9,6,6)
        history=db.get_import_history()
        assert set(history.account_number)=={s['source_iban'] for s in rows.attrs['safra_sections']}
        assert db.get_all_transactions().account_number.str.startswith('Current account').all()
        duplicate=upload(db,pages,True,b'renamed document')
        assert not duplicate.errors and counts()==(9,6,6)
        with closing(db.get_connection()) as conn:
            assert conn.execute('SELECT COUNT(*) FROM classified_transactions').fetchone()[0]==9
    # Preserve existing balance-only persistence without inventing transactions.
    with tempfile.TemporaryDirectory(dir=root) as folder, patch.object(db,'DB_PATH',str(Path(folder)/'empty.sqlite')),patch.object(db,'get_accounts',return_value=accounts):
        db.init_db()
        db.add_category('Synthetic','General','Synthetic')
        result=upload(db,fixture(empty=True),True)
        assert not result.errors,result.errors
        assert db.get_all_transactions().empty and len(db.get_import_history())==6
    for name in ('app.py','db.py','parsing.py','safra_history.py'):
        without_completion(name,Path(name).read_bytes())
    print('PASS six exact IBAN accounts, repeated currencies, long/wrapped owner, descriptive currency, contradictions, lifecycle, empty imports, duplicate prevention; unrelated source unchanged')


if __name__=='__main__':
    main()
