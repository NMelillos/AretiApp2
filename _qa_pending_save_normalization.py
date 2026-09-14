"""Synthetic Safra review saves: canonical snapshots and atomic conflicts."""
import ast
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from decimal import Decimal
import math
import hashlib
import os
from pathlib import Path
import tempfile
import subprocess
from threading import Barrier
from unittest.mock import patch


def without_save_normalization(name, source):
    source = source.replace(b"\r\n", b"\n")
    baseline = subprocess.check_output(["git", "show", "f735dfd04504d7bb54b99e890aab507a3eacdc29:" + name]).replace(b"\r\n", b"\n")
    function, digest = {
        "app.py": ("_prepare_pending_review_save_rows", "190431df08b51d845ff954db344ddd0b2e74d197c347df5a8958936c4489748e"),
        "db.py": ("save_reviewed_rows", "613c4d6ff4d547758bec979812df77246f20f316d8f66c6b33b30f6d68113388"),
    }[name]
    old, new = ast.parse(baseline), ast.parse(source)
    prior = next(n for n in old.body if isinstance(n, ast.FunctionDef) and n.name == function)
    current = next(n for n in new.body if isinstance(n, ast.FunctionDef) and n.name == function)
    assert hashlib.sha256(ast.dump(current).encode()).hexdigest() == digest
    new.body = [prior if n is current else n for n in new.body]
    assert ast.dump(old) == ast.dump(new), "Change outside exact reviewed save correction"
    return baseline


def prepare_function():
    import pandas as pd
    import db
    names = {"_parse_category_pair_label", "_category_pair_label", "_apply_category_pair_values",
             "_report_group_subcategory_key", "category_pair_report_group_maps",
             "add_report_group_column", "_refresh_category_pair_derived_columns",
             "_prepare_pending_review_save_rows"}
    tree = ast.parse(Path("app.py").read_text(encoding="utf-8"))
    env = dict(pd=pd, math=math, MAX_SAFE_FINANCIAL_AMOUNT=db.MAX_SAFE_FINANCIAL_AMOUNT,
               _CATEGORY_PAIR_COLUMN="category_subcategory", _NO_SUBCATEGORY_LABEL="No subcategory")
    exec(compile(ast.Module(body=[n for n in tree.body if isinstance(n, ast.FunctionDef)
                                 and n.name in names], type_ignores=[]), "app.py", "exec"), env)
    return env["_prepare_pending_review_save_rows"]


