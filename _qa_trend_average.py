import ast
from pathlib import Path


def _load_metric_helpers():
    source = Path("app.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    names = {
        "_executive_trend",
        "_executive_status_delta",
        "_executive_status_change_pct",
        "_executive_metric_values_from_month_values",
    }
    module = ast.Module(
        body=[node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in names],
        type_ignores=[],
    )
    ast.fix_missing_locations(module)
    namespace = {}
    exec(compile(module, "app.py", "exec"), namespace)
    return namespace["_executive_metric_values_from_month_values"]


def assert_close(name, actual, expected, tolerance=0.01):
    if abs(float(actual) - float(expected)) > tolerance:
        raise AssertionError(f"{name} failed. expected={expected}, actual={actual}")
    print(f"PASS: {name} | {actual:.2f}")


def assert_equal(name, actual, expected):
    if actual != expected:
        raise AssertionError(f"{name} failed. expected={expected}, actual={actual}")
    print(f"PASS: {name} | {actual}")


def main():
    metric_values = _load_metric_helpers()
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
    assert_close("trend change compares current to previous average", metrics["period_change"], 5464.0 - previous_average)
    assert_close("trend percent compares current to previous average", metrics["period_change_pct"], 120.72)
    assert_equal("trend status uses previous average comparison", metrics["period_trend_text"], "Increasing")

    single_month = metric_values({"2026-01": -100.0}, ["2026-01"])
    assert_close("single month trend change is neutral", single_month["period_change"], 0.0)
    assert_equal("single month trend percent is blank", single_month["period_change_pct"], None)
    assert_equal("single month trend status is no change", single_month["period_trend_text"], "No change")
    print("QA_COMPLETE")


if __name__ == "__main__":
    main()
