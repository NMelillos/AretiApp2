"""Protect the one-call THIRD display change against the approved baseline."""
import ast
from _qa_revolut_business import _app_without_authorized_income_charity_edits
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pandas as pd

BASE = "078e52eef214113d0067f48decdd98fda5c5174f"


def main():
    baseline = subprocess.check_output(["git", "show", f"{BASE}:app.py"]).decode("utf-8")
    source = Path("app.py").read_text(encoding="utf-8")
    old, new = ast.parse(baseline), ast.parse(source)
    def function(tree, name):
        return next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == name)
    third = function(old, "render_third_link_report")
    calls = [n for n in third.body if isinstance(n, ast.Expr) and isinstance(n.value, ast.Call)
             and isinstance(n.value.func, ast.Name) and n.value.func.id == "_render_report_cutoff_notice"]
    assert len(calls) == 1
    third.body.remove(calls[0])
    compatible = ast.parse(_app_without_authorized_income_charity_edits(source))
    assert ast.dump(old) == ast.dump(compatible), "Only the THIRD notice call and exact approved editing patch may change"
    print("PASS: entire app protected except one THIRD notice call and the exact approved Income/Charity patch")
    for name in ("auth.py", "reporting.py", "db.py", "requirements.txt", "render.yaml"):
        expected = subprocess.check_output(["git", "show", f"{BASE}:{name}"])
        actual = Path(name).read_bytes().replace(b"\r\n", b"\n")
        if name == "reporting.py":
            from _qa_income_membership import protected_reporting
            actual = protected_reporting(actual)
        if name == "db.py":
            from safra_page_qa import protected_db
            actual = protected_db(actual)
            # Later CHF import correction changes only this currency token.
            token = b'    ("CHF", ("CHF",)),\n'
            assert actual.count(token) == 1
            actual = actual.replace(token, b"", 1)
        assert actual == expected.replace(b"\r\n", b"\n"), name
    current_third = function(new, "render_third_link_report")
    assert "_render_report_cutoff_notice" not in ast.unparse(current_third)
    for phrase in ("Report date notice:", "Latest reviewed transaction:", "To include them, update"):
        assert phrase not in ast.unparse(current_third)
    notice = function(new, "_render_report_cutoff_notice")
    messages = []
    ui = SimpleNamespace(warning=messages.append, info=messages.append)
    env = {"st": ui, "pd": pd, "_post_cutoff_reviewed_summary": lambda *a: {
        "count": 2, "latest_date": pd.Timestamp("2026-08-24").date()}}
    exec(compile(ast.Module(body=[notice], type_ignores=[]), "app.py", "exec"), env)
    env["_render_report_cutoff_notice"](pd.DataFrame(), pd.Timestamp("2026-07-31").date())
    assert len(messages) == 1
    for phrase in ("Report date notice:", "Latest reviewed transaction:", "To include them, update 'Report until' in Setup."):
        assert phrase in messages[0]
    print("PASS: unchanged Areti notice renders all three original message parts")
    rows = pd.DataFrame({"id": [1, 2, 3], "txn_date": pd.to_datetime([
        "2026-07-30", "2026-07-31", "2026-08-01"]), "amount_usd": [-100.25, 28.50, 900.00]})
    original = rows.copy(deep=True)
    results = []
    for tree in (ast.parse(baseline), new):
        fn = function(tree, "render_third_link_report")
        statements = [n for n in fn.body if isinstance(n, ast.Assign) and any(
            isinstance(t, ast.Name) and t.id in {"cutoff_ts", "filtered"} for t in n.targets)]
        assert len(statements) == 2
        env = {"pd": pd, "active_transactions": rows, "cutoff": pd.Timestamp("2026-07-31").date()}
        exec(compile(ast.Module(body=statements, type_ignores=[]), "app.py", "exec"), env)
        results.append(env["filtered"])
    pd.testing.assert_frame_equal(results[0], results[1])
    pd.testing.assert_frame_equal(rows, original)
    assert results[1].id.tolist() == [1, 2]
    assert results[1].amount_usd.sum() == -71.75
    print("PASS: cutoff boundary IDs [1,2], after-cutoff ID 3 excluded; before/after total -71.75; source unchanged")
    print("THIRD_CUTOFF_VISIBILITY_QA_COMPLETE")


if __name__ == "__main__":
    main()
