import ast
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
import reporting
from parsing import _parse_amount, parse_csv


def assert_true(name, condition, details=""):
    if not condition:
        raise AssertionError(f"{name} failed. {details}")
    print(f"PASS: {name}" + (f" | {details}" if details else ""))


def _load_report_group_namespace():
    source = Path("app.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    names = {
        "_parse_category_pair_label",
        "_category_pair_label",
        "_apply_category_pair_values",
        "_report_group_subcategory_key",
        "category_pair_report_group_maps",
        "add_report_group_column",
        "report_group_consistency_audit",
        "_refresh_category_pair_derived_columns",
        "_apply_data_editor_state",
    }
    module = ast.Module(
        body=[node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in names],
        type_ignores=[],
    )
    ast.fix_missing_locations(module)
    namespace = {
        "pd": pd,
        "_CATEGORY_PAIR_COLUMN": "category_subcategory",
        "_NO_SUBCATEGORY_LABEL": "No subcategory",
        "st": type("FakeStreamlit", (), {"session_state": {}})(),
    }
    exec(compile(module, "app.py", "exec"), namespace)
    return namespace


def _load_report_group_helpers():
    namespace = _load_report_group_namespace()
    return namespace["add_report_group_column"], namespace["report_group_consistency_audit"]


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


def test_report_group_uses_exact_category_subcategory_pair():
    add_report_group_column, report_group_consistency_audit = _load_report_group_helpers()
    categories_df = pd.DataFrame([
        {"category": "Business travel", "subcategory": "", "report_group": ""},
        {"category": "Business travel", "subcategory": "Dubai", "report_group": "2-business"},
        {"category": "Business expenses", "subcategory": "", "report_group": "2-business"},
        {"category": "Mixed setup", "subcategory": "", "report_group": "1-family"},
        {"category": "Mixed setup", "subcategory": "Needs setup", "report_group": ""},
    ])
    transactions = pd.DataFrame([
        {"id": 1, "category": "Business travel", "subcategory": "Dubai"},
        {"id": 2, "category": "Business expenses", "subcategory": ""},
        {"id": 3, "category": "Business expenses", "subcategory": "No subcategory"},
        {"id": 4, "category": "Mixed setup", "subcategory": "Needs setup"},
    ])
    mapped = add_report_group_column(transactions, categories_df)
    assert_true(
        "exact subcategory report group is used",
        mapped.iloc[0]["report_group"] == "2-business",
        mapped.iloc[0].to_dict(),
    )
    assert_true(
        "blank subcategory uses blank setup row",
        mapped.iloc[1]["report_group"] == "2-business",
        mapped.iloc[1].to_dict(),
    )
    assert_true(
        "legacy no-subcategory text uses blank setup row",
        mapped.iloc[2]["report_group"] == "2-business",
        mapped.iloc[2].to_dict(),
    )
    assert_true(
        "explicit blank setup reporting group uses category fallback",
        mapped.iloc[3]["report_group"] == "1-family",
        mapped.iloc[3].to_dict(),
    )

    audit = report_group_consistency_audit(transactions, categories_df)
    assert_true(
        "blank exact setup reporting group fallback is audited",
        "Category/subcategory exists in Setup with blank reporting group; using category fallback" in audit["status"].tolist(),
        audit.to_dict("records"),
    )


def test_editor_refresh_updates_derived_report_group_before_save():
    namespace = _load_report_group_namespace()
    refresh = namespace["_refresh_category_pair_derived_columns"]
    categories_df = pd.DataFrame([
        {"category": "Business exps General", "subcategory": "", "report_group": "2-business"},
        {"category": "Lifestyle", "subcategory": "", "report_group": "1-family"},
        {"category": "Lifestyle", "subcategory": "Beauty and SPA", "report_group": "1-family"},
    ])
    editor_rows = pd.DataFrame([
        {
            "id": 1,
            "category": "Lifestyle",
            "subcategory": "",
            "category_subcategory": "Business exps General / No subcategory",
            "report_group": "",
        },
        {
            "id": 2,
            "category": "Business exps General",
            "subcategory": "",
            "category_subcategory": "Lifestyle / Beauty and SPA",
            "report_group": "stale group",
        },
    ])
    refreshed = refresh(editor_rows, categories_df)
    first = refreshed.iloc[0].to_dict()
    second = refreshed.iloc[1].to_dict()
    assert_true("editor category changes before save", first["category"] == "Business exps General", first)
    assert_true("editor no-subcategory clears before save", first["subcategory"] == "", first)
    assert_true("editor report group refreshes before save", first["report_group"] == "2-business", first)
    assert_true("editor subcategory changes before save", second["subcategory"] == "Beauty and SPA", second)
    assert_true("stale report group is replaced before save", second["report_group"] == "1-family", second)


