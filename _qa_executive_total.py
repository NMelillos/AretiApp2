import ast
from pathlib import Path

import pandas as pd


def assert_equal(name, actual, expected):
    if actual != expected:
        raise AssertionError(f"{name} failed. expected={expected!r} actual={actual!r}")
    print(f"PASS: {name} | {actual!r}")


def assert_close(name, actual, expected, tolerance=0.000001):
    if abs(float(actual) - float(expected)) > tolerance:
        raise AssertionError(f"{name} failed. expected={expected!r} actual={actual!r}")
    print(f"PASS: {name} | {actual!r}")


def load_helpers():
    source = Path("app.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    names = {
        "_executive_trend",
        "_executive_status_delta",
        "_executive_status_change_pct",
        "_executive_metric_values_from_month_values",
        "_executive_total_row",
    }
    nodes = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in names]
    namespace = {}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), "app.py", "exec"), namespace)
    return source, namespace


def metric_row(metric_fn, label, values, months, denominator):
    row = metric_fn(dict(zip(months, values)), months, denominator)
    row.update({"label": label, "value": label})
    return row


def main():
    source, helpers = load_helpers()
    metric_fn = helpers["_executive_metric_values_from_month_values"]
    total_fn = helpers["_executive_total_row"]
    months = list(pd.period_range("2026-01", periods=4, freq="M"))
    group_a = metric_row(metric_fn, "A", [-100, -50, 25, -75], months, 425)
    group_b = metric_row(metric_fn, "B", [-20, 10, -5, 40], months, 425)
    group_zero = metric_row(metric_fn, "0-Control", [0, 0, 0, 0], months, 425)
    rows = [group_a, group_b, group_zero]
    total = total_fn(rows, months)

    expected_months = dict(zip(months, [-120.0, -40.0, 20.0, -35.0]))
    assert_equal("TOTAL label", total["label"], "TOTAL")
    assert_equal("TOTAL is non-data row", total["is_total"], True)
    assert_equal("TOTAL monthly values", total["months"], expected_months)
    assert_close("TOTAL sum since Jan", total["total"], -175.0)
    assert_close("TOTAL percentage", total["share_pct"], 100.0)
    assert_close("TOTAL average uses total monthly series", total["average"], -43.75)
    assert_close("TOTAL change derives from total months", total["change"], -55.0)
    assert_close("TOTAL percent change", total["change_pct"], -275.0)
    assert_equal("TOTAL status", total["trend_text"], "Decreasing")
    assert_close("TOTAL trend baseline", total["period_start"], -46.666666666666664)
    assert_close("TOTAL trend change", total["period_change"], -11.666666666666664)
    assert_close("TOTAL trend percent", total["period_change_pct"], -25.0)
    assert_equal("TOTAL trend status", total["period_trend_text"], "Decreasing")
    assert_close(
        "TOTAL average is independently calculated",
        total["average"],
        sum(expected_months.values()) / len(months),
    )

    zero_total = total_fn([group_zero], months)
    assert_equal("zero denominator percentage is undefined", zero_total["share_pct"], None)
    assert_equal("zero total status", zero_total["trend_text"], "No change")
    assert_equal("zero total trend status", zero_total["period_trend_text"], "No change")
    assert_equal("empty rows have no TOTAL", total_fn([], months), None)

    filtered_total = total_fn([group_a], months)
    assert_equal("filter changes TOTAL population", filtered_total["months"], group_a["months"])
    assert_close("filter recalculates TOTAL", filtered_total["total"], group_a["total"])

    total_source_start = source.index("def _executive_total_row")
    total_source_end = source.index("def _ordered_text_values", total_source_start)
    total_source = source[total_source_start:total_source_end]
    render_start = source.index("def _render_executive_click_rows")
    render_end = source.index("def _executive_selected_transactions_export_sheets", render_start)
    render_source = source[render_start:render_end]
    executive_start = source.index("def render_executive_report")
    third_start = source.index("def render_third_link_report")
    assert_equal("TOTAL reuses existing metric helper", "_executive_metric_values_from_month_values" in total_source, True)
    assert_equal("TOTAL performs no database query", "get_" in total_source or "SELECT" in total_source, False)
    assert_equal("TOTAL row is static", "if is_total:" in render_source, True)
    assert_equal("TOTAL cannot render a child drill-down", "if not is_total and render_child" in render_source, True)
    assert_equal("Executive Summary opts into TOTAL", "show_group_total=not shared_report" in source[executive_start:third_start], True)
    assert_equal("THIRD does not opt into TOTAL", "show_group_total=" in source[third_start:], False)
    print("EXECUTIVE_TOTAL_QA_COMPLETE")


if __name__ == "__main__":
    main()
