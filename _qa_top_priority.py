import ast
import copy
import os
import sys
import time
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
        "_edited_data_editor_rows",
        "_capture_data_editor_state",
        "_clear_data_editor_state",
        "_add_transaction_edit_expectations",
        "_prepare_pending_review_save_rows",
    }
    module = ast.Module(
        body=[node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in names],
        type_ignores=[],
    )
    ast.fix_missing_locations(module)
    namespace = {
        "copy": copy,
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


def test_concurrent_transaction_edits_are_detected_and_atomic():
    qa_db = Path(os.environ["ARETI_DB_PATH"])
    if qa_db.exists():
        qa_db.unlink()
    db.init_db()
    db.add_category("Original", "", "1-family")
    db.add_category("First edit", "", "1-family")
    db.add_category("Second edit", "", "1-family")
    for description in ["Concurrent edit one", "Concurrent edit two"]:
        db.insert_manual_transaction(
            "2026-06-01",
            description,
            10,
            "Original",
            "",
            {
                "account_name": "QA Account",
                "bank": "QA Bank",
                "account_number": "QA1",
                "currency": "USD",
                "rate_type": "USD/USD",
            },
        )
    states = db.get_all_transactions().sort_values("id").reset_index(drop=True)
    ids = states["id"].astype(int).tolist()

    first_update = pd.DataFrame([{
        "id": ids[0],
        "category": "First edit",
        "subcategory": "",
        "reviewed": True,
        "status": "reviewed",
        "_expected_category": "Original",
        "_expected_subcategory": "",
        "_expected_reviewed": True,
    }])
    assert_true("compare-before-save accepts current row", db.update_database_rows(first_update) == 1)
    persisted = db.get_transaction_edit_states([ids[0]]).iloc[0].to_dict()
    assert_true("compare-before-save persists intended category", persisted["category"] == "First edit", persisted)

    stale_batch = pd.DataFrame([
        {
            "id": ids[1],
            "category": "First edit",
            "subcategory": "",
            "reviewed": True,
            "status": "reviewed",
            "_expected_category": "Original",
            "_expected_subcategory": "",
            "_expected_reviewed": True,
        },
        {
            "id": ids[0],
            "category": "Second edit",
            "subcategory": "",
            "reviewed": True,
            "status": "reviewed",
            "_expected_category": "Original",
            "_expected_subcategory": "",
            "_expected_reviewed": True,
        },
    ])
    conflict_detected = False
    try:
        db.update_database_rows(stale_batch)
    except db.ConcurrentTransactionEditError as exc:
        conflict_detected = exc.transaction_id == ids[0]
    assert_true("stale tab edit is rejected", conflict_detected)
    after_conflict = db.get_transaction_edit_states(ids).set_index("id")
    assert_true(
        "conflicting multi-row save rolls back earlier rows",
        after_conflict.loc[ids[1], "category"] == "Original",
        after_conflict.reset_index().to_dict("records"),
    )
    assert_true(
        "newer committed value is preserved",
        after_conflict.loc[ids[0], "category"] == "First edit",
        after_conflict.reset_index().to_dict("records"),
    )

    independent_update = pd.DataFrame([{
        "id": ids[1],
        "category": "Second edit",
        "subcategory": "",
        "reviewed": True,
        "status": "reviewed",
        "_expected_category": "Original",
        "_expected_subcategory": "",
        "_expected_reviewed": True,
    }])
    assert_true(
        "different-record tab edit remains independent",
        db.update_database_rows(independent_update) == 1,
    )
    after_independent = db.get_transaction_edit_states(ids).set_index("id")
    assert_true(
        "different-record edit preserves both committed values",
        after_independent.loc[ids[0], "category"] == "First edit"
        and after_independent.loc[ids[1], "category"] == "Second edit",
        after_independent.reset_index().to_dict("records"),
    )


def test_legacy_whitespace_does_not_create_false_conflict():
    qa_db = Path(os.environ["ARETI_DB_PATH"])
    if qa_db.exists():
        qa_db.unlink()
    db.init_db()
    db.add_category("Original", "", "1-family")
    db.add_category("Corrected", "", "1-family")
    db.insert_manual_transaction(
        "2026-08-12",
        "Legacy whitespace conflict",
        -10,
        "Original",
        "",
        {
            "account_name": "QA Account",
            "bank": "QA Bank",
            "account_number": "QA1",
            "currency": "USD",
            "rate_type": "USD/USD",
        },
    )
    row_id = int(db.get_all_transactions().iloc[0]["id"])
    conn = db.get_connection()
    try:
        conn.cursor().execute(
            "UPDATE classified_transactions SET category = ?, subcategory = ? WHERE id = ?",
            ("Original \t", None, row_id),
        )
        conn.commit()
    finally:
        conn.close()
    update = pd.DataFrame([{
        "id": row_id,
        "category": "Corrected",
        "subcategory": "",
        "reviewed": True,
        "status": "reviewed",
        "_expected_category": "Original",
        "_expected_subcategory": "",
        "_expected_reviewed": True,
    }])
    assert_true("legacy whitespace does not trigger false conflict", db.update_database_rows(update) == 1)
    persisted = db.get_transaction_edit_states([row_id]).iloc[0]
    assert_true("whitespace-normalized edit persists", persisted["category"] == "Corrected", persisted.to_dict())


def test_database_editor_stale_tab_is_blocked_one_hundred_times():
    qa_db = Path(os.environ["ARETI_DB_PATH"])
    if qa_db.exists():
        qa_db.unlink()
    db.init_db()
    for category in ["Original", "Current", "Stale"]:
        db.add_category(category, "", "1-family")
    add_expectations = _load_report_group_namespace()["_add_transaction_edit_expectations"]
    for attempt in range(100):
        db.insert_manual_transaction(
            "2026-08-12",
            f"Database stale tab UAT {attempt}",
            -10 - attempt,
            "Original",
            "",
            {
                "account_name": "QA Account",
                "bank": "QA Bank",
                "account_number": "QA1",
                "currency": "USD",
                "rate_type": "USD/USD",
            },
        )
    rows = db.get_all_transactions().sort_values("id").reset_index(drop=True)
    for attempt, original in rows.iterrows():
        row_id = int(original["id"])
        baseline = pd.DataFrame([original.to_dict()])
        current = add_expectations(pd.DataFrame([{
            "id": row_id,
            "category": "Current",
            "subcategory": "",
            "reviewed": True,
            "status": "reviewed",
        }]), baseline)
        assert_true(f"current tab edit saves {attempt + 1}", db.update_database_rows(current) == 1)
        stale = add_expectations(pd.DataFrame([{
            "id": row_id,
            "category": "Stale",
            "subcategory": "",
            "reviewed": True,
            "status": "reviewed",
        }]), baseline)
        blocked = False
        try:
            db.update_database_rows(stale)
        except db.ConcurrentTransactionEditError:
            blocked = True
        assert_true(f"database stale tab is blocked {attempt + 1}", blocked)
        persisted = db.get_transaction_edit_states([row_id]).iloc[0]
        assert_true(
            f"newer manual correction remains authoritative {attempt + 1}",
            persisted["category"] == "Current",
            persisted.to_dict(),
        )


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


def test_raw_category_subcategory_edits_sync_pair_before_save():
    namespace = _load_report_group_namespace()
    apply_editor_state = namespace["_apply_data_editor_state"]
    refresh = namespace["_refresh_category_pair_derived_columns"]
    fake_st = namespace["st"]
    fake_st.session_state.clear()
    editor_key = "executive_detail_editor_uat"
    fake_st.session_state[f"{editor_key}__captured_state"] = {
        "edited_rows": {0: {"subcategory": "Dubai"}},
        "added_rows": [],
        "deleted_rows": [],
    }
    categories_df = pd.DataFrame([
        {"category": "Business exps General", "subcategory": "", "report_group": "2-business"},
        {"category": "Business exps General", "subcategory": "Dubai", "report_group": "2-business Dubai"},
    ])
    editor_rows = pd.DataFrame([
        {
            "id": 1,
            "category": "Business exps General",
            "subcategory": "",
            "category_subcategory": "Business exps General / No subcategory",
            "report_group": "2-business",
        },
    ])
    resolved = refresh(apply_editor_state(editor_rows, editor_key), categories_df)
    edited = resolved.iloc[0].to_dict()
    assert_true("raw subcategory edit updates helper pair", edited["category_subcategory"] == "Business exps General / Dubai", edited)
    assert_true("raw subcategory edit persists through refresh", edited["subcategory"] == "Dubai", edited)
    assert_true("raw subcategory edit refreshes report group", edited["report_group"] == "2-business Dubai", edited)


def test_only_changed_editor_rows_are_saved_and_state_is_cleared():
    namespace = _load_report_group_namespace()
    edited_rows = namespace["_edited_data_editor_rows"]
    clear_state = namespace["_clear_data_editor_state"]
    fake_st = namespace["st"]
    fake_st.session_state.clear()
    editor_key = "executive_detail_editor_save_scope_uat"
    fake_st.session_state[f"{editor_key}__captured_state"] = {
        "edited_rows": {1: {"category_subcategory": "Business exps General / Dubai"}},
        "added_rows": [],
        "deleted_rows": [],
    }
    frame = pd.DataFrame([
        {"id": 1, "category": "Lifestyle", "subcategory": ""},
        {"id": 2, "category": "Business exps General", "subcategory": "Dubai"},
        {"id": 3, "category": "Own funds", "subcategory": ""},
    ])
    changed = edited_rows(frame, editor_key)
    assert_true("only edited row is selected for save", changed["id"].tolist() == [2], changed.to_dict("records"))
    clear_state(editor_key)
    assert_true(
        "editor state is cleared after save",
        editor_key not in fake_st.session_state
        and f"{editor_key}__captured_state" not in fake_st.session_state,
        fake_st.session_state,
    )


def test_empty_rerun_callback_preserves_edit_fifty_times():
    namespace = _load_report_group_namespace()
    capture_state = namespace["_capture_data_editor_state"]
    fake_st = namespace["st"]
    for attempt in range(50):
        fake_st.session_state.clear()
        editor_key = f"executive_detail_editor_rerun_uat_{attempt}"
        selected_pair = f"Lifestyle / Test subcategory {attempt}"
        fake_st.session_state[editor_key] = {
            "edited_rows": {0: {"category_subcategory": selected_pair}},
            "added_rows": [],
            "deleted_rows": [],
        }
        capture_state(editor_key)
        fake_st.session_state[editor_key] = {
            "edited_rows": {},
            "added_rows": [],
            "deleted_rows": [],
        }
        capture_state(editor_key)
        captured = fake_st.session_state[f"{editor_key}__captured_state"]
        assert_true(
            f"empty rerun callback preserves captured edit {attempt + 1}",
            captured["edited_rows"][0]["category_subcategory"] == selected_pair,
            captured,
        )


def test_no_subcategory_apply_pipeline_one_hundred_times():
    qa_db = Path(os.environ["ARETI_DB_PATH"])
    if qa_db.exists():
        qa_db.unlink()
    db.init_db()
    db.add_category("Lifestyle", "", "1-family")
    for attempt in range(100):
        db.add_category("Lifestyle", f"UAT subcategory {attempt}", "1-family")
        db.insert_manual_transaction(
            "2026-06-01",
            f"No subcategory production UAT {attempt}",
            -10,
            "Lifestyle",
            "",
            {
                "account_name": "QA Account",
                "bank": "QA Bank",
                "account_number": "QA1",
                "currency": "USD",
                "rate_type": "USD/USD",
            },
        )

    namespace = _load_report_group_namespace()
    capture_state = namespace["_capture_data_editor_state"]
    apply_editor_state = namespace["_apply_data_editor_state"]
    refresh = namespace["_refresh_category_pair_derived_columns"]
    edited_rows = namespace["_edited_data_editor_rows"]
    clear_state = namespace["_clear_data_editor_state"]
    fake_st = namespace["st"]
    categories_df = db.get_categories(include_subcategories=True)
    rows = db.get_all_transactions().sort_values("id").reset_index(drop=True)

    durations = []
    for attempt, original in rows.iterrows():
        fake_st.session_state.clear()
        editor_key = f"executive_detail_editor_full_pipeline_{attempt}"
        selected_pair = f"Lifestyle / UAT subcategory {attempt}"
        context = pd.DataFrame([{
            "id": int(original["id"]),
            "category": "Lifestyle",
            "subcategory": "",
            "reviewed": True,
            "report_group": "1-family",
            "category_subcategory": "Lifestyle / No subcategory",
        }])
        fake_st.session_state[editor_key] = {
            "edited_rows": {0: {"category_subcategory": selected_pair}},
            "added_rows": [],
            "deleted_rows": [],
        }
        capture_state(editor_key)
        fake_st.session_state[editor_key] = {
            "edited_rows": {},
            "added_rows": [],
            "deleted_rows": [],
        }
        capture_state(editor_key)
        time.sleep(0.005)
        started = time.perf_counter()
        edited = refresh(apply_editor_state(context, editor_key), categories_df)
        changed = edited_rows(edited, editor_key)
        assert_true(f"full pipeline selects exactly one row {attempt + 1}", len(changed) == 1)
        save_df = changed[["id", "category", "subcategory", "reviewed"]].copy()
        save_df["status"] = "reviewed"
        save_df["_expected_category"] = "Lifestyle"
        save_df["_expected_subcategory"] = ""
        save_df["_expected_reviewed"] = True
        assert_true(
            f"full pipeline updates exactly one database row {attempt + 1}",
            db.update_database_rows(save_df) == 1,
        )
        persisted = db.get_transaction_edit_states([int(original["id"])]).iloc[0]
        durations.append(time.perf_counter() - started)
        assert_true(
            f"full pipeline persists selected subcategory {attempt + 1}",
            persisted["subcategory"] == f"UAT subcategory {attempt}",
            persisted.to_dict(),
        )
        assert_true(
            f"updated row leaves no-subcategory filter {attempt + 1}",
            str(persisted["subcategory"] or "").strip() != "",
            persisted.to_dict(),
        )
        mapped = refresh(
            pd.DataFrame([{
                "id": int(original["id"]),
                "category": persisted["category"],
                "subcategory": persisted["subcategory"],
                "report_group": "",
            }]),
            categories_df,
        ).iloc[0]
        assert_true(
            f"full pipeline reporting group is correct {attempt + 1}",
            mapped["report_group"] == "1-family",
            mapped.to_dict(),
        )
        clear_state(editor_key)
        assert_true(
            f"repeated Apply has no duplicate update {attempt + 1}",
            edited_rows(context, editor_key).empty,
        )
    print(
        "PASS: 100-cycle full edit pipeline timing "
        f"| max={max(durations):.6f}s average={sum(durations) / len(durations):.6f}s"
    )


def test_recategorisation_metadata_only_and_pair_variants():
    qa_db = Path(os.environ["ARETI_DB_PATH"])
    if qa_db.exists():
        qa_db.unlink()
    db.init_db()
    for category, subcategory, group in [
        ("Original", "", "0-UNCATEGORISED"),
        ("Family", "", "1-family"),
        ("Family", "General", "1-family"),
        ("Business", "", "2-business"),
        ("Business", "Software", "2-business"),
    ]:
        db.add_category(category, subcategory, group)
    seed_pairs = [("Original", ""), ("Family", ""), ("Original", ""), ("Original", "")]
    for index, (seed_category, seed_subcategory) in enumerate(seed_pairs):
        db.insert_manual_transaction(
            "2026-08-12",
            f"Recategorisation metadata variant {index}",
            -100.25 - index,
            seed_category,
            seed_subcategory,
            {
                "account_name": "QA Account",
                "bank": "QA Bank",
                "account_number": "QA1",
                "currency": "EUR",
                "rate_type": "EUR/USD",
            },
        )
    before = db.get_all_transactions().sort_values("id").reset_index(drop=True)
    financial_columns = [
        "id", "txn_date", "amount", "currency", "rate_type", "fx_rate", "amount_usd",
        "account_name", "bank", "account_number", "statement_hash", "row_hash",
    ]
    financial_before = before[financial_columns].copy()
    variants = [
        ("Family", "", "category-only"),
        ("Family", "General", "subcategory-only"),
        ("Business", "Software", "category-and-subcategory"),
        ("Family", "General", "revert-setup"),
    ]
    for index, (category, subcategory, label) in enumerate(variants):
        original = before.iloc[index]
        payload = pd.DataFrame([{
            "id": int(original["id"]),
            "category": category,
            "subcategory": subcategory,
            "reviewed": True,
            "status": "reviewed",
            "_expected_category": str(original["category"] or "").strip(),
            "_expected_subcategory": str(original["subcategory"] or "").strip(),
            "_expected_reviewed": bool(original["reviewed"]),
        }])
        assert_true(f"{label} updates exactly one row", db.update_database_rows(payload) == 1)
    revert_source = db.get_transaction_edit_states([int(before.iloc[3]["id"])]).iloc[0]
    revert = pd.DataFrame([{
        "id": int(revert_source["id"]),
        "category": "Original",
        "subcategory": "",
        "reviewed": True,
        "status": "reviewed",
        "_expected_category": str(revert_source["category"] or "").strip(),
        "_expected_subcategory": str(revert_source["subcategory"] or "").strip(),
        "_expected_reviewed": bool(revert_source["reviewed"]),
    }])
    assert_true("intentional revert updates exactly one row", db.update_database_rows(revert) == 1)
    after = db.get_all_transactions().sort_values("id").reset_index(drop=True)
    assert_true("recategorisation creates no transactions", len(after) == len(before), (len(before), len(after)))
    assert_true("recategorisation deletes no transactions", set(after["id"]) == set(before["id"]))
    pd.testing.assert_frame_equal(
        financial_before.reset_index(drop=True),
        after[financial_columns].reset_index(drop=True),
        check_dtype=False,
    )
    assert_true("recategorisation changes classification metadata only", True)
    add_report_group_column, _ = _load_report_group_helpers()
    mapped = add_report_group_column(after, db.get_categories(include_subcategories=True)).set_index("id")
    expected_groups = ["1-family", "1-family", "2-business", "0-UNCATEGORISED"]
    assert_true(
        "reporting groups recalculate for every edit variant",
        [mapped.loc[int(before.iloc[index]["id"]), "report_group"] for index in range(4)] == expected_groups,
        mapped[["category", "subcategory", "report_group"]].reset_index().to_dict("records"),
    )
    prepared, _, _, _ = reporting._prepare_report_data(
        after,
        db.get_categories(include_subcategories=True),
        include_all_valid=True,
    )
    report_pairs = prepared.set_index("id")[["category", "subcategory", "report_group"]]
    assert_true(
        "Database and Executive report use the same committed classifications",
        all(
            str(report_pairs.loc[int(row["id"]), "category"] or "").strip()
            == str(row["category"] or "").strip()
            and str(report_pairs.loc[int(row["id"]), "subcategory"] or "").strip()
            == str(row["subcategory"] or "").strip()
            for _, row in after.iterrows()
        ),
        report_pairs.reset_index().to_dict("records"),
    )


def _seed_pending_rows(count):
    db.init_db()
    db.add_category("Original", "", "0-UNCATEGORISED")
    db.add_category("Family", "General", "1-family")
    db.add_category("Business", "Software", "2-business")
    for index in range(count):
        db.insert_manual_transaction(
            "2026-08-01",
            f"Pending batch UAT {index}",
            -10 - index,
            "Original",
            "",
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
        conn.cursor().execute(
            "UPDATE classified_transactions SET reviewed = 0, status = 'pending', reviewed_at = NULL"
        )
        conn.commit()
    finally:
        conn.close()
    return db.get_pending_transactions().sort_values("id").reset_index(drop=True)


def test_pending_review_exact_workflow_fifty_times():
    qa_db = Path(os.environ["ARETI_DB_PATH"])
    if qa_db.exists():
        qa_db.unlink()
    original = _seed_pending_rows(50)
    prepare = _load_report_group_namespace()["_prepare_pending_review_save_rows"]
    categories_df = db.get_categories(include_subcategories=True)
    durations = []
    for attempt, source in original.iterrows():
        editor = pd.DataFrame([{
            **source.to_dict(),
            "category_subcategory": "Family / General",
            "reviewed": True,
        }])
        started = time.perf_counter()
        save_df = prepare(pd.DataFrame([source]), editor, categories_df)
        assert_true(f"pending form selects one stable ID {attempt + 1}", save_df["id"].tolist() == [int(source["id"])])
        assert_true(f"pending save updates one SQL row {attempt + 1}", db.save_reviewed_rows(save_df) == 1)
        persisted = db.get_transaction_edit_states([int(source["id"])]).iloc[0]
        durations.append(time.perf_counter() - started)
        assert_true(
            f"pending category, subcategory and Reviewed persist {attempt + 1}",
            persisted["category"] == "Family"
            and persisted["subcategory"] == "General"
            and bool(persisted["reviewed"])
            and persisted["status"] == "reviewed",
            persisted.to_dict(),
        )
    assert_true("all 50 reviewed rows leave Pending Review", db.get_pending_transactions().empty)
    print(
        "PASS: 50-cycle Pending Review timing "
        f"| max={max(durations):.6f}s average={sum(durations) / len(durations):.6f}s"
    )


def test_pending_review_thirty_row_batch_is_atomic():
    qa_db = Path(os.environ["ARETI_DB_PATH"])
    if qa_db.exists():
        qa_db.unlink()
    original = _seed_pending_rows(30)
    prepare = _load_report_group_namespace()["_prepare_pending_review_save_rows"]
    categories_df = db.get_categories(include_subcategories=True)
    editor = original.copy()
    editor["category_subcategory"] = [
        "Family / General" if index % 2 == 0 else "Business / Software"
        for index in range(len(editor))
    ]
    editor["reviewed"] = True

    invalid = editor.copy()
    invalid.at[10, "category_subcategory"] = "Not in Setup / Invalid"
    invalid_save = prepare(original, invalid, categories_df)
    failed_atomically = False
    try:
        db.save_reviewed_rows(invalid_save)
    except ValueError:
        failed_atomically = True
    assert_true("invalid row rejects the entire 30-row batch", failed_atomically)
    assert_true("failed batch leaves all 30 rows pending", len(db.get_pending_transactions()) == 30)

    started = time.perf_counter()
    save_df = prepare(original, editor, categories_df)
    assert_true("30-row form collects exactly 30 stable IDs", len(save_df) == 30)
    assert_true("30-row batch commits exactly once per intended row", db.save_reviewed_rows(save_df) == 30)
    elapsed = time.perf_counter() - started
    states = db.get_transaction_edit_states(save_df["id"].tolist()).sort_values("id").reset_index(drop=True)
    assert_true("30-row batch leaves no row pending", db.get_pending_transactions().empty)
    assert_true("30-row batch preserves every Reviewed tick", states["reviewed"].astype(bool).all())
    assert_true(
        "30-row batch persists every selected category/subcategory",
        all(
            (row["category"], row["subcategory"])
            == (("Family", "General") if index % 2 == 0 else ("Business", "Software"))
            for index, (_, row) in enumerate(states.iterrows())
        ),
        states.to_dict("records"),
    )
    assert_true("30-row batch completes in seconds", elapsed < 3.0, f"elapsed={elapsed:.6f}s")
    print(f"PASS: 30-row Pending Review batch timing | elapsed={elapsed:.6f}s")


def test_pending_review_stale_tab_cannot_partially_overwrite():
    qa_db = Path(os.environ["ARETI_DB_PATH"])
    if qa_db.exists():
        qa_db.unlink()
    original = _seed_pending_rows(2)
    prepare = _load_report_group_namespace()["_prepare_pending_review_save_rows"]
    categories_df = db.get_categories(include_subcategories=True)

    tab_one = original.iloc[[0]].copy()
    tab_one["category_subcategory"] = "Family / General"
    tab_one["reviewed"] = True
    tab_one_save = prepare(original.iloc[[0]], tab_one, categories_df)
    assert_true("first tab saves its intended row", db.save_reviewed_rows(tab_one_save) == 1)

    stale_tab = original.copy()
    stale_tab["category_subcategory"] = "Business / Software"
    stale_tab["reviewed"] = True
    stale_save = prepare(original, stale_tab, categories_df)
    rejected = False
    try:
        db.save_reviewed_rows(stale_save)
    except db.ConcurrentTransactionEditError:
        rejected = True
    assert_true("stale tab is rejected", rejected)

    states = db.get_transaction_edit_states(original["id"].astype(int).tolist()).sort_values("id").reset_index(drop=True)
    assert_true(
        "stale batch cannot overwrite the newer row or partially save another row",
        states.loc[0, "category"] == "Family"
        and states.loc[0, "subcategory"] == "General"
        and bool(states.loc[0, "reviewed"])
        and states.loc[1, "category"] == "Original"
        and states.loc[1, "subcategory"] == ""
        and not bool(states.loc[1, "reviewed"]),
        states.to_dict("records"),
    )


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
    test_concurrent_transaction_edits_are_detected_and_atomic()
    test_legacy_whitespace_does_not_create_false_conflict()
    test_database_editor_stale_tab_is_blocked_one_hundred_times()
    test_report_group_uses_exact_category_subcategory_pair()
    test_editor_refresh_updates_derived_report_group_before_save()
    test_captured_editor_state_is_used_before_save()
    test_raw_category_subcategory_edits_sync_pair_before_save()
    test_only_changed_editor_rows_are_saved_and_state_is_cleared()
    test_empty_rerun_callback_preserves_edit_fifty_times()
    test_no_subcategory_apply_pipeline_one_hundred_times()
    test_recategorisation_metadata_only_and_pair_variants()
    test_pending_review_exact_workflow_fifty_times()
    test_pending_review_thirty_row_batch_is_atomic()
    test_pending_review_stale_tab_cannot_partially_overwrite()
    test_sample_report_uses_category_subcategory_mapping()
    test_report_group_audit_flags_missing_setup_pairs()
    test_csv_amounts()
    print("TOP_PRIORITY_QA_COMPLETE")


if __name__ == "__main__":
    main()
