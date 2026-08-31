import ast
from pathlib import Path
import re


def _load_metric_helpers():
    source = Path("app.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    names = {
        "_executive_trend",
        "_executive_semantic_trend_class",
        "_executive_status_delta",
        "_executive_status_change_pct",
        "_executive_metric_values_from_month_values",
    }
    module = ast.Module(
        body=[node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in names],
        type_ignores=[],
    )
    ast.fix_missing_locations(module)
    namespace = {"re": re}
    exec(compile(module, "app.py", "exec"), namespace)
    return (
        namespace["_executive_metric_values_from_month_values"],
        namespace["_executive_semantic_trend_class"],
    )


def assert_close(name, actual, expected, tolerance=0.01):
    if abs(float(actual) - float(expected)) > tolerance:
        raise AssertionError(f"{name} failed. expected={expected}, actual={actual}")
    print(f"PASS: {name} | {actual:.2f}")


def assert_equal(name, actual, expected):
    if actual != expected:
        raise AssertionError(f"{name} failed. expected={expected}, actual={actual}")
    print(f"PASS: {name} | {actual}")


def main():
    metric_values, semantic_class = _load_metric_helpers()
    source = Path("app.py").read_text(encoding="utf-8")
    months = ["2026-01", "2026-02", "2026-03", "2026-04", "2026-05", "2026-06"]
    values = {
        "2026-01": -3176.0,
        "2026-02": -24210.0,
        "2026-03": -3549.0,
        "2026-04": -17817.0,
        "2026-05": -83131.0,
        "2026-06": 5464.0,
    }
    metrics = metric_values(values, months)
    previous_average = sum(values[month] for month in months[:-1]) / 5

    assert_close("trend baseline is average of previous months", metrics["period_start"], previous_average)
    assert_close("trend change treats return after outflows as decrease", metrics["period_change"], previous_average - 5464.0)
    assert_close("trend percent treats return after outflows as decrease", metrics["period_change_pct"], -120.72)
    assert_equal("trend status treats return after outflows as decrease", metrics["period_trend_text"], "Decreasing")
    assert_close("previous month change treats return after outflow as decrease", metrics["change"], -88595.0)
    assert_equal("previous month status treats return after outflow as decrease", metrics["trend_text"], "Decreasing")

    income_metrics = metric_values({"2026-01": 100.0, "2026-02": 150.0}, ["2026-01", "2026-02"])
    assert_close("positive income still increases normally", income_metrics["period_change"], 50.0)
    assert_equal("positive income status still increases normally", income_metrics["period_trend_text"], "Increasing")

    income_cases = [
        ("decrease", 100.0, 80.0, -20.0, "Decreasing", "trend-up"),
        ("increase", 100.0, 120.0, 20.0, "Increasing", "trend-down"),
        ("unchanged", 100.0, 100.0, 0.0, "No change", "trend-flat"),
    ]
    for name, previous, current, expected_change, expected_status, expected_colour_class in income_cases:
        case_metrics = metric_values(
            {"2026-01": previous, "2026-02": current},
            ["2026-01", "2026-02"],
        )
        before = dict(case_metrics)
        assert_close(f"Income {name} change unchanged", case_metrics["change"], expected_change)
        assert_equal(f"Income {name} status unchanged", case_metrics["trend_text"], expected_status)
        assert_equal(
            f"Income {name} colour semantics",
            semantic_class(case_metrics["trend_class"], "Income"),
            expected_colour_class,
        )
        assert_equal(f"Income {name} metrics not mutated", case_metrics, before)

    expense_cases = [
        ("increase", 100.0, 120.0, "trend-up"),
        ("decrease", 100.0, 80.0, "trend-down"),
        ("unchanged", 100.0, 100.0, "trend-flat"),
    ]
    for name, previous, current, expected_colour_class in expense_cases:
        case_metrics = metric_values(
            {"2026-01": previous, "2026-02": current},
            ["2026-01", "2026-02"],
        )
        assert_equal(
            f"Expense {name} colour unchanged",
            semantic_class(case_metrics["trend_class"], "Lifestyle"),
            expected_colour_class,
        )

    trend_metrics = metric_values(
        {"2026-01": 100.0, "2026-02": 100.0, "2026-03": 80.0},
        ["2026-01", "2026-02", "2026-03"],
    )
    assert_close("Income trend numeric value unchanged", trend_metrics["period_change"], -20.0)
    assert_equal("Income trend status remains decreasing", trend_metrics["period_trend_text"], "Decreasing")
    assert_equal(
        "Income decreasing trend is red",
        semantic_class(trend_metrics["period_trend_class"], "Income"),
        "trend-up",
    )
    assert_equal(
        "special Woking Income category is detected",
        semantic_class("trend-down", "Walt Disney house tour income"),
        "trend-up",
    )
    assert_equal(
        "special TB Tribute Income subcategory is detected",
        semantic_class("trend-down", "Income"),
        "trend-up",
    )
    assert_equal(
        "Income analysis children inherit Income semantics",
        semantic_class("trend-down", "Amazon", income_context=True),
        "trend-up",
    )
    assert_equal(
        "incoming description-like label is not a false positive",
        semantic_class("trend-down", "Incoming bank transfer"),
        "trend-down",
    )
    assert_equal(
        "TOTAL row is not treated as Income",
        semantic_class("trend-down", "TOTAL"),
        "trend-down",
    )
    assert_equal(
        "Charity row is not treated as Income",
        semantic_class("trend-down", "Charity"),
        "trend-down",
    )

    signed_decrease = metric_values({"2026-01": -100.0, "2026-02": -80.0}, ["2026-01", "2026-02"])
    assert_equal("signed Income decrease status", signed_decrease["trend_text"], "Decreasing")
    assert_equal(
        "signed Income decrease is red",
        semantic_class(signed_decrease["trend_class"], "Income"),
        "trend-up",
    )

    renderer_source = source[
        source.index("def _render_executive_click_rows"):
        source.index("def _executive_selected_transactions_export_sheets")
    ]
    income_section_source = source[
        source.index("def _render_income_charity_section"):
        source.index("def render_executive_report")
    ]
    metric_source = source[
        source.index("def _executive_metric_values_from_month_values"):
        source.index("def _executive_metric_values(")
    ]
    assert_equal(
        "shared renderer applies semantic colour translation",
        "_executive_semantic_trend_class(" in renderer_source,
        True,
    )
    assert_equal(
        "Income child hierarchy inherits semantics at both levels",
        income_section_source.count('income_context=row_type == "Income"') == 2,
        True,
    )
    assert_equal(
        "numeric metric calculation is isolated from colour semantics",
        "_executive_semantic_trend_class" in metric_source,
        False,
    )
    assert_equal("red CSS class remains unchanged", ".drill-cell.trend-up" in source and "#b42318" in source, True)
    assert_equal("green CSS class remains unchanged", ".drill-cell.trend-down" in source and "#067647" in source, True)

    single_month = metric_values({"2026-01": -100.0}, ["2026-01"])
    assert_close("single month trend change is neutral", single_month["period_change"], 0.0)
    assert_equal("single month trend percent is blank", single_month["period_change_pct"], None)
    assert_equal("single month trend status is no change", single_month["period_trend_text"], "No change")
    print("QA_COMPLETE")


if __name__ == "__main__":
    main()
