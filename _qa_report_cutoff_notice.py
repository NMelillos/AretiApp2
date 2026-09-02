import ast
from pathlib import Path
from time import perf_counter

import pandas as pd

from db import filter_financially_active_transactions
from reporting import _prepare_report_data


def assert_equal(name, actual, expected):
    if actual != expected:
        raise AssertionError(f"{name} failed. expected={expected!r} actual={actual!r}")
    print(f"PASS: {name} | {actual!r}")


def load_summary_helper(source):
    tree = ast.parse(source)
    node = next(
        item for item in tree.body
        if isinstance(item, ast.FunctionDef) and item.name == "_post_cutoff_reviewed_summary"
    )
    namespace = {
        "pd": pd,
        "active_financial_transactions": filter_financially_active_transactions,
    }
    exec(compile(ast.Module(body=[node], type_ignores=[]), "app.py", "exec"), namespace)
    return namespace["_post_cutoff_reviewed_summary"]


def row(transaction_id, date, *, status="reviewed", reviewed=1, **extra):
    values = {
        "id": transaction_id,
        "txn_date": date,
        "amount": 10.0,
        "amount_usd": 10.0,
        "currency": "USD",
        "category": "Operations",
        "subcategory": "General",
        "status": status,
        "reviewed": reviewed,
        "split_group_id": "",
        "split_parent_id": None,
        "split_allocation_index": None,
    }
    values.update(extra)
    return values


def main():
    source = Path("app.py").read_text(encoding="utf-8")
    summary = load_summary_helper(source)
    incident = pd.DataFrame([
        row(5393, "2026-08-21", amount=4980.0, amount_usd=4980.0),
        row(5395, "2026-08-24", amount=3823.95, amount_usd=3823.95),
    ])
    incident_before = incident.copy(deep=True)

    result = summary(incident, "2026-07-31")
    assert_equal("incident warning count", result["count"], 2)
    assert_equal("incident latest reviewed date", str(result["latest_date"]), "2026-08-24")
    assert_equal("summary does not mutate transactions", incident.equals(incident_before), True)
    assert_equal("cutoff equal to latest date has no warning", summary(incident, "2026-08-24")["count"], 0)
    assert_equal("cutoff after latest date has no warning", summary(incident, "2026-08-31")["count"], 0)

    edge_rows = pd.DataFrame([
        row(1, "2026-08-05", status="pending", reviewed=0),
        row(2, "2026-08-06", status="excluded", reviewed=1),
        row(3, "2026-08-07", split_group_id="parent-group"),
        row(4, "2026-08-08", split_group_id="child-group", split_parent_id=3, split_allocation_index=1),
        row(5, "not-a-date"),
        row(6, "2026-07-30"),
    ])
    edge_result = summary(edge_rows, "2026-07-31")
    assert_equal("pending unreviewed row excluded", edge_result["count"], 1)
    assert_equal("active split child counted", str(edge_result["latest_date"]), "2026-08-08")

    categories = pd.DataFrame([
        {"category": "Operations", "subcategory": "General", "report_group": "Business expenses"},
    ])
    _, before_report, _, _ = _prepare_report_data(
        incident,
        categories,
        include_own_funds=True,
        include_all_valid=True,
    )
    summary(incident, "2026-07-31")
    _, after_report, _, _ = _prepare_report_data(
        incident,
        categories,
        include_own_funds=True,
        include_all_valid=True,
    )
    pd.testing.assert_frame_equal(before_report, after_report)
    assert_equal("warning leaves report calculations unchanged", True, True)

    large = pd.concat([incident] * 2500, ignore_index=True)
    started = perf_counter()
    for _ in range(100):
        summary(large, "2026-07-31")
    elapsed_ms = (perf_counter() - started) * 1000 / 100
    if elapsed_ms > 100:
        raise AssertionError(f"warning calculation too slow: {elapsed_ms:.3f} ms")
    print(f"PASS: warning calculation benchmark | {elapsed_ms:.3f} ms per 5,000 rows")

    helper_start = source.index("def _render_report_cutoff_notice")
    helper_end = source.index("@st.cache_data", helper_start)
    helper_source = source[helper_start:helper_end]
    executive_start = source.index("def render_executive_report")
    third_start = source.index("def render_third_link_report")
    navigation_start = source.index("if is_third_link_report_request()")
    reports_start = source.index('elif page == "Reports":')
    setup_start = source.index('elif page == "Setup":')
    reports_source = source[reports_start:setup_start]
    setup_source = source[setup_start:]

    assert_equal("warning wording explains report-date exclusion", "are not included in this report" in helper_source, True)
    assert_equal("warning points to Setup", "update 'Report until' in Setup" in helper_source, True)
    assert_equal("warning does not describe missing data", "missing transaction" in helper_source.casefold(), False)
    assert_equal("warning never changes report date", "set_app_setting" in helper_source, False)
    assert_equal("Executive report renders cutoff warning", "_render_report_cutoff_notice(all_transactions, cutoff)" in source[executive_start:third_start], True)
    assert_equal("THIRD report renders cutoff warning", "_render_report_cutoff_notice(all_transactions, cutoff)" in source[third_start:navigation_start], True)
    assert_equal("ordinary Reports has no cutoff warning", "_render_report_cutoff_notice" in reports_source, False)
    assert_equal("Setup renders informational cutoff notice", "_render_report_cutoff_notice(setup_transactions, configured_report_until, setup=True)" in setup_source, True)
    assert_equal("Setup reuses transactions for backup", '"Transactions": setup_transactions' in setup_source, True)
    print("REPORT_CUTOFF_NOTICE_QA_COMPLETE")


if __name__ == "__main__":
    main()
