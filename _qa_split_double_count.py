import os
from pathlib import Path

os.environ.pop("DATABASE_URL", None)
os.environ.pop("POSTGRES_URL", None)
os.environ["ARETI_DB_PATH"] = str(Path("_qa_split_double_count.db").resolve())

import pandas as pd

import db
import reporting


def assert_equal(name, actual, expected):
    if actual != expected:
        raise AssertionError(f"{name} failed. expected={expected!r} actual={actual!r}")
    print(f"PASS: {name} | {actual!r}")


def assert_true(name, condition, details=""):
    if not condition:
        raise AssertionError(f"{name} failed. {details}")
    print(f"PASS: {name}" + (f" | {details}" if details else ""))


def reset_db():
    qa_db = Path(os.environ["ARETI_DB_PATH"])
    if qa_db.exists():
        qa_db.unlink()
    db.init_db()
    db.add_category("Split source", "", "1-family")
    db.add_category("Category A", "Sub A", "1-family")
    db.add_category("Category B", "Sub B", "2-business")
    db.add_category("Category C", "Sub C", "4 - Funding to group companies")
    db.add_category("Category D", "Sub D", "Woking Way LLC - Walt Disney House")


def insert_reviewed(amount, description):
    inserted = db.insert_manual_transaction(
        "2026-07-01",
        description,
        amount,
        "Split source",
        "",
        {
            "account_name": "QA Account",
            "bank": "QA Bank",
            "account_number": "QA1",
            "currency": "USD",
            "rate_type": "USD/USD",
        },
    )
    assert_equal(f"insert {description}", inserted, 1)
    all_rows = db.get_all_transactions()
    return int(all_rows.loc[all_rows["original_description"].eq(description), "id"].iloc[0])


def active_signed_total(frame):
    active = db.filter_financially_active_transactions(frame)
    return round(float(pd.to_numeric(active["amount"], errors="coerce").fillna(0).sum()), 2)


def report_signed_total(frame, categories):
    _, expenses, _, _ = reporting._prepare_report_data(
        frame,
        categories,
        include_own_funds=True,
        include_all_valid=True,
    )
    return round(float(pd.to_numeric(expenses["report_amount"], errors="coerce").fillna(0).sum()), 2)


def split_and_assert(parent_id, allocations, expected_total, expected_child_total=None):
    expected_child_total = expected_total if expected_child_total is None else expected_child_total
    result = db.split_transaction(parent_id, allocations)
    assert_equal("split inserted allocation count", result["inserted"], len(allocations))

    all_rows = db.get_all_transactions()
    saved_rows = db.get_saved_transactions()
    categories = db.get_categories(include_subcategories=True)

    assert_equal("active helper excludes split parent", active_signed_total(all_rows), expected_total)
    assert_equal("saved transactions exclude split parent", round(float(saved_rows["amount"].sum()), 2), expected_total)
    assert_equal("report prep excludes split parent", report_signed_total(all_rows, categories), expected_total)

    verification, _ = reporting.build_report_verification(all_rows, categories)
    assert_equal("verification net movement excludes split parent", verification["net_movement"], expected_total)

    parent = all_rows.loc[all_rows["id"].eq(parent_id)].iloc[0]
    assert_equal("parent remains traceable as excluded", str(parent["status"]).strip().casefold(), "excluded")
    assert_true("parent has split audit group", bool(str(parent.get("split_group_id") or "").strip()))
    children = all_rows[pd.to_numeric(all_rows.get("split_parent_id"), errors="coerce").eq(parent_id)]
    assert_equal("split children remain traceable", len(children), len(allocations))
    assert_equal("split child total equals original", round(float(children["amount"].sum()), 2), expected_child_total)
    return all_rows


def test_two_way_split_no_double_count():
    reset_db()
    parent_id = insert_reviewed(9500, "Two-way split test")
    split_and_assert(
        parent_id,
        [
            {"amount": 4000, "category": "Category A", "subcategory": "Sub A"},
            {"amount": 5500, "category": "Category B", "subcategory": "Sub B"},
        ],
        9500.0,
    )


