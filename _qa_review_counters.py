"""Read-only counter equivalence over synthetic persisted review states."""
import ast
import hashlib
from contextlib import closing
import os
from pathlib import Path
import tempfile
import subprocess
from unittest.mock import patch


def without_counter_change(name, source):
    from _qa_analytical_widths import without_analytical_sizing
    source = without_analytical_sizing(name, source)
    source = source.replace(b"\r\n", b"\n")
    expected = {"app.py": "04fb94a28ce7d248d4401819937a4ffcf62094e4a4ca8226039e0fdeacf04487",
                "db.py": "c14c030bca9b4fcffad8b33aa7e49c99106dcdc5072e114a8cf4b8368f4f2d40"}
    assert hashlib.sha256(source).hexdigest() == expected[name]
    baseline = subprocess.check_output(["git", "show", "554a804e351fad56c9bc9e3bc3dc8af86c918325:" + name]).replace(b"\r\n", b"\n")
    old, new = ast.parse(baseline), ast.parse(source)
    if name == "db.py":
        changed = b"before_reviewed = int(_bool_from_value(before[2]))"
        assert source.count(changed) == 1
        new = ast.parse(source.replace(changed, b"before_reviewed = int(before[2] or 0)", 1))
        allowed = {"get_pending_transactions", "get_saved_transactions", "get_dashboard_counts"}
        prior = {n.name: n for n in old.body if isinstance(n, ast.FunctionDef) and n.name in allowed}
        new.body = [prior[n.name] if isinstance(n, ast.FunctionDef) and n.name in prior else n for n in new.body]
        assert ast.dump(old) == ast.dump(new)
    else:
        added = ('        from review_state import counts as review_state_counts\n'
                 '        review_counts = review_state_counts(active_financial_transactions(db_view))\n')
        assert source.decode().count(added) == 1
        restored = source.decode().replace(added, "", 1)
        for label, key, fill in [("Pending", "pending", "pending"), ("Reviewed", "reviewed", "")]:
            token = f'("{label}", review_counts["{key}"])'
            original = f'("{label}", int((db_view["status"].fillna("{fill}") == "{key}").sum()) if "status" in db_view else 0)'
            assert restored.count(token) == 1
            restored = restored.replace(token, original, 1)
        assert restored.encode() == baseline
    return baseline


def database_summary(frame):
    import pandas as pd
    import db
    tree = ast.parse(Path("app.py").read_text(encoding="utf-8"))
    call, = [n for n in ast.walk(tree) if isinstance(n, ast.Call)
             and isinstance(n.func, ast.Name) and n.func.id == "render_summary_strip"
             and "('Visible rows', len(db_view))" in ast.unparse(n)]
    env = dict(pd=pd, db_view=frame, excluded_total=0, render_summary_strip=dict)
    if any(isinstance(n, ast.Name) and n.id == "review_counts" for n in ast.walk(call)):
        from review_state import counts
        env["review_counts"] = counts(db.filter_financially_active_transactions(frame))
    return eval(compile(ast.Expression(call), "app.py", "eval"), env)


def main():
    import pandas as pd
    import db
    assert not db.USING_POSTGRES and Path(os.environ["TEMP"]).drive.upper() == "E:"
    with tempfile.TemporaryDirectory(dir=os.environ["TEMP"]) as root, patch.object(
            db, "DB_PATH", str(Path(root) / "counters.sqlite")):
        db.init_db()
        def insert(states):
            with closing(db.get_connection()) as conn, conn:
                start = conn.execute("SELECT COUNT(*) FROM classified_transactions").fetchone()[0]
                for i, (status, reviewed) in enumerate(states, start):
                    conn.execute("""INSERT INTO classified_transactions
                        (row_hash,txn_date,amount,amount_usd,currency,account_name,status,reviewed)
                        VALUES (?, '2026-01-01', -10, -10, 'USD', 'Synthetic visible account', ?, ?)""",
                        (f"counter-{i}", status, reviewed))
        insert([("pending", 1)] * 12 + [("reviewed", 1)] * 3)
        top = db.get_dashboard_counts()
        summary = database_summary(db.get_all_transactions())
        assert (summary["Pending"], summary["Reviewed"]) == (top["pending"], top["reviewed"]), "Database status-only counters disagree with effective review state"
        assert (top["pending"], top["reviewed"]) == (0, 15)
        insert([(None, None), ("", ""), ("pending", False), ("pending", 0),
                ("pending", "false"), ("pending", "0"), ("pending", True),
                ("pending", "true"), ("reviewed", None), ("excluded", 1)])
        with closing(db.get_connection()) as conn, conn:
            conn.execute("UPDATE classified_transactions SET split_group_id='synthetic-split' WHERE id=1")
            conn.execute("UPDATE classified_transactions SET split_parent_id=1, split_group_id='synthetic-split', split_allocation_index=1 WHERE id=2")
        raw = db.get_all_transactions()
        protected = raw.copy(deep=True)
        active = db.filter_financially_active_transactions(raw)
        top = db.get_dashboard_counts()
        summary = database_summary(active)
        assert (top["pending"], top["reviewed"]) == (6, 17)
        assert len(db.get_pending_transactions()) == 6 and len(db.get_saved_transactions()) == 17
        assert summary["Pending"] + summary["Reviewed"] == summary["Visible rows"] == 23
        visible = active[active.id.gt(15)]
        selected = database_summary(visible)
        assert (selected["Pending"], selected["Reviewed"]) == (6, 3)
        assert selected["Accounts"] == 1
        # Configured accounts are deliberately not the count of visible account names.
        with closing(db.get_connection()) as conn, conn:
            for i in range(3):
                conn.execute("INSERT INTO account_list (account_name,bank,account_number,currency,rate_type) VALUES (?, 'Synthetic', ?, 'USD', 'USD/USD')", (f"Setup {i}", f"fake-{i}"))
        assert db.get_dashboard_counts()["accounts"] == 3
        pd.testing.assert_frame_equal(protected, db.get_all_transactions())
        with patch.dict(os.environ, {"HIDDEN_TRANSACTION_IDS": "2"}):
            assert db.get_dashboard_counts()["reviewed"] == 16
            assert database_summary(db.filter_financially_active_transactions(db.get_all_transactions()))["Reviewed"] == 16
        from _qa_pending_save_normalization import prepare_function
        db.add_category("Synthetic", "Checked", "Operations")
        pending = db.get_pending_transactions()
        edited = pending.drop(columns=["category", "subcategory"]).copy()
        edited["category_subcategory"] = "Synthetic / Checked"
        edited["reviewed"] = True
        payload = prepare_function()(pending, edited, db.get_categories(include_subcategories=True))
        assert db.save_reviewed_rows(payload) == 6
        assert db.get_pending_transactions().empty
        assert db.get_dashboard_counts()["reviewed"] == 23
        columns = [c for c in protected if c not in {"category", "subcategory", "reviewed", "status", "reviewed_at"}]
        pd.testing.assert_frame_equal(protected[columns], db.get_all_transactions()[columns])
    print("PASS: global versus visible scope, Pending+Reviewed partition, NULL/boolean/string states, exclusions, split parent/child, hidden IDs, fresh reads, post-save refresh and separate Setup account count; financial values unchanged")


if __name__ == "__main__":
    main()