def test_captured_editor_state_is_used_before_save():
    namespace = _load_report_group_namespace()
    apply_editor_state = namespace["_apply_data_editor_state"]
    refresh = namespace["_refresh_category_pair_derived_columns"]
    fake_st = namespace["st"]
    fake_st.session_state.clear()
    editor_key = "database_editor_uat"
    fake_st.session_state[f"{editor_key}__captured_state"] = {
        "edited_rows": {1: {"category_subcategory": "Business exps General / Dubai"}},
        "added_rows": [],
        "deleted_rows": [],
    }
    categories_df = pd.DataFrame([
        {"category": "Business exps General", "subcategory": "", "report_group": "2-business"},
        {"category": "Business exps General", "subcategory": "Dubai", "report_group": "2-business Dubai"},
        {"category": "Lifestyle", "subcategory": "", "report_group": "1-family"},
    ])
    editor_rows = pd.DataFrame([
        {
            "id": 1,
            "category": "Lifestyle",
            "subcategory": "",
            "category_subcategory": "Lifestyle / No subcategory",
            "report_group": "1-family",
        },
        {
            "id": 2,
            "category": "Lifestyle",
            "subcategory": "",
            "category_subcategory": "Lifestyle / No subcategory",
            "report_group": "1-family",
        },
    ])
    resolved = refresh(apply_editor_state(editor_rows, editor_key), categories_df)
    edited = resolved.iloc[1].to_dict()
    assert_true("captured editor state changes category", edited["category"] == "Business exps General", edited)
    assert_true("captured editor state changes subcategory", edited["subcategory"] == "Dubai", edited)
    assert_true("captured editor state refreshes report group", edited["report_group"] == "2-business Dubai", edited)


def test_sample_report_uses_category_subcategory_mapping():
    categories_df = pd.DataFrame([
        {"category": "Business exps General", "subcategory": "", "report_group": "2-business"},
        {"category": "Business travel", "subcategory": "", "report_group": ""},
        {"category": "Business travel", "subcategory": "Dubai", "report_group": "2-business travel"},
    ])
    transactions = pd.DataFrame([
        {
            "id": 1,
            "txn_date": "2026-06-01",
            "amount": -5.02,
            "amount_usd": -5.02,
            "currency": "USD",
            "category": "Business exps General",
            "subcategory": "",
        },
        {
            "id": 2,
            "txn_date": "2026-06-02",
            "amount": -100,
            "amount_usd": -100,
            "currency": "USD",
            "category": "Business travel",
            "subcategory": "Dubai",
        },
    ])
    prepared, expenses, _, _ = reporting._prepare_report_data(
        transactions,
        categories_df,
        include_all_valid=True,
    )
    groups = prepared.set_index("id")["report_group"].to_dict()
    assert_true("sample report no-subcategory uses category fallback", groups[1] == "2-business", groups)
    assert_true("sample report exact subcategory group is used", groups[2] == "2-business travel", groups)
    assert_true("sample report expenses keep refreshed groups", set(expenses["report_group"]) == {"2-business", "2-business travel"}, expenses.to_dict("records"))


def test_report_group_audit_flags_missing_setup_pairs():
    _, report_group_consistency_audit = _load_report_group_helpers()
    categories_df = pd.DataFrame([
        {"category": "Business travel", "subcategory": "Dubai", "report_group": ""},
        {"category": "Known category", "subcategory": "", "report_group": "1-family"},
    ])
    transactions = pd.DataFrame([
        {"id": 10, "category": "Known category", "subcategory": "Not in setup"},
        {"id": 11, "category": "Unknown category", "subcategory": ""},
    ])
    audit = report_group_consistency_audit(transactions, categories_df)
    statuses = audit["status"].tolist()
    assert_true(
        "category fallback is visible for non-setup pair",
        "Category/subcategory pair is not in Setup; using category fallback" in statuses,
        audit.to_dict("records"),
    )
    assert_true(
        "unknown category is visible in audit",
        "Category is not in Setup" in statuses,
        audit.to_dict("records"),
    )


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
    test_report_group_uses_exact_category_subcategory_pair()
    test_editor_refresh_updates_derived_report_group_before_save()
    test_captured_editor_state_is_used_before_save()
    test_sample_report_uses_category_subcategory_mapping()
    test_report_group_audit_flags_missing_setup_pairs()
    test_csv_amounts()
    print("TOP_PRIORITY_QA_COMPLETE")


if __name__ == "__main__":
    main()
