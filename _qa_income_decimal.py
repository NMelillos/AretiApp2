"""Exact monetary aggregation through Income and shared report hierarchy."""
from decimal import Decimal
import ast
import hashlib
from pathlib import Path
import subprocess


def without_decimal_change(name, actual):
    from _qa_report_formatting import without_formatting_change
    actual = without_formatting_change(name, actual)
    actual = actual.replace(b"\r\n", b"\n")
    expected = {"app.py": "29281a1ddaace658e83df97d1ea17dc8cdb99b2847c974fa6ee3868b78152999",
                "reporting.py": "47b27fa5246470722729f67f7546b3dc26540daea3e548b8082e54ba57484c97"}
    assert hashlib.sha256(actual).hexdigest() == expected[name], "Unreviewed Decimal source change"
    baseline = subprocess.check_output(["git", "show", "c8a2c54d9488a3068ce7ee138adf8ba282c4ecf6:" + name]).replace(b"\r\n", b"\n")
    allowed = {"reporting.py": {"income_charity_month_values", "income_charity_percentage", "_prepare_report_data"},
               "app.py": {"_money", "_executive_trend", "_executive_status_delta", "_executive_status_change_pct",
                          "_executive_signed_amount_series", "_executive_metric_values_from_month_values",
                          "_executive_metric_values", "_executive_share_denominator", "_executive_level_rows",
                          "_executive_total_row", "_income_charity_target_variance", "_render_income_charity_section"}}[name]
    old, new = ast.parse(baseline), ast.parse(actual)
    prior = {n.name: n for n in old.body if isinstance(n, ast.FunctionDef) and n.name in allowed}
    new.body = [prior[n.name] if isinstance(n, ast.FunctionDef) and n.name in prior else n for n in new.body]
    assert ast.dump(old) == ast.dump(new), "Unrelated application change"
    return baseline


def main():
    import pandas as pd
    from reporting import income_charity_month_values, _prepare_report_data
    from _qa_income_charity_edit import load_functions
    month = pd.Period("2026-01")
    frame = pd.DataFrame([dict(category="Tour Income", subcategory="Other", report_group="Woking Way LLC",
                               txn_date="2026-01-01", month=month, report_amount=v)
                          for v in ("0.1", "0.2")])
    _, monthly, cumulative = income_charity_month_values(frame, [month])
    assert monthly["Income"][month] == Decimal("0.3"), "Monthly monetary sum is not exact"
    assert isinstance(monthly["Income"][month], Decimal)
    assert cumulative["Income"][month] == Decimal("0.3")
    env = dict(pd=pd)
    names = {"_money", "_executive_trend", "_executive_status_delta", "_executive_status_change_pct",
             "_executive_metric_values_from_month_values", "_executive_signed_amount_series",
             "_executive_amount_series", "_executive_metric_values", "_executive_share_denominator",
             "_executive_level_rows", "_ordered_text_values", "_executive_total_row"}
    load_functions(Path("app.py").read_text(encoding="utf-8"), names, env)
    assert env["_money"]("invalid") == "-"
    assert env["_money"](Decimal("2.5")) == "$2"
    rows = env["_executive_level_rows"](frame, "report_group", [month])
    assert rows[0]["total"] == Decimal("0.3") and rows[0]["share_pct"] == 100
    assert env["_executive_total_row"](rows, [month])["total"] == Decimal("0.3")
    for values, expected in [(["90000000000.01", "-0.02", "0"], "89999999999.99"),
                             (["-1.15", "0.15", "1"], "0.00")]:
        test = pd.concat([frame.iloc[:1]] * len(values), ignore_index=True)
        test["report_amount"] = values
        _, result, _ = income_charity_month_values(test, [month])
        assert result["Income"][month] == Decimal(expected)
    tx = pd.DataFrame([dict(txn_date="2026-01-01", category="Tour Income", subcategory="Other",
                            currency=currency, amount="2.50", amount_usd=value)
                       for currency, value in [("USD", "0.10"), ("CHF", "0.20")]])
    categories = pd.DataFrame([dict(category="Tour Income", subcategory="Other", report_group="Woking Way LLC")])
    _, prepared, _, _ = _prepare_report_data(tx, categories, include_all_valid=True)
    _, result, _ = income_charity_month_values(prepared, [month])
    assert result["Income"][month] == Decimal("0.30")
    print("PASS: exact Decimal monthly/cumulative/hierarchy/TOTAL, Woking 0.3, signs, zero, large sum, existing USD conversions")


if __name__ == "__main__":
    main()
