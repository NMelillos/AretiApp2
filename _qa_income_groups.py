"""Isolated Income visibility, stored group mapping and edit preservation."""
import ast
import hashlib
from contextlib import closing
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from unittest.mock import patch


def compatible(name, actual):
    actual = actual.replace(b"\r\n", b"\n")
    hashes = {"app.py": "2daf0c8779715640b8a3deff28c8cb54f4b6dc5163a48987ef53872516b96e64",
              "reporting.py": "f976cfbecb137cdb523260ca40e37ce20a28c052099cb8b760e3dc048d71565a"}
    assert hashlib.sha256(actual).hexdigest() == hashes[name]
    baseline = subprocess.check_output(['git','show','267ecb82669f44b87aa6d76ea92abd8143cf0654:'+name]).replace(b"\r\n", b"\n")
    function = {'app.py':'_save_income_charity_edits', 'reporting.py':'third_hierarchy_item19_exclusions'}[name]
    old, new = ast.parse(baseline), ast.parse(actual)
    prior = next(n for n in old.body if isinstance(n, ast.FunctionDef) and n.name == function)
    new.body = [prior if isinstance(n, ast.FunctionDef) and n.name == function else n for n in new.body]
    assert ast.dump(old) == ast.dump(new), 'Unrelated application change'
    return baseline


def main():
    import pandas as pd
    import db
    import reporting
    from _qa_income_charity_edit import load_functions
    assert not db.USING_POSTGRES and Path(os.environ["TEMP"]).drive.upper() == "E:"
    with tempfile.TemporaryDirectory(dir=os.environ["TEMP"]) as root:
        with patch.object(db, "DB_PATH", str(Path(root) / "synthetic.sqlite")):
            db.init_db()
            for sub in ("Walt Disney", "Other", "Todd Regan"):
                db.add_category("Tour Income", sub, "Woking Way LLC")
            db.add_category("Other Income", "Different group", "Other group")
            with closing(db.get_connection()) as conn, conn:
                for i, sub in enumerate(("Walt Disney", "Other", "Todd Regan"), 1):
                    conn.execute('''INSERT INTO classified_transactions
                        (id,row_hash,txn_date,amount,amount_usd,currency,category,subcategory,
                         original_description,normalized_description,reviewed,status)
                        VALUES (?,?,?,?,?,?,?,?,?,?,?,?)''',
                        (i, 'synthetic-'+str(i), '2026-01-01', i*100, i*100, 'USD',
                         'Tour Income', sub, 'Synthetic '+str(i), 'synthetic '+str(i), 1, 'reviewed'))
            def read():
                with closing(db.get_connection()) as conn:
                    return pd.read_sql_query('SELECT * FROM classified_transactions ORDER BY id', conn)
            categories = db.get_categories(include_subcategories=True)
            raw = read()
            _, report, months, _ = reporting._prepare_report_data(raw, categories, include_all_valid=True)
            scoped, monthly, _ = reporting.income_charity_month_values(report, months)
            assert scoped.id.tolist() == [1, 2, 3] and scoped.report_group.eq('Woking Way LLC').all()
            assert sum(monthly['Income'].values()) == 600
            env = dict(pd=pd, _CATEGORY_PAIR_COLUMN='category_subcategory', _NO_SUBCATEGORY_LABEL='No subcategory',
                       save_reviewed_rows=db.save_reviewed_rows)
            names = {'_third_report_group_scope', '_save_income_charity_edits', '_category_pair_options',
                     '_category_pair_label', '_parse_category_pair_label', '_with_category_pair_column'}
            load_functions(Path('app.py').read_text(encoding='utf-8'), names, env)
            third, _ = env['_third_report_group_scope'](report, categories)
            print('Income rows:', len(scoped), 'THIRD Woking rows:', len(third))
            if '--baseline' in sys.argv:
                assert third.empty
                edited = env['_with_category_pair_column'](report.iloc[:1].copy())
                edited['category_subcategory'] = 'Other Income / Different group'
                assert env['_save_income_charity_edits'](report.iloc[:1], edited, categories) == 1
                changed = reporting._assign_report_groups(read(), categories)
                assert changed.iloc[0].report_group == 'Other group'
                print('BASELINE REPRODUCED: THIRD hides all three; cross-group Income edit changes derived Reporting Group')
                return
            assert third.id.tolist() == [1, 2, 3] and third.report_group.eq('Woking Way LLC').all()
            assert third.id.is_unique and third.report_amount.sum() == report.report_amount.sum() == 600
            edited = env['_with_category_pair_column'](report.copy())
            edited['category_subcategory'] = ['Tour Income / Other', 'Tour Income / Todd Regan', 'Tour Income / Walt Disney']
            assert env['_save_income_charity_edits'](report, edited, categories) == 3
            after = read()
            assert after.reviewed_at.notna().all()
            pd.testing.assert_frame_equal(raw.drop(columns=['category','subcategory','reviewed_at']), after.drop(columns=['category','subcategory','reviewed_at']))
            mapped = reporting._assign_report_groups(after.copy(), db.get_categories(include_subcategories=True))
            assert mapped.report_group.eq('Woking Way LLC').all()
            blocked = env['_with_category_pair_column'](mapped.copy())
            blocked['category_subcategory'] = ['Tour Income / Walt Disney', 'Other Income / Different group', 'Tour Income / Other']
            try:
                env['_save_income_charity_edits'](mapped, blocked, categories)
            except ValueError:
                pass
            else:
                raise AssertionError('Cross-group edit accepted')
            pd.testing.assert_frame_equal(after, read())
            assert not reporting.is_income('Incoming', 'incomeable')
            assert not reporting.is_income('NoIncome', 'incomes')
            pd.testing.assert_frame_equal(categories, db.get_categories(include_subcategories=True))
    print('PASS: stored mappings preserved, Income/Executive/Areti/THIRD rows and totals once each, same-group Save, atomic cross-group rejection')


if __name__ == '__main__':
    main()
