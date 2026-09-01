import ast
from pathlib import Path

import pandas as pd

from reporting import income_charity_percentage, income_charity_scope


def assert_equal(name, actual, expected):
    if actual != expected:
        raise AssertionError(f"{name} failed. expected={expected!r} actual={actual!r}")
    print(f"PASS: {name} | {actual!r}")


def assert_close(name, actual, expected, tolerance=0.000001):
    if abs(float(actual) - float(expected)) > tolerance:
        raise AssertionError(f"{name} failed. expected={expected!r} actual={actual!r}")
    print(f"PASS: {name} | {actual!r}")


def load_helpers(source):
    tree = ast.parse(source)
    names = {
        "_executive_trend",
        "_executive_status_delta",
        "_executive_status_change_pct",
        "_executive_metric_values_from_month_values",
        "_executive_total_row",
        "_third_report_group_scope",
        "_money",
        "_income_charity_target_variance",
        "_income_charity_target_variance_message",
    }
    nodes = [
        node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name in names
    ]
    namespace = {"pd": pd}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), "app.py", "exec"), namespace)
    return namespace


def metric_row(metric_fn, label, values, months, denominator):
    row = metric_fn(dict(zip(months, values)), months, denominator)
    row.update({"label": label, "value": label})
    return row


def main():
    source = Path("app.py").read_text(encoding="utf-8")
    helpers = load_helpers(source)
    metric_fn = helpers["_executive_metric_values_from_month_values"]
    total_fn = helpers["_executive_total_row"]
    scope_fn = helpers["_third_report_group_scope"]
    variance_fn = helpers["_income_charity_target_variance"]
    variance_message_fn = helpers["_income_charity_target_variance_message"]
    months = list(pd.period_range("2026-01", periods=3, freq="M"))

    group_a = metric_row(metric_fn, "A", [-100, -25, 0], months, 215)
    group_b = metric_row(metric_fn, "B", [0, -50, 10], months, 215)
    group_c = metric_row(metric_fn, "C", [-50, 0, 0], months, 215)
    one = total_fn([group_a], months)
    two = total_fn([group_a, group_c], months)
    three = total_fn([group_a, group_b, group_c], months)
    assert_equal("one selected group TOTAL months", one["months"], group_a["months"])
    assert_close("one selected group TOTAL", one["total"], -125)
    assert_close("one selected group percentage", one["share_pct"], 100)
    assert_close("two selected groups TOTAL", two["total"], -175)
    assert_close("two selected groups average", two["average"], -175 / 3)
    assert_close("multiple selected groups TOTAL", three["total"], -215)
    assert_equal("no selected groups has no TOTAL", total_fn([], months), None)

    rows = pd.DataFrame([
        {"id": 1, "category": "Operations", "subcategory": "General", "report_group": "A"},
        {"id": 2, "category": "Charity", "subcategory": "Support", "report_group": "A"},
        {"id": 3, "category": "Operations", "subcategory": "General", "report_group": "B"},
        {"id": 4, "category": "Income", "subcategory": "Interest earned", "report_group": "Income deposits"},
        {"id": 5, "category": "Walt Disney house tour income", "subcategory": "Income", "report_group": "Woking Way LLC"},
        {"id": 6, "category": "Technology", "subcategory": "Hosting", "report_group": "Woking Way LLC"},
    ])
    categories = rows[["category", "subcategory", "report_group"]].copy()
    rows_before = rows.to_dict("records")
    categories_before = categories.to_dict("records")
    group_rows, group_categories = scope_fn(rows, categories)
    assert_equal("THIRD scope does not mutate report rows", rows.to_dict("records"), rows_before)
    assert_equal("THIRD scope does not mutate mappings", categories.to_dict("records"), categories_before)
    assert_equal("Income and Charity rows excluded from group scope", group_rows["id"].tolist(), [1, 3, 6])
    assert_equal(
        "Income and Charity mappings excluded from group setup scope",
        group_categories["category"].tolist(),
        ["Operations", "Operations", "Technology"],
    )
    assert_equal("Item 19 source rows remain available", income_charity_scope(rows)["id"].tolist(), [2, 4, 5])
    assert_equal("mixed reporting group remains available", "Woking Way LLC" in group_rows["report_group"].tolist(), True)
    assert_equal("dedicated Income group is absent", "Income deposits" in group_rows["report_group"].tolist(), False)

    baseline_percentage = income_charity_percentage(187548.01, -38513.07)
    assert_close("protected Item 19 percentage", baseline_percentage, 20.535, tolerance=0.001)
    assert_close("protected amount above target", variance_fn(187548.01, -38513.07), 19758.269)
    assert_close("below-target variance", variance_fn(1000, -99), -1)
    assert_close("exact-target variance", variance_fn(1000, -100), 0)
    assert_equal("zero Income has no variance", variance_fn(0, -100), None)
    assert_equal("undefined Income has no variance", variance_fn(None, -100), None)
    assert_equal(
        "above-target display message",
        variance_message_fn(187548.01, -38513.07),
        "Amount above the 10% target: $19,758.",
    )
    assert_equal(
        "below-target display message",
        variance_message_fn(1000, -99),
        "Amount below the 10% target: $1.",
    )
    assert_equal("exact-target has no artificial difference", variance_message_fn(1000, -100), None)
    assert_equal("undefined target has no difference message", variance_message_fn(0, -100), None)

    item19_start = source.index("def _render_income_charity_section")
    executive_start = source.index("def render_executive_report")
    third_start = source.index("def render_third_link_report")
    item19_source = source[item19_start:executive_start]
    third_source = source[third_start:]
    setup_source = source[source.index('elif page == "Setup":'):]
    assert_equal(
        "target message renders before any Item 19 selection",
        item19_source.index("target_summary_message = _income_charity_target_summary_message")
        < item19_source.index("def render_selected_subcategory"),
        True,
    )
    assert_equal("large Income Charity cards removed", "render_summary_strip" in item19_source, False)
    assert_equal("Monthly values presentation removed", "#### Monthly values" in item19_source, False)
    assert_equal("Cumulative presentation removed", "#### Cumulative from January" in item19_source, False)
    assert_equal("THIRD uses selected-group TOTAL", "show_group_total=True" in third_source, True)
    assert_equal("THIRD explains mathematically correct zero rows", "show_zero_explanations=True" in third_source, True)
    assert_equal("THIRD separates Item 19 before group filtering", "_third_report_group_scope" in third_source, True)
    assert_equal(
        "THIRD group drilldown uses separated selected expenses",
        "_render_executive_drilldown(\n            selected_expenses" in third_source,
        True,
    )
    assert_equal(
        "THIRD Item 19 uses the complete report population",
        "_render_income_charity_section(\n        report_expenses" in third_source,
        True,
    )
    assert_equal("THIRD Item 19 does not trigger AI", "_get_reporting_group_analysis(\n        report_expenses" in third_source, False)
    assert_equal(
        "Setup forces Item 19-only third-link selections off",
        'settings_edit["report_group"].isin(item19_only_groups)' in setup_source
        and '"third_link_visible",\n        ] = False' in setup_source,
        True,
    )
    assert_equal(
        "Setup explains automatic separate Item 19 section",
        "Income and Charity is shown automatically as a separate THIRD Report section." in setup_source,
        True,
    )
    print("THIRD_REPORT_REFINEMENT_QA_COMPLETE")


if __name__ == "__main__":
    main()
