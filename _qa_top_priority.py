import os
import sys
from pathlib import Path

os.environ.pop("DATABASE_URL", None)
os.environ.pop("POSTGRES_URL", None)
os.environ["ARETI_DB_PATH"] = str(Path("_qa_top_priority.db").resolve())
sys.path.insert(0, str(Path("_qa_deps").resolve()))

import pandas as pd
from streamlit.testing.v1 import AppTest

import db
from parsing import _parse_amount, parse_csv


def assert_true(name, condition, details=""):
    if not condition:
        raise AssertionError(f"{name} failed. {details}")
    print(f"PASS: {name}" + (f" | {details}" if details else ""))


def _rendered_text(at):
    parts = []
    for collection_name in [
        "markdown",
        "caption",
        "info",
        "success",
        "warning",
        "error",
        "subheader",
        "title",
        "button",
        "expander",
    ]:
        for item in getattr(at, collection_name, []):
            parts.append(str(getattr(item, "value", item)))
            label = getattr(item, "label", None)
            if label:
                parts.append(str(label))
    return "\n".join(parts)


def seed_report_data():
    db.init_db()
    db.add_category("Control category", "", "0-not on reports")
    db.add_category("Family category", "", "1-family")
    db.add_category("Empty category", "", "8-Cypress apartments-TB Tribute")
    db.insert_manual_transaction(
        "2026-06-01",
        "Family test expense",
        100,
        "Family category",
        "",
        {
            "account_name": "QA Account",
            "bank": "QA Bank",
            "account_number": "QA1",
            "currency": "USD",
            "rate_type": "USD/USD",
        },
    )


def test_executive_trust_text():
    qa_db = Path(os.environ["ARETI_DB_PATH"])
    if qa_db.exists():
        qa_db.unlink()
    seed_report_data()

    at = AppTest.from_file("app.py", default_timeout=20)
    at.query_params["page"] = "Executive Summary"
    at.session_state["authenticated"] = True
    at.run()
    text = _rendered_text(at)

    assert_true("executive page renders", "Executive Summary" in text)
    assert_true("setup group count visible", "Setup groups" in text)
    assert_true("setup category count visible", "Setup categories" in text)
    assert_true("setup subcategory count visible", "Setup subcategories" in text)
    assert_true("zero group remains visible", "0-not on reports" in text)
    assert_true("zero row explanation available", "Why 1. reporting groups rows show $0" in text)


def test_database_category_enforcement():
    qa_db = Path(os.environ["ARETI_DB_PATH"])
    if qa_db.exists():
        qa_db.unlink()
    db.init_db()
    db.add_category("OldCat", "OldSub", "1-family")
    db.add_category("NewCat", "NewSub", "1-family")
    db.insert_manual_transaction(
        "2026-06-01",
        "Category enforcement test",
        10,
        "OldCat",
        "OldSub",
        {
            "account_name": "QA Account",
            "bank": "QA Bank",
            "account_number": "QA1",
            "currency": "USD",
            "rate_type": "USD/USD",
        },
    )
    conn = db.get_connection()
    try:
        row_id = int(pd.read_sql_query("SELECT id FROM classified_transactions", conn).iloc[0]["id"])
    finally:
        conn.close()

    db.update_database_rows(
        pd.DataFrame([
            {
                "id": row_id,
                "category": "NewCat",
                "subcategory": "OldSub",
                "reviewed": True,
                "status": "reviewed",
            }
        ])
    )
    conn = db.get_connection()
    try:
        stored = pd.read_sql_query(
            "SELECT category, subcategory FROM classified_transactions WHERE id = ?",
            conn,
            params=(row_id,),
        ).iloc[0]
    finally:
        conn.close()
    assert_true("database category edit is enforced", stored["category"] == "NewCat", stored.to_dict())
    assert_true("stale subcategory cleared", stored["subcategory"] == "", stored.to_dict())


def test_category_save_shapes_persist():
    qa_db = Path(os.environ["ARETI_DB_PATH"])
    if qa_db.exists():
        qa_db.unlink()
    db.init_db()
    db.add_category("OldCat", "OldSub", "1-family")
    db.add_category("NewCat", "NewSub", "1-family")
    db.add_category("NoSubCat", "", "1-family")
    db.insert_manual_transaction(
        "2026-06-01",
        "Executive shape persistence test",
        10,
        "OldCat",
        "OldSub",
        {
            "account_name": "QA Account",
            "bank": "QA Bank",
            "account_number": "QA1",
            "currency": "USD",
            "rate_type": "USD/USD",
        },
    )
    db.insert_manual_transaction(
        "2026-06-02",
        "Database shape persistence test",
        20,
        "OldCat",
        "OldSub",
        {
            "account_name": "QA Account",
            "bank": "QA Bank",
            "account_number": "QA1",
            "currency": "USD",
            "rate_type": "USD/USD",
        },
    )
    conn = db.get_connection()
    try:
        ids = pd.read_sql_query(
            "SELECT id FROM classified_transactions ORDER BY id",
            conn,
        )["id"].astype(int).tolist()
    finally:
        conn.close()

    executive_shape = pd.DataFrame([
        {
            "id": ids[0],
            "category": "NewCat",
            "subcategory": "NewSub",
            "reviewed": True,
            "status": "reviewed",
        }
    ])
    database_shape = pd.DataFrame([
        {
            "id": ids[1],
            "category": "NoSubCat",
            "subcategory": "",
            "reviewed": True,
            "status": "reviewed",
        }
    ])
    assert_true("executive-shaped category update writes", db.update_database_rows(executive_shape) == 1)
    assert_true("database-shaped category update writes", db.update_database_rows(database_shape) == 1)

    conn = db.get_connection()
    try:
        stored = pd.read_sql_query(
            "SELECT id, category, subcategory, reviewed, status FROM classified_transactions ORDER BY id",
            conn,
        )
    finally:
        conn.close()
    first = stored.iloc[0].to_dict()
    second = stored.iloc[1].to_dict()
    assert_true("executive-shaped category persists", first["category"] == "NewCat", first)
    assert_true("executive-shaped subcategory persists", first["subcategory"] == "NewSub", first)
    assert_true("database no-subcategory category persists", second["category"] == "NoSubCat", second)
    assert_true("database no-subcategory remains blank", second["subcategory"] == "", second)


def test_csv_amounts():
    assert_true("European amount with thousands", abs(_parse_amount("2.000,00") - 2000.0) < 0.001)
    assert_true("US amount with thousands", abs(_parse_amount("1,234.56") - 1234.56) < 0.001)
    boc_path = Path(
        r"C:\Users\Student\Dropbox\ARETI FILES ONE DRIVE\OneDrive_1_02-05-2026\Statement folder - New statements uploaded by Areti 02.04.2026\Bank of Cyprus\TransactionHistory_1775137479403.csv"
    )
    if boc_path.exists():
        with boc_path.open("rb") as handle:
            parsed = parse_csv(handle)
        assert_true("available BOC CSV parses", len(parsed) == 20, f"rows={len(parsed)}")
        assert_true("BOC thousands parsed correctly", parsed["Amount"].abs().max() >= 10000, parsed["Amount"].abs().max())
    else:
        print("SKIP: local BOC CSV sample not found")


def main():
    test_executive_trust_text()
    test_database_category_enforcement()
    test_category_save_shapes_persist()
    test_csv_amounts()
    print("TOP_PRIORITY_QA_COMPLETE")


if __name__ == "__main__":
    main()
