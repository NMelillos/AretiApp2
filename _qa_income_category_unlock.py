"""Areti classification-only save: disposable DB, frozen source and Git evidence."""
import ast
from contextlib import closing
import hashlib
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import pandas as pd
import db
import reporting
from _qa_income_charity_edit import UI, Rerun, load_functions


class EditTests(unittest.TestCase):
    def setUp(self):
        self.assertFalse(db.USING_POSTGRES)
        self.tmp=tempfile.TemporaryDirectory(dir=os.environ['ARETI_ISOLATION_ROOT'])
        self.db_patch=patch.object(db,'DB_PATH',str(Path(self.tmp.name)/'classification.sqlite'))
        self.db_patch.start()
        db.init_db()
        for category,sub,group in (
            ('Interest Income','Old','GROUP-A'),('Interest Income','Same','GROUP-A'),
            ('Rental Income','Rent','GROUP-B'),('Bank Transfer','Income Distribution','Investments'),
            ('Distribution','Other','Income'),('Transfer','Internal','Assets'),
            ('Charity','Gift','Charity'),('Unmapped valid','Explicit',''),
        ):db.add_category(category,sub,group)
        with closing(db.get_connection()) as conn:
            conn.execute("INSERT INTO account_list(account_name,bank,account_number,currency,rate_type) VALUES('Synthetic holder','Synthetic bank','SYN-1','USD','USD/USD')")
            conn.execute("INSERT INTO statement_imports(id,statement_hash,statement_name,imported_at,transaction_count) VALUES(901,'synthetic-source','synthetic.pdf','2026-10-01T08:00:00+00:00',3)")
            conn.execute("INSERT INTO statement_balances(statement_hash,statement_name,account_name,bank,account_number,currency,period_start,period_end,opening_balance,closing_balance,imported_at,updated_at) VALUES('synthetic-source','synthetic.pdf','Synthetic holder','Synthetic bank','SYN-1','USD','2026-09-01','2026-09-30',100,476.35,'2026-10-01T08:00:00+00:00','2026-10-01T08:00:00+00:00')")
            for ident,amount,reviewed in ((1,'123.45',1),(2,'127.23',1),(3,'125.67',0)):
                conn.execute('''INSERT INTO classified_transactions
                    (id,statement_hash,statement_name,row_hash,txn_date,original_description,normalized_description,
                     amount,amount_usd,fx_rate,currency,rate_type,bank,account_name,account_number,category,subcategory,
                     reviewed,status,created_at,reviewed_at,dup_flag)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                    (ident,'synthetic-source','synthetic.pdf','synthetic-row-'+str(ident),'2026-09-20',
                     'Synthetic description '+str(ident),'synthetic description '+str(ident),amount,amount,'1',
                     'USD','USD/USD','Synthetic bank','Synthetic holder','SYN-1','Interest Income','Old',
                     reviewed,'reviewed' if reviewed else 'pending','2026-10-01T08:00:00+00:00',
                     '2026-10-02T08:00:00+00:00' if reviewed else None,0))
            conn.commit()
        self.env=self.functions(Path('app.py'))

    def tearDown(self):
        self.db_patch.stop()
        self.tmp.cleanup()

    def functions(self,path,ui=None):
        env=dict(pd=pd,hashlib=hashlib,st=ui or UI(),_CATEGORY_PAIR_COLUMN='category_subcategory',
                 _NO_SUBCATEGORY_LABEL='No subcategory',save_reviewed_rows=db.save_reviewed_rows,
                 get_categories=db.get_categories,_executive_signed_amount_series=lambda f:f.amount_usd,
                 _clear_transaction_read_caches=Mock())
        names={'_save_income_charity_edits','_category_pair_label','_parse_category_pair_label',
               '_category_pair_options','_with_category_pair_column'}
        if ui is not None:
            names|={'_editor_row_signature','_scoped_editor_key','_render_income_charity_editor','_render_income_charity_transactions'}
        load_functions(path.read_text(encoding='utf-8-sig'),names,env)
        return env

    def raw(self):
        with closing(db.get_connection()) as conn:
            return pd.read_sql_query('SELECT * FROM classified_transactions ORDER BY id',conn)

    def report(self):
        return reporting._assign_report_groups(self.raw(),db.get_categories(include_subcategories=True))

    def dump(self):
        with closing(db.get_connection()) as conn:return '\n'.join(conn.iterdump())

    def metadata(self):
        with closing(db.get_connection()) as conn:
            return {t:conn.execute('SELECT * FROM '+t+' ORDER BY 1').fetchall() for t in
                    ('statement_imports','statement_balances','account_list','rates','category_list','report_group_settings')}

    def edit(self,replacements,env=None,baseline=None,categories=None):
        env=env or self.env
        baseline=self.report() if baseline is None else baseline
        edited=env['_with_category_pair_column'](baseline.copy(deep=True))
        for ident,label in replacements.items():edited.loc[edited.id.eq(ident),'category_subcategory']=label
        return env['_save_income_charity_edits'](baseline,edited,
                db.get_categories(include_subcategories=True) if categories is None else categories)

    def assert_protected(self,before,metadata):
        pd.testing.assert_frame_equal(before.drop(columns=['category','subcategory','reviewed_at']),
                                      self.raw().drop(columns=['category','subcategory','reviewed_at']))
        self.assertEqual(self.metadata(),metadata)

    def test_live_baseline_rejects_valid_cross_group_and_writes_nothing(self):
        baseline=self.functions(Path(os.environ['ARETI_INCOME_EVIDENCE'])/'live-baseline-app.py')
        before=self.dump()
        with self.assertRaisesRegex(ValueError,'preserve Reporting Group'):
            self.edit({1:'Rental Income / Rent'},env=baseline)
        self.assertEqual(self.dump(),before)

    def test_pre_lock_git_source_allowed_cross_group(self):
        prior=self.functions(Path(os.environ['ARETI_INCOME_EVIDENCE'])/'pre-lock-app.py')
        self.assertEqual(self.edit({1:'Rental Income / Rent'},env=prior),1)
        self.assertEqual(self.report().set_index('id').loc[1,'report_group'],'GROUP-B')

    def test_a_same_group(self):
        before,meta=self.raw(),self.metadata()
        self.assertEqual(self.edit({1:'Interest Income / Same'}),1)
        self.assertEqual(self.report().set_index('id').loc[1,'report_group'],'GROUP-A')
        self.assert_protected(before,meta)

    def test_b_cross_group_follows_setup(self):
        before,meta=self.raw(),self.metadata()
        self.assertEqual(self.edit({1:'Rental Income / Rent'}),1)
        changed=self.report().set_index('id').loc[1]
        self.assertEqual((changed.category,changed.subcategory,changed.report_group),('Rental Income','Rent','GROUP-B'))
        self.assert_protected(before,meta)

    def test_pending_review_status_stays_pending(self):
        before,meta=self.raw(),self.metadata()
        self.assertEqual(self.edit({3:'Rental Income / Rent'}),1)
        self.assertEqual(self.raw().set_index('id').loc[3,'reviewed'],0)
        self.assertEqual(self.raw().set_index('id').loc[3,'status'],'pending')
        self.assert_protected(before,meta)

    def test_h_invalid_pair_batch_is_rejected_without_partial_writes(self):
        before=self.dump()
        with self.assertRaisesRegex(ValueError,'valid Category'):
            self.edit({1:'Rental Income / Rent',2:'Missing / Wrong'})
        self.assertEqual(self.dump(),before)

    def test_ambiguous_normalized_mapping_is_rejected_without_writes(self):
        db.add_category('rental income','rent','GROUP-C')
        before=self.dump()
        with self.assertRaisesRegex(ValueError,'ambiguous'):
            self.edit({1:'Rental Income / Rent'})
        self.assertEqual(self.dump(),before)

    def test_missing_mapping_column_is_unresolved_and_rejected(self):
        categories=db.get_categories(include_subcategories=True).drop(columns='report_group')
        before=self.dump()
        with self.assertRaisesRegex(ValueError,'Reporting Group mapping'):
            self.edit({1:'Rental Income / Rent'},categories=categories)
        self.assertEqual(self.dump(),before)

    def test_explicit_blank_group_uses_existing_unassigned_convention(self):
        self.assertEqual(self.edit({1:'Unmapped valid / Explicit'}),1)
        self.assertEqual(self.report().set_index('id').loc[1,'report_group'],reporting.UNASSIGNED_GROUP)

    def test_no_subcategory_ambiguity_rejected(self):
        db.add_category('Rental Income','Different','OTHER-GROUP')
        before=self.dump()
        with self.assertRaisesRegex(ValueError,'ambiguous'):
            self.edit({1:'Rental Income / No subcategory'})
        self.assertEqual(self.dump(),before)

    def test_i_forced_failure_rolls_back_entire_batch_audit_and_memory(self):
        with closing(db.get_connection()) as conn:
            conn.execute("CREATE TRIGGER fail_second BEFORE UPDATE ON classified_transactions WHEN NEW.id=2 BEGIN SELECT RAISE(ABORT,'synthetic forced failure'); END")
            conn.commit()
        before=self.dump()
        with self.assertRaisesRegex(RuntimeError,'Nothing was saved'):
            self.edit({1:'Rental Income / Rent',2:'Rental Income / Rent'})
        self.assertEqual(self.dump(),before)

    def test_stale_classification_rejected_and_batch_rolled_back(self):
        baseline=self.report()
        with closing(db.get_connection()) as conn:
            conn.execute("UPDATE classified_transactions SET subcategory='Same' WHERE id=2")
            conn.commit()
        before=self.dump()
        with self.assertRaises(db.ConcurrentTransactionEditError):
            self.edit({1:'Rental Income / Rent',2:'Rental Income / Rent'},baseline=baseline)
        self.assertEqual(self.dump(),before)

    def test_stale_review_status_rejected(self):
        baseline=self.report()
        with closing(db.get_connection()) as conn:
            conn.execute("UPDATE classified_transactions SET reviewed=0,status='pending' WHERE id=1")
            conn.commit()
        before=self.dump()
        with self.assertRaises(db.ConcurrentTransactionEditError):self.edit({1:'Rental Income / Rent'},baseline=baseline)
        self.assertEqual(self.dump(),before)

    def test_missing_transaction_is_rejected(self):
        baseline=self.report()
        with closing(db.get_connection()) as conn:
            conn.execute('DELETE FROM classified_transactions WHERE id=2');conn.commit()
        before=self.dump()
        with self.assertRaisesRegex(ValueError,'no longer exists'):
            self.edit({1:'Rental Income / Rent',2:'Rental Income / Rent'},baseline=baseline)
        self.assertEqual(self.dump(),before)

    def test_pair_deleted_after_preview_rejected_by_backend(self):
        cats=db.get_categories(include_subcategories=True)
        with closing(db.get_connection()) as conn:
            conn.execute("DELETE FROM category_list WHERE category='Rental Income'");conn.commit()
        before=self.dump()
        with self.assertRaisesRegex(ValueError,'Invalid Category'):
            self.edit({1:'Interest Income / Same',2:'Rental Income / Rent'},categories=cats)
        self.assertEqual(self.dump(),before)

    def test_j_edited_financial_fields_never_forwarded(self):
        before,meta=self.raw(),self.metadata()
        base=self.report();edited=self.env['_with_category_pair_column'](base.copy())
        edited.loc[edited.id.eq(1),'category_subcategory']='Rental Income / Rent'
        edited['amount']=-999999;edited['txn_date']='1900-01-01';edited['reviewed']=0
        self.assertEqual(self.env['_save_income_charity_edits'](base,edited,db.get_categories(include_subcategories=True)),1)
        self.assert_protected(before,meta)

    def test_k_split_classification_does_not_change_allocations_or_identities(self):
        db.split_transaction(3,[dict(category='Interest Income',subcategory='Old',amount='50.00'),
                                dict(category='Interest Income',subcategory='Same',amount='75.67')])
        before,meta=self.raw(),self.metadata()
        child=int(before.loc[before.split_parent_id.eq(3),'id'].iloc[0])
        base=self.report().query('id == @child')
        self.assertEqual(self.edit({child:'Rental Income / Rent'},baseline=base),1)
        self.assert_protected(before,meta)
        self.assertEqual(self.raw().loc[self.raw().split_parent_id.eq(3),'amount'].sum(),125.67)

    def test_split_amount_edit_guard_still_rejects(self):
        db.split_transaction(3,[dict(category='Interest Income',subcategory='Old',amount='50.00'),
                                dict(category='Interest Income',subcategory='Same',amount='75.67')])
        child=self.raw().loc[lambda f:f.split_parent_id.eq(3)].iloc[0]
        before=self.dump()
        with self.assertRaisesRegex(ValueError,'linked to a split'):
            db.save_reviewed_rows(pd.DataFrame([dict(id=child.id,category=child.category,subcategory=child.subcategory,
                                                   reviewed=False,status='pending',amount='49.00')]))
        self.assertEqual(self.dump(),before)

    def test_income_financial_field_guard_still_rejects(self):
        before=self.dump()
        with self.assertRaisesRegex(ValueError,'cannot change financial'):
            db.save_reviewed_rows(pd.DataFrame([dict(id=1,amount='99')]),income_edit=True)
        self.assertEqual(self.dump(),before)

    def test_ui_save_round_trip_and_cancel(self):
        ui=UI();env=self.functions(Path('app.py'),ui)
        before,meta=self.raw(),self.metadata()
        ui.action='Cancel'
        ui.transform=lambda f:f.assign(category_subcategory='Rental Income / Rent')
        with self.assertRaises(Rerun):env['_render_income_charity_transactions'](self.report().iloc[:1],editable=True)
        pd.testing.assert_frame_equal(self.raw(),before)
        ui.action='Save'
        with self.assertRaises(Rerun):env['_render_income_charity_transactions'](self.report().iloc[:1],editable=True)
        self.assertFalse(ui.errors)
        self.assertEqual(self.report().set_index('id').loc[1,'report_group'],'GROUP-B')
        self.assert_protected(before,meta)

    def test_no_change_repeat_save_is_noop(self):
        self.edit({1:'Rental Income / Rent'})
        before=self.dump()
        self.assertEqual(self.edit({}),0)
        self.assertEqual(self.dump(),before)


class MembershipTests(unittest.TestCase):
    def check(self,category,sub,group,expected):
        frame=pd.DataFrame([dict(id=1,category=category,subcategory=sub,report_group=group,amount_usd=123)])
        before=frame.copy(deep=True)
        self.assertEqual(reporting.is_income(category,sub,group),expected)
        self.assertEqual(reporting.income_charity_scope(frame).id.tolist(),[1] if expected else [])
        pd.testing.assert_frame_equal(frame,before)

    def test_c_income_via_category(self):self.check('Interest Income','Interest','Investments',True)
    def test_d_income_via_subcategory(self):self.check('Bank Transfer','Income Distribution','Investments',True)
    def test_e_income_via_group(self):self.check('Distribution','Other','Income',True)
    def test_f_no_income(self):self.check('Transfer','Internal','Assets',False)
    def test_g_case_insensitive_whole_word(self):
        for value in ('Income','INCOME','income'):
            for values in ((value,'Other','Investments'),('Transfer',value,'Investments'),('Transfer','Other',value)):
                self.check(*values,True)
        self.check('Incoming','incomeable','incomes',False)


class SourceScopeTests(unittest.TestCase):
    def test_only_report_save_helper_changes(self):
        baseline=Path(os.environ['ARETI_INCOME_BASELINE'])
        old=ast.parse((baseline/'app.py').read_text(encoding='utf-8-sig'))
        new=ast.parse(Path('app.py').read_text(encoding='utf-8-sig'))
        prior=next(n for n in old.body if isinstance(n,ast.FunctionDef) and n.name=='_save_income_charity_edits')
        new.body=[prior if isinstance(n,ast.FunctionDef) and n.name==prior.name else n for n in new.body]
        self.assertEqual(ast.dump(old),ast.dump(new))
        for source in baseline.glob('*.py'):
            if source.name=='app.py' or source.name.startswith('_qa'):continue
            self.assertEqual(source.read_text(encoding='utf-8-sig'),Path(source.name).read_text(encoding='utf-8-sig'),source.name)
        self.assertEqual(Path(db.__file__).resolve(),Path('db.py').resolve())


if __name__=='__main__':unittest.main(verbosity=2)
