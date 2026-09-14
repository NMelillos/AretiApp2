"""Three-field Income membership, persisted edits and protected source scope."""
import ast
import hashlib
from contextlib import closing
import os
from pathlib import Path
import subprocess
import tempfile
from unittest.mock import patch


def without_three_field_change(name, actual):
    actual = actual.replace(b"\r\n", b"\n")
    expected = {"app.py": "dafeb1c49bd293f3fb553d7a74e8aec6090045a402c0dbb8f364befd1d73140f",
                "reporting.py": "b49b68ad307c78f6cdb696d6a6d36045c6ad7aed03f43968ee2b46d1a40e9982"}
    assert hashlib.sha256(actual).hexdigest() == expected[name], "Unreviewed source change"
    baseline = subprocess.check_output(["git", "show", "9a12e21d73e9e414c561aea74cf176de5e2be6b3:" + name]).replace(b"\r\n", b"\n")
    allowed = {"app.py": {"_save_income_charity_edits"},
               "reporting.py": {"is_income", "income_charity_scope"}}[name]
    old, new = ast.parse(baseline), ast.parse(actual)
    prior = {n.name: n for n in old.body if isinstance(n, ast.FunctionDef) and n.name in allowed}
    new.body = [prior[n.name] if isinstance(n, ast.FunctionDef) and n.name in prior else n for n in new.body]
    assert ast.dump(old) == ast.dump(new), "Unrelated application change"
    return baseline


def main():
    import pandas as pd
    import db
    import reporting
    from _qa_income_charity_edit import load_functions

    cases = [
        ("Projects", "MISSING", "Income", True),
        ("Tour Income", "Other", "Woking Way LLC", True),
        ("Other", "Rental Income", "Other", True),
        ("Income", "Income", "Income", True),
        ("Projects", "Other", "Other", False),
        (None, "MISSING", "", False),
        (" ", None, "  rEnTaL InCoMe  ", True),
        ("Incoming", "incomeable", "incomes", False),
    ]
    frame = pd.DataFrame([dict(id=i, category=c, subcategory=s, report_group=g,
                              original_description="Income", amount_usd=100,
                              txn_date="2026-01-01")
                          for i, (c, s, g, _) in enumerate(cases)])
    expected = [i for i, case in enumerate(cases) if case[3]]
    scoped = reporting.income_charity_scope(frame)
    assert scoped.id.tolist() == expected, "Income OR rule must include Reporting Group"
    assert scoped.id.is_unique and scoped.amount_usd.sum() == len(expected) * 100
    assert not db.USING_POSTGRES
    assert Path(os.environ["TEMP"]).drive.upper() == "E:"
    with tempfile.TemporaryDirectory(dir=os.environ["TEMP"]) as root:
        with patch.object(db, "DB_PATH", str(Path(root) / "synthetic.sqlite")):
            db.init_db()
            db.add_category("Income", "Original", "Income")
            db.add_category("Projects", "MISSING", "Income")
            categories = db.get_categories(include_subcategories=True)
            with closing(db.get_connection()) as conn, conn:
                conn.execute("""INSERT INTO classified_transactions
                    (row_hash,txn_date,amount,amount_usd,currency,category,subcategory,
                     original_description,reviewed,status)
                    VALUES ('three-field-synthetic','2026-01-01',125,125,'USD',
                            'Income','Original','Synthetic',1,'reviewed')""")
            def read():
                with closing(db.get_connection()) as conn:
                    return pd.read_sql_query("SELECT * FROM classified_transactions", conn)
            before = read()
            _, report, months, _ = reporting._prepare_report_data(before, categories, include_all_valid=True)
            env = dict(pd=pd, _CATEGORY_PAIR_COLUMN="category_subcategory",
                       _NO_SUBCATEGORY_LABEL="No subcategory", save_reviewed_rows=db.save_reviewed_rows)
            names = {"_save_income_charity_edits", "_category_pair_options", "_category_pair_label",
                     "_parse_category_pair_label", "_with_category_pair_column"}
            load_functions(Path("app.py").read_text(encoding="utf-8"), names, env)
            edited = env["_with_category_pair_column"](report.copy())
            edited["category_subcategory"] = "Projects / MISSING"
            assert env["_save_income_charity_edits"](report, edited, categories) == 1
            after = read()
            pd.testing.assert_frame_equal(before.drop(columns=["category", "subcategory", "reviewed_at"]),
                                          after.drop(columns=["category", "subcategory", "reviewed_at"]))
            _, refreshed, months, _ = reporting._prepare_report_data(after, categories, include_all_valid=True)
            scoped, monthly, _ = reporting.income_charity_month_values(refreshed, months)
            assert len(scoped) == 1 and scoped.report_group.eq("Income").all()
            assert scoped.category.unique().tolist() == ["Projects"]
            assert scoped.subcategory.unique().tolist() == ["MISSING"]
            assert sum(monthly["Income"].values()) == 125
            assert reporting.income_charity_scope(refreshed.assign(report_group="Other")).empty
    print("PASS: three-field whole-word OR, no duplicates, protected fields, Save/reload, Projects hierarchy and total 125")


if __name__ == "__main__":
    main()
