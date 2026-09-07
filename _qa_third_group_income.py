"""Synthetic report reconciliation; no application startup or database writes."""
import ast
import subprocess
from pathlib import Path
from unittest.mock import patch

import pandas as pd

import reporting
from reporting import _prepare_report_data, income_charity_scope, third_hierarchy_item19_exclusions

BASE = "089eb931062e211ec940a1fc1c53baf966d7f7af"


def helpers(source):
    names = {
        "_third_report_group_scope", "_executive_signed_amount_series",
        "_executive_amount_series", "_executive_metric_values",
        "_executive_metric_values_from_month_values", "_executive_trend",
        "_executive_status_delta", "_executive_status_change_pct",
        "_executive_level_rows", "_ordered_text_values", "_executive_share_denominator",
        "_executive_total_row",
    }
    nodes = [n for n in ast.parse(source).body if isinstance(n, ast.FunctionDef) and n.name in names]
    namespace = {"pd": pd}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), "app.py", "exec"), namespace)
    return namespace


def main():
    source = Path("app.py").read_text(encoding="utf-8")
    baseline = subprocess.check_output(["git", "show", f"{BASE}:app.py"]).decode("utf-8")
    old, new = helpers(baseline), helpers(source)
    old_nodes = {n.name: ast.dump(n) for n in ast.parse(baseline).body if isinstance(n, ast.FunctionDef)}
    new_nodes = {n.name: ast.dump(n) for n in ast.parse(source).body if isinstance(n, ast.FunctionDef)}
    assert {k for k in old_nodes if old_nodes[k] != new_nodes[k]} <= {"_third_report_group_scope"}
    print("PASS: Executive and all other function ASTs unchanged")
    report_baseline = subprocess.check_output(["git", "show", f"{BASE}:reporting.py"]).decode("utf-8")
    reporting_old = {n.name: ast.dump(n) for n in ast.parse(report_baseline).body if isinstance(n, ast.FunctionDef)}
    reporting_new = {n.name: ast.dump(n) for n in ast.parse(Path("reporting.py").read_text(encoding="utf-8")).body if isinstance(n, ast.FunctionDef)}
    assert all(reporting_new[k] == v for k, v in reporting_old.items())
    old_reporting = {}
    exec(compile(report_baseline, "baseline_reporting.py", "exec"), old_reporting)
    assert reporting.INCOME_CHARITY_SPECIAL_INCOME == old_reporting["INCOME_CHARITY_SPECIAL_INCOME"]
    print("PASS: existing reporting functions and Item 19 taxonomy unchanged")
    categories = pd.DataFrame([
        {"category": "Walt Disney house expenses", "subcategory": "General", "report_group": "Woking Way LLC"},
        {"category": "Walt Disney house tour income", "subcategory": "Income", "report_group": "Woking Way LLC"},
    ])
    months = list(pd.period_range("2026-01", periods=8, freq="M"))
    # Rounded screenshot monthly values sum to -97,886, not -97,885.
    # These explicit synthetic cents reconcile the stated rounded display totals.
    expenses = [-1998.88, -28442.88, -3449.88, -39145.88, -2562.88, -12066.88, -3071.88, -7145.84]
    income = [6500, 3000, 2500, 14000, 0, 2500, 0, 0]
    scenarios = [(expenses, income), ([0]*8, income), (expenses, [0]*8), ([0]*8, [0]*8)]
    for index, (outflows, inflows) in enumerate(scenarios):
        rows = []
        for category, values in zip(categories.to_dict("records"), (outflows, inflows)):
            for month, amount in zip(months, values):
                rows.append({**category, "id": len(rows)+1, "txn_date": str(month)+"-01",
                             "amount": amount, "amount_usd": amount, "currency": "USD", "status": "reviewed"})
        transactions = pd.DataFrame(rows)
        original = transactions.copy(deep=True)
        _, executive, _, _ = _prepare_report_data(transactions, categories, include_all_valid=True, include_own_funds=True)
        third, setup = new["_third_report_group_scope"](executive, categories)
        before, _ = old["_third_report_group_scope"](executive, categories)
        pd.testing.assert_frame_equal(third, executive)
        pd.testing.assert_frame_equal(setup, categories)
        pd.testing.assert_frame_equal(transactions, original)
        _, baseline_executive, _, _ = old_reporting["_prepare_report_data"](
            original, categories, include_all_valid=True, include_own_funds=True)
        pd.testing.assert_frame_equal(executive, baseline_executive)
        assert third["id"].is_unique
        pd.testing.assert_frame_equal(income_charity_scope(executive), income_charity_scope(third))
        metric = new["_executive_metric_values"]
        parent = metric(third, months)
        assert parent == old["_executive_metric_values"](executive, months)
        children = [metric(third[third.category.eq(c)], months) for c in categories.category]
        running_parent = 0.0
        running_children = 0.0
        for month in months:
            assert abs(parent["months"][month] - sum(c["months"][month] for c in children)) < 1e-8
            running_parent += parent["months"][month]
            running_children += sum(c["months"][month] for c in children)
            assert abs(running_parent - running_children) < 1e-8
        assert abs(parent["total"] - sum(c["total"] for c in children)) < 1e-8
        assert abs(parent["average"] - sum(c["average"] for c in children)) < 1e-8
        if index == 0:
            assert parent["total"] == -69385
            assert [c["total"] for c in children] == [-97885, 28500]
            print(f"PASS: synthetic Jan-Aug before={metric(before, months)['total']} after={parent['total']} average={parent['average']}")
            shares = new["_executive_level_rows"](third, "category", months)
            assert sorted(round(r["share_pct"], 1) for r in shares) == [22.6, 77.4]
            group_rows = new["_executive_level_rows"](third, "report_group", months)
            assert new["_executive_total_row"](group_rows, months)["total"] == parent["total"]
            print("PASS: child shares 77.4/22.6 and selected-group TOTAL counts income once")
        print(f"PASS: scenario={index} identical transaction set, metrics, monthly sums, no duplicate IDs or input mutation")

    canonical = {"category": "Walt Disney house tour income", "subcategory": "Income", "report_group": "Woking Way LLC"}
    policy_rows = pd.DataFrame([
        {**canonical, "id": 1, "report_amount": 100},
        {"id": 2, "category": "Cypress Apartments-TB Tribute", "subcategory": "Income", "report_group": "TB Tribute Ltd", "report_amount": 200},
        {**canonical, "id": 3, "report_group": "Income", "report_amount": 300},
        {**canonical, "id": 4, "category": "Other income", "report_group": "Income", "report_amount": 400},
        {"id": 5, "category": "Charity", "subcategory": "Support", "report_group": "Family expenses", "report_amount": -50},
        {**canonical, "id": 6, "subcategory": "Other", "report_amount": 600},
        {**canonical, "id": 7, "report_group": "Other group", "report_amount": 700},
        {**canonical, "id": 8, "category": "Walt Disney house tour income extra", "report_amount": 800},
    ])
    before_scope, _ = old["_third_report_group_scope"](policy_rows, policy_rows)
    after_scope, _ = new["_third_report_group_scope"](policy_rows, policy_rows)
    assert set(after_scope.id) - set(before_scope.id) == {1}
    assert after_scope.loc[after_scope.id.eq(1), list(canonical)].to_dict("records") == [canonical]
    assert 2 not in set(after_scope.id)
    pd.testing.assert_frame_equal(after_scope[after_scope.id.ne(1)], before_scope)
    pd.testing.assert_frame_equal(income_charity_scope(policy_rows), old_reporting["income_charity_scope"](policy_rows))
    assert income_charity_scope(policy_rows).id.is_unique
    assert third_hierarchy_item19_exclusions(pd.DataFrame()).empty
    nullable = pd.DataFrame([{**canonical, "subcategory": None}, {**canonical, "report_group": None}])
    assert third_hierarchy_item19_exclusions(nullable).empty
    print("PASS: only canonical identity 1 added; TB Tribute, other groups, near mappings and Item 19 unchanged")

    split_rows = pd.DataFrame([
        {**canonical, "id": 10, "amount": 100, "amount_usd": 100, "split_group_id": "qa-split", "split_parent_id": None, "split_allocation_index": None},
        {**canonical, "id": 11, "amount": 40, "amount_usd": 40, "split_group_id": "qa-split", "split_parent_id": 10, "split_allocation_index": 1},
        {**canonical, "id": 12, "amount": 60, "amount_usd": 60, "split_group_id": "qa-split", "split_parent_id": 10, "split_allocation_index": 2},
    ])
    split_rows["txn_date"] = "2026-01-01"
    split_rows["currency"] = "USD"
    split_rows["status"] = "reviewed"
    with patch("db.get_connection", side_effect=AssertionError("report calculation attempted a database connection")):
        _, prepared, _, _ = _prepare_report_data(split_rows, categories, include_all_valid=True)
        scoped, _ = new["_third_report_group_scope"](prepared, categories)
        assert scoped.id.tolist() == [11, 12]
        assert metric(scoped, months)["total"] == 100
        assert income_charity_scope(prepared).report_amount.sum() == 100
    print("PASS: split parent excluded, children counted once in each analytical view; calculation uses no database connection")
    print("THIRD_GROUP_INCOME_QA_COMPLETE")


if __name__ == "__main__":
    main()
