"""Synthetic whole-word Income membership and authorized drill-down regression."""
import ast
import hashlib
import subprocess
from contextlib import nullcontext
from html import escape
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pandas as pd
from _qa_income_charity_edit import load_functions


def protected_reporting(actual):
    from _qa_income_groups import compatible
    actual = compatible('reporting.py', actual)
    actual = actual.replace(b"\r\n", b"\n")
    assert hashlib.sha256(actual).hexdigest() == "a57f7cc80c74de1e9998c7587b4b3fd41e385025ba7a757ecff88edcbf520d88"
    baseline = subprocess.check_output(["git", "show", "5ee9b76c36ce4b311f92ae8350895dcae6a728bc:reporting.py"]).replace(b"\r\n", b"\n")
    old, new = ast.parse(baseline), ast.parse(actual)
    prior = next(n for n in old.body if isinstance(n, ast.FunctionDef) and n.name == "income_charity_scope")
    new.body = [prior if isinstance(n, ast.FunctionDef) and n.name == "income_charity_scope" else n
                for n in new.body if not (isinstance(n, ast.FunctionDef) and n.name == "is_income")]
    assert ast.dump(new) == ast.dump(old), "Unrelated reporting change"
    return baseline


def main():
    from reporting import is_income, income_charity_scope, income_charity_month_values, income_charity_percentage
    protected_reporting(Path("reporting.py").read_bytes())
    pairs = [("Tour Income", "Walt Disney"), ("Tour Income", "Other"),
             ("Tour Income", "Todd Regan"), ("Other", "Rental INCOME"),
             ("Income", "Income"), ("Incoming", "Other"),
             ("Other", "Other"), ("Charity", "Support")]
    month = pd.Period("2026-01", freq="M")
    rows = pd.DataFrame([dict(id=i+1, category=c, subcategory=s, report_group="Woking Way LLC",
                             month=month, txn_date="2026-01-01", report_amount=10 if i<7 else -5)
                         for i, (c, s) in enumerate(pairs)])
    baseline = rows.copy(deep=True)
    scoped, monthly, cumulative = income_charity_month_values(rows, [month])
    assert scoped.id.tolist() == [1, 2, 3, 4, 5, 8]
    assert monthly["Income"][month] == cumulative["Income"][month] == 50
    assert monthly["Charity"][month] == -5
    assert income_charity_percentage(50, -5) == 10
    assert not is_income("Incoming") and not is_income(None, None)
    assert is_income("pre-income") and is_income("", "INCOME")
    assert income_charity_scope(rows.assign(report_group="Income")).id.tolist() == rows.id.tolist()
    exported = pd.read_csv(StringIO(scoped.to_csv(index=False)))
    assert exported.id.tolist() == scoped.id.tolist() and exported.report_amount.sum() == 45
    pd.testing.assert_frame_equal(rows, baseline)

    # Execute the production section and its selected category/subcategory callbacks.
    # No database or application startup is invoked.
    source = Path("app.py").read_text(encoding="utf-8")
    for editable in (True, False):
        displayed = []
        def click_rows(title, data, *args, **kwargs):
            for row in data:
                kwargs["render_child"](row["value"])
        ui = SimpleNamespace(session_state={}, markdown=Mock(), info=Mock(), button=Mock(),
                             container=lambda **kwargs: nullcontext())
        env = dict(pd=pd, st=ui, escape=escape,
                   _executive_metric_values_from_month_values=lambda *args: {},
                   _income_charity_target_summary_message=lambda *args: "",
                   _clear_executive_selections=Mock(), _render_executive_click_rows=click_rows,
                   _executive_level_rows=lambda frame, col, months: [{"value": v} for v in frame[col].unique()],
                   _render_income_charity_transactions=lambda frame, **kw: displayed.append((frame.copy(), kw.get("editable", False))))
        load_functions(source, {"_render_income_charity_section"}, env)
        env["_render_income_charity_section"](rows, [month], {month: "Jan"}, editable=editable)
        assert sorted(i for frame, _ in displayed for i in frame.id) == [1, 2, 3, 4, 5, 8]
        assert all(flag == editable for _, flag in displayed)
    tree = ast.parse(source)
    third = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "render_third_link_report")
    calls = [n for n in ast.walk(third) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
             and n.func.id == "_render_income_charity_section"]
    assert calls and all(not any(k.arg == "editable" for k in n.keywords) for n in calls)
    assert "editable=not shared_report" in source
    print("PASS: exact membership, no double counting, totals/percentage/CSV, authorized editor drill-down and read-only THIRD")


if __name__ == "__main__":
    main()