def test_three_way_decimal_split_no_double_count():
    reset_db()
    parent_id = insert_reviewed(4233.06, "Decimal split test")
    split_and_assert(
        parent_id,
        [
            {"amount": 2000.03, "category": "Category A", "subcategory": "Sub A"},
            {"amount": 1000, "category": "Category B", "subcategory": "Sub B"},
            {"amount": 1233.03, "category": "Category C", "subcategory": "Sub C"},
        ],
        4233.06,
    )


def test_normal_plus_split_total_and_child_edit():
    reset_db()
    normal_id = insert_reviewed(100, "Normal row remains active")
    parent_id = insert_reviewed(1000, "Three-way split test")
    all_rows = split_and_assert(
        parent_id,
        [
            {"amount": 300, "category": "Category A", "subcategory": "Sub A"},
            {"amount": 300, "category": "Category B", "subcategory": "Sub B"},
            {"amount": 400, "category": "Category C", "subcategory": "Sub C"},
        ],
        1100.0,
        expected_child_total=1000.0,
    )

    child_id = int(all_rows[pd.to_numeric(all_rows.get("split_parent_id"), errors="coerce").eq(parent_id)]["id"].iloc[0])
    updated = db.update_database_rows(pd.DataFrame([{
        "id": child_id,
        "category": "Category D",
        "subcategory": "Sub D",
        "reviewed": True,
        "status": "reviewed",
    }]))
    assert_equal("split child category edit saves", updated, 1)

    categories = db.get_categories(include_subcategories=True)
    refreshed = db.get_saved_transactions()
    _, expenses, _, _ = reporting._prepare_report_data(
        refreshed,
        categories,
        include_own_funds=True,
        include_all_valid=True,
    )
    edited_child = expenses.loc[expenses["id"].eq(child_id)].iloc[0]
    assert_equal("split child reporting group refreshes", edited_child["report_group"], "Woking Way LLC - Walt Disney House")
    assert_equal("normal transaction still active", int(refreshed["id"].eq(normal_id).sum()), 1)
    assert_equal("total after child edit unchanged", round(float(refreshed["amount"].sum()), 2), 1100.0)


def test_historical_stale_split_parent_is_not_counted():
    reset_db()
    parent_id = insert_reviewed(1000, "Historical stale parent test")
    split_and_assert(
        parent_id,
        [
            {"amount": 600, "category": "Category A", "subcategory": "Sub A"},
            {"amount": 400, "category": "Category B", "subcategory": "Sub B"},
        ],
        1000.0,
    )

    conn = db.get_connection()
    try:
        conn.execute("UPDATE classified_transactions SET status = 'reviewed' WHERE id = ?", (parent_id,))
        conn.commit()
    finally:
        conn.close()

    all_rows = db.get_all_transactions()
    saved_rows = db.get_saved_transactions()
    categories = db.get_categories(include_subcategories=True)
    assert_equal("stale split parent excluded by structural marker", active_signed_total(all_rows), 1000.0)
    assert_equal("saved rows exclude stale split parent", round(float(saved_rows["amount"].sum()), 2), 1000.0)
    assert_equal("reports exclude stale split parent", report_signed_total(all_rows, categories), 1000.0)


def test_already_split_parent_cannot_split_again():
    reset_db()
    parent_id = insert_reviewed(1000, "Already split test")
    split_and_assert(
        parent_id,
        [
            {"amount": 500, "category": "Category A", "subcategory": "Sub A"},
            {"amount": 500, "category": "Category B", "subcategory": "Sub B"},
        ],
        1000.0,
    )
    try:
        db.split_transaction(
            parent_id,
            [
                {"amount": 500, "category": "Category A", "subcategory": "Sub A"},
                {"amount": 500, "category": "Category B", "subcategory": "Sub B"},
            ],
        )
    except ValueError as exc:
        assert_true("already split parent blocked", "already been split" in str(exc))
    else:
        raise AssertionError("already split parent was allowed")


if __name__ == "__main__":
    test_two_way_split_no_double_count()
    test_three_way_decimal_split_no_double_count()
    test_normal_plus_split_total_and_child_edit()
    test_historical_stale_split_parent_is_not_counted()
    test_already_split_parent_cannot_split_again()
    print("SPLIT_DOUBLE_COUNT_QA_COMPLETE")
