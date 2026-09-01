import ast
from pathlib import Path

import pandas as pd

from reporting import (
    _prepare_report_data,
    income_charity_month_values,
    income_charity_percentage,
    income_charity_scope,
)


def assert_equal(name, actual, expected):
    if actual != expected:
        raise AssertionError(f"{name} failed. expected={expected!r} actual={actual!r}")
    print(f"PASS: {name} | {actual!r}")


def assert_close(name, actual, expected, tolerance=0.005):
    if abs(float(actual) - float(expected)) > tolerance:
        raise AssertionError(f"{name} failed. expected={expected!r} actual={actual!r}")
    print(f"PASS: {name} | {actual:.2f}")


def sample_rows():
    return pd.DataFrame([
        {"id": 1, "txn_date": "2026-01-01", "month": pd.Period("2026-01", freq="M"), "report_amount": 1000, "category": "Income", "subcategory": "Interest earned", "report_group": "Income"},
        {"id": 2, "txn_date": "2026-02-01", "month": pd.Period("2026-02", freq="M"), "report_amount": 500, "category": "Income", "subcategory": "Amazon", "report_group": "Income"},
        {"id": 3, "txn_date": "2026-01-02", "month": pd.Period("2026-01", freq="M"), "report_amount": -100, "category": "Charity", "subcategory": "Support to poor", "report_group": "Family expenses"},
        {"id": 4, "txn_date": "2026-02-02", "month": pd.Period("2026-02", freq="M"), "report_amount": -50, "category": "Charity", "subcategory": "Patreon", "report_group": "Family expenses"},
        {"id": 5, "txn_date": "2026-02-03", "month": pd.Period("2026-02", freq="M"), "report_amount": 250, "category": "Walt Disney house tour income", "subcategory": "Income", "report_group": "Woking Way LLC"},
        {"id": 6, "txn_date": "2026-02-04", "month": pd.Period("2026-02", freq="M"), "report_amount": 300, "category": "Cypress Apartments-TB Tribute", "subcategory": "Income", "report_group": "TB Tribute Ltd"},
        {"id": 7, "txn_date": "2026-02-05", "month": pd.Period("2026-02", freq="M"), "report_amount": 999, "category": "Walt Disney house tour income", "subcategory": "Income", "report_group": "Wrong group"},
        {"id": 8, "txn_date": "2026-02-06", "month": pd.Period("2026-02", freq="M"), "report_amount": -75, "category": "Lifestyle", "subcategory": "General", "report_group": "Family expenses"},
        {"id": 9, "txn_date": "2026-02-07", "month": pd.Period("2026-02", freq="M"), "report_amount": 400, "category": "Projects", "subcategory": "Project return", "report_group": "Income"},
        {"id": 10, "txn_date": "2026-02-08", "month": pd.Period("2026-02", freq="M"), "report_amount": 450, "category": "Paper deposits", "subcategory": "Deposit", "report_group": " Income "},
        {"id": 11, "txn_date": "2026-02-09", "month": pd.Period("2026-02", freq="M"), "report_amount": 700, "category": "Unapproved income label", "subcategory": "General", "report_group": "Family expenses"},
        {"id": 12, "txn_date": "2026-02-10", "month": pd.Period("2026-02", freq="M"), "report_amount": 200, "category": "Walt Disney house tour income", "subcategory": "Income", "report_group": "Income"},
        {"id": 13, "txn_date": "2026-02-11", "month": pd.Period("2026-02", freq="M"), "report_amount": 800, "category": "Income", "subcategory": "General", "report_group": "Wrong group"},
    ])