def main():
    import pandas as pd
    import db
    import parsing
    from safra_page_qa import fixture
    from _qa_safra_uat import labelled_accounts
    assert not db.USING_POSTGRES and Path(os.environ["TEMP"]).drive.upper() == "E:"
    prepare = prepare_function()
    with tempfile.TemporaryDirectory(dir=os.environ["TEMP"]) as root, patch.object(
            db, "DB_PATH", str(Path(root) / "review.sqlite")):
        db.init_db()
        db.add_category("Synthetic costs", "Service", "Operations")
        db.add_category("Synthetic costs", "Other service", "Operations")
        parsed = parsing._parse_safra_pages(fixture())
        accounts = labelled_accounts(parsed)
        with patch.object(db, "get_accounts", return_value=accounts):
            mapped = db.apply_account_and_rates(parsed, accounts.iloc[0].to_dict())
            assert db.save_pending_transactions(mapped, "synthetic.pdf", "review-normalization")[0] == 6
        categories = db.get_categories(include_subcategories=True)
        # A SQL NULL is represented by None, NaN or pandas NA depending on dtype/driver.
        with closing(db.get_connection()) as conn, conn:
            conn.execute("UPDATE classified_transactions SET category=NULL, subcategory=NULL, reviewed=NULL")

        def read():
            with closing(db.get_connection()) as conn:
                return pd.read_sql_query("SELECT * FROM classified_transactions ORDER BY id", conn)

        before = read()
        for missing, unchecked in [(None, None), (float("nan"), float("nan")),
                                    (pd.NA, pd.NA), ("", False), ("", 0), ("", "0"), ("", "false")]:
            original = before.astype(object).copy()
            original["category"] = missing
            original["subcategory"] = missing
            original["reviewed"] = unchecked
            original["amount"] = original.amount.map(lambda v: Decimal(str(v)))
            edited = original.iloc[::-1].drop(columns=["category", "subcategory"]).copy()
            edited["category_subcategory"] = "Synthetic costs / Service"
            edited["reviewed"] = True
            payload = prepare(original, edited, categories)
            assert payload._expected_category.eq("").all(), "NULL category became a display string in the concurrency snapshot"
            assert payload._expected_subcategory.eq("").all()
            assert payload._expected_reviewed.eq(False).all(), "Unchecked representation became True"
            pd.testing.assert_frame_equal(payload, prepare(original.copy(), edited.copy(), categories))
        assert db.save_reviewed_rows(payload) == 6
        after = read()
        assert after.category.eq("Synthetic costs").all() and after.subcategory.eq("Service").all()
        assert after.reviewed.eq(1).all() and after.status.eq("reviewed").all()
        protected = [c for c in before if c not in {"category", "subcategory", "reviewed", "status", "reviewed_at"}]
        pd.testing.assert_frame_equal(before[protected], after[protected])
        assert db.get_pending_transactions().empty and len(db.get_saved_transactions()) == 6
        assert Decimal(str(after.amount.sum())) == Decimal("-900.0")

        def reject_unchanged(stale):
            current = read()
            try:
                db.save_reviewed_rows(stale)
            except db.ConcurrentTransactionEditError:
                pass
            else:
                raise AssertionError("Real stale snapshot was accepted")
            pd.testing.assert_frame_equal(current, read())

        reject_unchanged(payload)  # Repeated submission cannot duplicate or overwrite.
        edited = after.copy()
        edited["category_subcategory"] = "Synthetic costs / Other service"
        edited["reviewed"] = True
        stale = prepare(after, edited, categories)
        with closing(db.get_connection()) as conn, conn:
            conn.execute("UPDATE classified_transactions SET subcategory='Other service' WHERE id=?", (int(after.id.iloc[-1]),))
        reject_unchanged(stale)  # Last-row conflict rolls back earlier writes.
        fresh = read()
        edited = fresh.copy()
        edited["category_subcategory"] = "Synthetic costs / Service"
        edited["reviewed"] = True
        retry = prepare(fresh, edited, categories)
        assert db.save_reviewed_rows(retry) == 6
        assert read().subcategory.eq("Service").all() and len(read()) == 6
        pd.testing.assert_frame_equal(before[protected], read()[protected])
        fresh = read()
        edited = fresh.copy()
        edited["category_subcategory"] = "Synthetic costs / Other service"
        edited["reviewed"] = True
        racing = prepare(fresh, edited, categories).iloc[:1].copy()
        barrier = Barrier(2)
        real_connection = db.get_connection

        class Cursor:
            def __init__(self, raw):
                self.raw = raw
                self.pause = False

            def execute(self, sql, params=()):
                self.pause = "SELECT category, subcategory, reviewed, status," in sql
                self.raw.execute(sql, params)
                return self

            def fetchone(self):
                value = self.raw.fetchone()
                if self.pause:
                    self.pause = False
                    barrier.wait(timeout=10)
                return value

            def __getattr__(self, name):
                return getattr(self.raw, name)

        class Connection:
            def __init__(self):
                self.raw = real_connection()

            def cursor(self):
                return Cursor(self.raw.cursor())

            def __getattr__(self, name):
                return getattr(self.raw, name)

        def competing_save(_):
            try:
                return db.save_reviewed_rows(racing.copy())
            except db.ConcurrentTransactionEditError:
                return "conflict"

        with patch.object(db, "get_connection", side_effect=Connection), ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(competing_save, range(2)))
        assert outcomes.count(1) == 1 and outcomes.count("conflict") == 1, "Overlapping snapshots both overwrote the row"
        assert len(read()) == 6
        pd.testing.assert_frame_equal(before[protected], read()[protected])
    print("PASS: six Safra rows, NULL/NaN/NA/boolean normalization, ID order, rerun, fresh reads, genuine conflict rollback, reload retry, duplicate submission and protected fields")


if __name__ == "__main__":
    main()
