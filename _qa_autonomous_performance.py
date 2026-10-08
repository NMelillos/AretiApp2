"""Preview optimization compared to the preserved live-code oracle, offline."""
import ast,os,unittest
from pathlib import Path
from contextlib import closing
from decimal import Decimal
from unittest.mock import patch
import pandas as pd
import db
from _qa_post_import_message import CompletionTests
class DuplicatePerformanceTests(CompletionTests):
    def oracle(self):
        tree=ast.parse((Path(os.environ['ARETI_REAL_BASELINE'])/'db.py').read_text(encoding='utf-8'))
        fn=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='mark_duplicate_transactions')
        fn.name='previous_preview';scope=dict(vars(db));exec(compile(ast.fix_missing_locations(ast.Module(body=[fn],type_ignores=[])),'preserved-live-preview','exec'),scope)
        return scope['previous_preview']
    def seed(self):
        with closing(db.get_connection()) as c:
            for i in range(24):
                c.execute('INSERT INTO classified_transactions(row_hash,statement_name,txn_date,original_description,normalized_description,amount,currency,bank,account_number,account_name,status,split_group_id) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',(str(i),'Synthetic source '+str(i%3),'2026-09-10','Synthetic fee '+chr(65+i%4),'SYNTHETIC FEE '+chr(65+i%4),(-1 if i%2 else 1)*Decimal('17.43'),'USD' if i%3 else 'EUR','Revolut' if i%4 else 'Safra',str(i%2),'Synthetic Owner '+str(i%3),'excluded' if i%7==0 else 'pending','synthetic-split' if i%7==0 and i%2 else ''))
            c.commit()
    def test_all_scopes_signs_exclusions_and_split_identical_to_live_oracle(self):
        self.seed();rows=[]
        for bank in ('Safra','Revolut','Unrelated'):
            for account in ('0','1','other',''):
                for owner in ('Synthetic Owner 0','Synthetic Owner 1',''):
                    for sign in (-1,1):
                        rows.append(dict(Date='2026-09-10',Description='Synthetic fee A',normalized_description='SYNTHETIC FEE A',Amount=sign*Decimal('17.43'),currency='EUR',bank=bank,account_number=account,account_name=owner))
        rows.append(dict(Date='invalid',Description='',Amount=None,currency='USD',bank='Safra',account_number='',account_name=''))
        frame=pd.DataFrame(rows);before=self.snapshot()
        pd.testing.assert_frame_equal(db.mark_duplicate_transactions(frame),self.oracle()(frame));self.assertEqual(self.snapshot(),before)
        for row in rows:
            single=pd.DataFrame([row]);pd.testing.assert_frame_equal(db.mark_duplicate_transactions(single),self.oracle()(single))
        self.assertEqual(self.snapshot(),before)
    def test_only_unreachable_lookup_variants_skipped(self):
        self.seed();frame=pd.DataFrame([dict(Date='2026-09-10',Description='Synthetic fee A',Amount=Decimal('-17.43'),currency='EUR',bank='Safra',account_number='0',account_name='Synthetic Owner 0')])
        with patch.object(db,'_existing_transaction_line_lookup',wraps=db._existing_transaction_line_lookup) as lookup:
            actual=db.mark_duplicate_transactions(frame);self.assertEqual(lookup.call_count,1)
        pd.testing.assert_frame_equal(actual,self.oracle()(frame))
        frame.loc[0,'bank']='Revolut';frame.loc[0,'account_number']='';frame.loc[0,'account_name']=''
        with patch.object(db,'_existing_transaction_line_lookup',wraps=db._existing_transaction_line_lookup) as lookup:
            actual=db.mark_duplicate_transactions(frame);self.assertEqual(lookup.call_count,6)
        pd.testing.assert_frame_equal(actual,self.oracle()(frame))