def main():
    rows = sample_rows()
    baseline = rows.copy(deep=True)
    months = [pd.Period("2026-01", freq="M"), pd.Period("2026-02", freq="M")]
    scoped, monthly, cumulative = income_charity_month_values(rows, months)

    assert_equal("source rows unchanged", rows.to_dict("records"), baseline.to_dict("records"))
    assert_equal("only structured Income and Charity rows included", scoped["id"].tolist(), [1, 2, 3, 4, 5, 6, 9, 10, 12])
    assert_close("January Income", monthly["Income"][months[0]], 1000)
    assert_close("February Income includes Reporting Group and special mappings", monthly["Income"][months[1]], 2100)
    assert_close("February Charity keeps statement sign", monthly["Charity"][months[1]], -50)
    assert_close("Income cumulative from January", cumulative["Income"][months[1]], 3100)
    assert_close("Charity cumulative from January", cumulative["Charity"][months[1]], -150)
    assert_equal("special Woking group preserved", scoped.loc[scoped["id"].eq(5), "report_group"].iloc[0], "Woking Way LLC")
    assert_equal("special TB Tribute group preserved", scoped.loc[scoped["id"].eq(6), "report_group"].iloc[0], "TB Tribute Ltd")
    assert_equal("Projects under Income included", 9 in scoped["id"].tolist(), True)
    assert_equal("another renamed category under Income included", 10 in scoped["id"].tolist(), True)
    assert_equal("unapproved income text outside Income group excluded", 11 in scoped["id"].tolist(), False)
    assert_equal("Category Income outside Income group excluded", 13 in scoped["id"].tolist(), False)
    assert_equal("row qualifying through group and special mapping counted once", scoped["id"].tolist().count(12), 1)

    zero_income = rows[rows["category"].eq("Charity")].copy()
    zero_scoped, zero_monthly, _ = income_charity_month_values(zero_income, months)
    assert_equal("charity-only rows retained", len(zero_scoped), 2)
    assert_close("zero-income month safe", sum(zero_monthly["Income"].values()), 0)
    assert_equal("zero-income percentage safe", income_charity_percentage(0, -150), None)
    assert_close("charity-income magnitude percentage", income_charity_percentage(3100, -150), 4.838710)
    assert_close("income-only percentage is zero", income_charity_percentage(3100, 0), 0)

    empty_scope = income_charity_scope(rows.iloc[0:0])
    assert_equal("empty input safe", len(empty_scope), 0)

    split_rows = pd.DataFrame([
        {
            "id": 20,
            "txn_date": "2026-02-10",
            "amount": 1000,
            "amount_usd": 1000,
            "currency": "USD",
            "category": "Income",
            "subcategory": "Interest earned",
            "status": "excluded",
            "split_group_id": "split-20",
            "split_parent_id": None,
        },
        {
            "id": 21,
            "txn_date": "2026-02-10",
            "amount": 600,
            "amount_usd": 600,
            "currency": "USD",
            "category": "Income",
            "subcategory": "Interest earned",
            "status": "reviewed",
            "split_group_id": "split-20",
            "split_parent_id": 20,
        },
        {
            "id": 22,
            "txn_date": "2026-02-10",
            "amount": 400,
            "amount_usd": 400,
            "currency": "USD",
            "category": "Income",
            "subcategory": "Interest earned",
            "status": "reviewed",
            "split_group_id": "split-20",
            "split_parent_id": 20,
        },
    ])
    split_categories = pd.DataFrame([
        {"category": "Income", "subcategory": "Interest earned", "report_group": "Income"},
    ])
    _, split_report_rows, _, _ = _prepare_report_data(
        split_rows,
        split_categories,
        include_own_funds=True,
        include_all_valid=True,
    )
    split_scoped, split_monthly, _ = income_charity_month_values(split_report_rows, months)
    assert_equal("split parent excluded", split_scoped["id"].tolist(), [21, 22])
    assert_close("split children counted once", sum(split_monthly["Income"].values()), 1000)

    source = Path("app.py").read_text(encoding="utf-8")
    source_tree = ast.parse(source)
    target_message_node = next(
        node
        for node in source_tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "_income_charity_target_message"
    )
    target_namespace = {}
    exec(compile(ast.Module(body=[target_message_node], type_ignores=[]), "app.py", "exec"), target_namespace)
    target_message = target_namespace["_income_charity_target_message"]
    assert_equal(
        "9.99 percent is below target",
        target_message(9.99),
        "Charity falls below the Family’s target of 10%.",
    )
    assert_equal(
        "10 percent is exactly on target",
        target_message(10.00),
        "Charity is meeting the Family’s target of 10%.",
    )
    assert_equal(
        "10.01 percent is above target",
        target_message(10.01),
        "Charity is exceeding the Family’s target of 10%.",
    )
    assert_equal(
        "zero percent is below target",
        target_message(0),
        "Charity falls below the Family’s target of 10%.",
    )
    assert_equal("undefined percentage has no target message", target_message(None), None)
    production_percentage = income_charity_percentage(187548.01, -38513.07)
    assert_close("production-derived percentage", production_percentage, 20.535, tolerance=0.001)
    assert_equal(
        "production-derived percentage exceeds target",
        target_message(production_percentage),
        "Charity is exceeding the Family’s target of 10%.",
    )
    helper_names = {
        "_money",
        "_percent",
        "_income_charity_target_variance",
        "_income_charity_target_summary_message",
    }
    helper_nodes = [
        node
        for node in source_tree.body
        if isinstance(node, ast.FunctionDef) and node.name in helper_names
    ]
    summary_namespace = {"pd": pd}
    exec(compile(ast.Module(body=helper_nodes, type_ignores=[]), "app.py", "exec"), summary_namespace)
    summary_message = summary_namespace["_income_charity_target_summary_message"]
    assert_equal(
        "above-target summary is one dynamic message",
        summary_message(production_percentage, 187548.01, -38513.07),
        "Charity is at 20.5% of income. Charity is exceeding the Family’s target of 10% by $19,758.",
    )
    assert_equal(
        "below-target summary is mathematically correct",
        summary_message(5.0, 1000, -50),
        "Charity is at 5.0% of income. Charity is below the Family’s target of 10% by $50.",
    )
    assert_equal(
        "exact-target summary is neutral",
        summary_message(10.0, 1000, -100),
        "Charity is at 10.0% of income. Charity is meeting the Family’s target of 10%.",
    )
    assert_equal("undefined-income summary remains safe", summary_message(None, 0, -50), None)
    item19_start = source.index("def _render_income_charity_section")
    third_start = source.index("def render_third_link_report")
    item19_source = source[item19_start:third_start]
    assert_equal(
        "Item 19 uses native report rows",
        '_render_executive_click_rows(\n        "4. Income and Charity"' in item19_source,
        True,
    )
    assert_equal(
        "Item 19 detail is rendered only by the selected row",
        "render_child=render_selected_type" in item19_source,
        True,
    )
    assert_equal(
        "Item 19 has explicit close control",
        '"Close",' in item19_source and 'help=f"Close {row_type} analysis"' in item19_source,
        True,
    )
    assert_equal(
        "Item 19 categories and subcategories use inline native rows",
        '"income_charity_category"' in item19_source
        and '"income_charity_subcategory"' in item19_source
        and "render_child=lambda category" in item19_source
        and "render_child=lambda subcategory" in item19_source,
        True,
    )
    assert_equal(
        "Item 19 no longer uses detached category selectors",
        "st.selectbox(" in item19_source,
        False,
    )
    assert_equal(
        "Item 19 summary uses the existing verified percentage and totals",
        "_income_charity_target_summary_message(" in item19_source
        and "charity_income_pct," in item19_source
        and "income_total," in item19_source
        and "charity_total," in item19_source,
        True,
    )
    assert_equal(
        "Item 19 renders one target message immediately below the section heading",
        "target_captions = [target_summary_message] if target_summary_message else []" in item19_source
        and "intro_captions=target_captions" in item19_source
        and "target_variance_message" not in item19_source,
        True,
    )
    selected_type_source = item19_source[
        item19_source.index("def render_selected_type"):item19_source.index(
            '_render_executive_click_rows(\n        "4. Income and Charity"'
        )
    ]
    assert_equal(
        "Item 19 close control follows the expanded branch content",
        selected_type_source.index('"Categories"')
        < selected_type_source.index('"Close",'),
        True,
    )
    assert_equal(
        "Item 19 branch retains its local compact presentation",
        'with st.container(key=f"income_charity_branch_{row_type.lower()}")' in selected_type_source
        and "show_title=False" in selected_type_source
        and "show_header=False" in selected_type_source,
        True,
    )
    assert_equal(
        "top-level Income and Charity share percentages are display-only blanks",
        "blank_share_pct=True" in item19_source,
        True,
    )
    assert_equal(
        "Income and Charity child percentages remain enabled",
        "blank_share_pct=True" not in selected_type_source,
        True,
    )
    assert_equal(
        "Item 19 is a separate third report section",
        "_render_income_charity_section(" in source[third_start:],
        True,
    )
    print("QA_COMPLETE")


if __name__ == "__main__":
    main()
