"""Approved Family Charity hierarchy parity; no production writes."""
import ast,json,os,unittest
from contextlib import closing
from decimal import Decimal
from pathlib import Path
import pandas as pd
import db
from financial_decimal import exact_sum,cents
from reporting import _prepare_report_data,income_charity_scope,third_hierarchy_item19_exclusions
from _qa_post_import_message import CompletionTests


def functions():
    tree=ast.parse(Path('app.py').read_text(encoding='utf-8-sig'))
    names={'_third_report_group_scope','_executive_month_window','_executive_metric_values',
           '_executive_metric_values_from_month_values','_executive_amount_series','_executive_signed_amount_series',
           '_executive_status_delta','_executive_status_change_pct','_executive_trend'}
    env={'pd':pd}
    exec(compile(ast.Module(body=[n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name in names],type_ignores=[]),'actual-report-functions','exec'),env)
    return env


class FamilyTests(CompletionTests):
    def setup_data(self):
        return pd.DataFrame([('Lifestyle','General','Family expenses'),('Charity','Gift','Family expenses'),
          ('Charity','Other','Business expenses'),('Interest Income','Credit','Family expenses'),
          ('Travel','','Family expenses')],columns=['category','subcategory','report_group'])

    def rows(self):
        base=dict(txn_date='2026-09-01',amount=Decimal('-10'),amount_usd=Decimal('-10'),currency='USD',category='Lifestyle',subcategory='General',reviewed=1,status='reviewed',split_parent_id=None,split_group_id=None,split_allocation_index=None)
        return pd.DataFrame([dict(base,id=1),dict(base,id=2,category='Charity',subcategory='Gift',amount=Decimal('-20'),amount_usd=Decimal('-20')),dict(base,id=3,reviewed=0,status='pending'),dict(base,id=4,amount=Decimal('3'),amount_usd=Decimal('3'))])

    def compare(self,rows,categories=None):
        rows=rows.copy();rows['txn_date']=pd.to_datetime(rows.txn_date,errors='coerce')
        rows=rows[rows.txn_date.between(pd.Timestamp('2026-01-01'),pd.Timestamp('2026-09-30'))]
        categories=self.setup_data() if categories is None else categories
        _,prepared,_,_=_prepare_report_data(rows,categories,include_own_funds=True,include_all_valid=True)
        env=functions();third,_=env['_third_report_group_scope'](prepared,categories)
        own=prepared[prepared.report_group.str.casefold().isin(['family expenses','1-family'])]
        other=third[third.report_group.str.casefold().isin(['family expenses','1-family'])]
        self.assertEqual(set(own.id),set(other.id))
        months=env['_executive_month_window'](pd.Period('2026-09','M'))
        a=env['_executive_metric_values'](own,months);b=env['_executive_metric_values'](other,months)
        self.assertEqual(a,b)
        return own,other,a

    def test_same_qualifying_transactions_and_signed_totals(self):
        own,third,metrics=self.compare(self.rows())
        self.assertEqual(set(own.id),{1,2,3,4});self.assertEqual(metrics['total'],Decimal('-37'))

    def test_split_parent_excluded_children_not_double_counted(self):
        rows=self.rows().iloc[:0].copy()
        seed=self.rows().iloc[0].to_dict()
        rows=pd.DataFrame([dict(seed,id=10,amount=Decimal('-100'),amount_usd=Decimal('-100'),split_group_id='G'),dict(seed,id=11,amount=Decimal('-40'),amount_usd=Decimal('-40'),split_group_id='G',split_parent_id=10,split_allocation_index=0),dict(seed,id=12,category='Charity',subcategory='Gift',amount=Decimal('-60'),amount_usd=Decimal('-60'),split_group_id='G',split_parent_id=10,split_allocation_index=1)])
        own,_,m=self.compare(rows);self.assertEqual(set(own.id),{11,12});self.assertEqual(m['total'],Decimal('-100'))

    def test_reviewed_unreviewed_and_excluded_status(self):
        rows=self.rows();rows.loc[rows.id.eq(1),'status']='excluded'
        own,_,m=self.compare(rows)
        self.assertEqual(set(own.id),{2,3,4});self.assertIn(0,own.reviewed.tolist());self.assertEqual(m['total'],Decimal('-27'))

    def test_null_subcategory_setup_fallback_and_unmapped_not_family(self):
        rows=self.rows();rows.loc[rows.id.eq(1),['category','subcategory']]=['Travel',None]
        rows.loc[rows.id.eq(3),['category','subcategory']]=[None,None]
        own,_,_=self.compare(rows);self.assertEqual(set(own.id),{1,2,4})

    def test_date_cutoff_and_prior_year_do_not_leak_into_ytd(self):
        rows=self.rows();rows.loc[rows.id.eq(1),'txn_date']='2025-09-01';rows.loc[rows.id.eq(3),'txn_date']='2026-10-01'
        own,_,m=self.compare(rows);self.assertEqual(set(own.id),{2,4});self.assertEqual(m['total'],Decimal('-17'))

    def test_income_charity_analysis_and_other_groups_preserved(self):
        rows=self.rows();rows.loc[rows.id.eq(3),['category','subcategory']]=['Charity','Other']
        rows.loc[rows.id.eq(4),['category','subcategory']]=['Interest Income','Credit']
        _,prepared,_,_=_prepare_report_data(rows,self.setup_data(),include_own_funds=True,include_all_valid=True)
        original_analysis=income_charity_scope(prepared)
        before=prepared.copy(deep=True)
        third,third_categories=functions()['_third_report_group_scope'](prepared,self.setup_data())
        self.assertIn(2,third.id.tolist());self.assertNotIn(3,third.id.tolist());self.assertIn(4,third.id.tolist())
        self.assertTrue(any(third_categories.category.eq('Charity') & third_categories.report_group.eq('Family expenses')))
        pd.testing.assert_frame_equal(prepared,before)
        pd.testing.assert_frame_equal(income_charity_scope(prepared),original_analysis)
        self.assertEqual(set(original_analysis.id),{2,3,4})

    def test_actual_editor_backend_category_edit_preserves_financial_values_and_both_views(self):
        for cat,sub,group in self.setup_data().itertuples(index=False,name=None):db.add_category(cat,sub,group)
        with closing(db.get_connection()) as c:
            c.execute("INSERT INTO classified_transactions(id,row_hash,txn_date,amount,amount_usd,currency,category,subcategory,reviewed,status,original_description,account_name) VALUES(101,'synthetic-family','2026-09-01',-10,-10,'USD','Lifestyle','General',0,'pending','Synthetic family edit','Synthetic')");c.commit()
        before=db.get_all_transactions().copy(deep=True)
        edits=pd.DataFrame([dict(id=101,category='Charity',subcategory='Gift',reviewed=False,status='pending',_expected_category='Lifestyle',_expected_subcategory='General',_expected_reviewed=False)])
        self.assertEqual(db.update_database_rows(edits),1)
        after=db.get_all_transactions();self.assertEqual(after.iloc[0].amount,before.iloc[0].amount);self.assertEqual(after.iloc[0].amount_usd,before.iloc[0].amount_usd)
        own,_,m=self.compare(after,db.get_categories(include_subcategories=True));self.assertEqual(own.iloc[0].category,'Charity');self.assertEqual(m['total'],Decimal('-10'))

    def test_legacy_family_label_and_other_charity_policy(self):
        categories=self.setup_data().replace({'report_group':{'Family expenses':'1-family'}})
        own,_,m=self.compare(self.rows(),categories);self.assertEqual(len(own),4);self.assertEqual(m['total'],Decimal('-37'))

    def test_same_snapshot_production_rows_reconcile_without_writes(self):
        root=Path(os.environ['ARETI_ISOLATION_ROOT']);snap=json.loads((root/'family-readonly-snapshot-private.json').read_text())
        self.assertEqual(snap['read_only'],'on')
        tx=db._filter_hidden_transactions(pd.DataFrame(snap['transactions']))
        rows,other,metrics=self.compare(tx,pd.DataFrame(snap['categories']))
        expected=json.loads((root/'family-reconciliation-summary-private.json').read_text())
        self.assertEqual(len(rows),expected['rows'])
        self.assertEqual(cents(metrics['total']),cents(Decimal(expected['areti_total'])))
        old_excluded=third_hierarchy_item19_exclusions(rows)
        self.assertEqual(len(old_excluded),expected['areti_only'])
        self.assertEqual(cents(exact_sum(old_excluded.report_amount)),cents(Decimal(expected['explained_signed_difference'])))


if __name__=='__main__':
    suite=unittest.TestSuite(FamilyTests(name) for name in FamilyTests.__dict__ if name.startswith('test_'))
    result=unittest.TextTestRunner(verbosity=2).run(suite)
    Path(os.environ['ARETI_ISOLATION_ROOT'],'family-test-counts.json').write_text(json.dumps(dict(tests=result.testsRun,failures=len(result.failures),errors=len(result.errors),skipped=len(result.skipped))))
    raise SystemExit(not result.wasSuccessful())
