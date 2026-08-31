import ast
from io import BytesIO
import math
import os
import sys
from pathlib import Path

os.environ.pop("DATABASE_URL", None)
os.environ.pop("POSTGRES_URL", None)
os.environ["ARETI_DB_PATH"] = str(Path("_qa_pending_amount_override.db").resolve())
sys.path.insert(0, str(Path("_qa_deps").resolve()))

import pandas as pd

import db
import reporting


ASSERTIONS = 0


def check(name, condition, details=""):
    global ASSERTIONS
    ASSERTIONS += 1
    if not condition:
        raise AssertionError(f"{name} failed. {details}")
    print(f"PASS: {name}" + (f" | {details}" if details else ""))


def load_prepare_function():
    tree = ast.parse(Path("app.py").read_text(encoding="utf-8"))
    names = {
        "_parse_category_pair_label",
        "_category_pair_label",
        "_apply_category_pair_values",
        "_report_group_subcategory_key",
        "category_pair_report_group_maps",
        "add_report_group_column",
        "_refresh_category_pair_derived_columns",
        "_prepare_pending_review_save_rows",
    }
    module = ast.Module(
        body=[node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in names],
        type_ignores=[],
    )
    ast.fix_missing_locations(module)
    namespace = {
        "math": math,
        "MAX_SAFE_FINANCIAL_AMOUNT": db.MAX_SAFE_FINANCIAL_AMOUNT,
        "pd": pd,
        "_CATEGORY_PAIR_COLUMN": "category_subcategory",
        "_NO_SUBCATEGORY_LABEL": "No subcategory",
    }
    exec(compile(module, "app.py", "exec"), namespace)
    return namespace["_prepare_pending_review_save_rows"]


def reset_db():
    path = Path(os.environ["ARETI_DB_PATH"])
    if path.exists():
        path.unlink()
    db.init_db()
    db.add_category("Income", "Refund", "1-income")
    db.add_category("Business", "Software", "2-business")


def insert_pending(amount, currency="USD", fx_rate=1.0, description="AMAZON incoming payment"):
    conn = db.get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            """
            INSERT INTO statement_imports
            (statement_hash, statement_name, imported_at, transaction_count)
            VALUES (?, ?, ?, ?)
            """,
            (f"statement-{description}", f"{description}.csv", "2026-08-31T10:00:00", 1),
        )
        cur.execute(
            """
            INSERT INTO classified_transactions
            (statement_hash, statement_name, row_hash, txn_date, original_description,
             normalized_description, amount, currency, rate_type, fx_rate, amount_usd,
             account_name, bank, account_number, category, subcategory, reviewed, status, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 'pending', ?)
            """,
            (
                f"statement-{description}", f"{description}.csv", f"row-{description}",
                "2026-08-20", description, description.lower(), amount, currency,
                "USD/USD" if currency == "USD" else f"{currency}/USD", fx_rate,
                round(amount * fx_rate, 2), "QA", "QA Bank", "QA-1", "Income", "Refund",
                "2026-08-31T10:00:00",
            ),
        )
        row_id = int(cur.lastrowid)
        conn.commit()
        return row_id
    finally:
        conn.close()


def row(row_id):
    conn = db.get_connection()
    try:
        return pd.read_sql_query(
            "SELECT * FROM classified_transactions WHERE id = ?", conn, params=(row_id,)
        ).iloc[0]
    finally:
        conn.close()


def prepared_save(row_id, new_amount):
    prepare = load_prepare_function()
    original = pd.DataFrame([row(row_id)])
    edited = original.copy()
    edited["amount"] = new_amount
    edited["reviewed"] = True
    edited["category_subcategory"] = "Income / Refund"
    return prepare(original, edited, db.get_categories(include_subcategories=True))


def test_usd_sign_correction_and_provenance():
    reset_db()
    row_id = insert_pending(-237.0)
    before = row(row_id)
    conn = db.get_connection()
    try:
        row_count_before = int(conn.execute("SELECT COUNT(*) FROM classified_transactions").fetchone()[0])
    finally:
        conn.close()
    save_df = prepared_save(row_id, 237.0)
    check("USD correction saves one row", db.save_reviewed_rows(save_df) == 1)
    after = row(row_id)
    check("authoritative amount sign corrected", after["amount"] == 237.0)
    check("USD reporting amount sign corrected", after["amount_usd"] == 237.0)
    check("row hash remains immutable", after["row_hash"] == before["row_hash"])
    check("statement hash remains immutable", after["statement_hash"] == before["statement_hash"])
    check("source description remains immutable", after["original_description"] == before["original_description"])
    conn = db.get_connection()
    try:
        audit = pd.read_sql_query(
            "SELECT field_name, old_value, new_value FROM transaction_change_log WHERE transaction_id = ?",
            conn,
            params=(row_id,),
        )
        imports = pd.read_sql_query("SELECT * FROM statement_imports", conn)
    finally:
        conn.close()
    amount_audit = audit[audit["field_name"] == "amount"].iloc[0]
    check("old imported operational amount retained in audit", float(amount_audit["old_value"]) == -237.0)
    check("corrected amount retained in audit", float(amount_audit["new_value"]) == 237.0)
    check("statement import fingerprint remains registered", len(imports) == 1)
    duplicate_frame = pd.DataFrame([{
        "Date": "2026-08-20",
        "Description": "AMAZON incoming payment",
        "Amount": -237.0,
        "normalized_description": "amazon incoming payment",
        "currency": "USD",
        "rate_type": "USD/USD",
        "fx_rate": 1.0,
        "amount_usd": -237.0,
    }])
    inserted, duplicate_statement, _ = db.save_pending_transactions(
        duplicate_frame,
        "AMAZON incoming payment.csv",
        "statement-AMAZON incoming payment",
    )
    check("same source statement remains blocked after correction", inserted == 0 and duplicate_statement)
    conn = db.get_connection()
    try:
        row_count_after = int(conn.execute("SELECT COUNT(*) FROM classified_transactions").fetchone()[0])
    finally:
        conn.close()
    check("manual correction creates no transaction", row_count_after == row_count_before)
    report_tx, _, _, _ = reporting._prepare_report_data(
        pd.DataFrame([after]), db.get_categories(include_subcategories=True), include_all_valid=True
    )
    check("report consumes corrected positive USD amount", float(report_tx.iloc[0]["report_amount"]) == 237.0)
    item_19 = reporting.income_charity_scope(report_tx)
    check(
        "Item 19 consumes corrected Income amount",
        len(item_19) == 1
        and item_19.iloc[0]["income_charity_type"] == "Income"
        and float(item_19.iloc[0]["report_amount"]) == 237.0,
    )
    export_bytes = db.dataframe_to_excel_bytes({"Transactions": pd.DataFrame([after])})
    exported = pd.read_excel(BytesIO(export_bytes), sheet_name="Transactions")
    check("Excel export contains corrected amount", float(exported.iloc[0]["amount"]) == 237.0)
    check("Excel export contains corrected USD amount", float(exported.iloc[0]["amount_usd"]) == 237.0)


def test_non_usd_fx_and_stale_protection():
    reset_db()
    row_id = insert_pending(-200.0, currency="EUR", fx_rate=1.085, description="EUR incoming")
    save_df = prepared_save(row_id, 200.0)
    check("EUR correction saves one row", db.save_reviewed_rows(save_df) == 1)
    after = row(row_id)
    check("EUR amount corrected", after["amount"] == 200.0)
    check("stored FX rate recomputes USD", after["amount_usd"] == 217.0)

    stale_id = insert_pending(-10.0, description="stale amount")
    stale_save = prepared_save(stale_id, 10.0)
    conn = db.get_connection()
    try:
        conn.execute("UPDATE classified_transactions SET amount = -11, amount_usd = -11 WHERE id = ?", (stale_id,))
        conn.commit()
    finally:
        conn.close()
    rejected = False
    try:
        db.save_reviewed_rows(stale_save)
    except db.ConcurrentTransactionEditError:
        rejected = True
    check("stale amount edit rejected", rejected)
    stale_after = row(stale_id)
    check("newer database amount preserved", stale_after["amount"] == -11.0)
    check("stale rejection is atomic", not bool(stale_after["reviewed"]))


def test_invalid_split_balance_and_untouched_rows():
    reset_db()
    valid_id = insert_pending(-25.25, description="unchanged row")
    untouched_before = row(valid_id)
    save_df = prepared_save(valid_id, -25.25)
    db.save_reviewed_rows(save_df)
    untouched_after = row(valid_id)
    check("untouched amount remains exact", untouched_after["amount"] == untouched_before["amount"])
    check("untouched USD amount remains exact", untouched_after["amount_usd"] == untouched_before["amount_usd"])

    split_id = insert_pending(-100.0, description="split-linked row")
    conn = db.get_connection()
    try:
        conn.execute(
            "UPDATE classified_transactions SET split_parent_id = 999, split_group_id = 'split-qa', split_allocation_index = 1 WHERE id = ?",
            (split_id,),
        )
        conn.execute(
            """
            INSERT INTO statement_balances
            (statement_hash, statement_name, opening_balance, money_out, money_in, closing_balance, imported_at, updated_at)
            VALUES ('balance-hash', 'balance.csv', 1000, -100, 0, 900, 'now', 'now')
            """
        )
        conn.commit()
        balances_before = pd.read_sql_query("SELECT * FROM statement_balances", conn)
    finally:
        conn.close()
    split_save = prepared_save(split_id, -90.0)
    split_rejected = False
    try:
        db.save_reviewed_rows(split_save)
    except ValueError as exc:
        split_rejected = "linked to a split" in str(exc)
    check("split-linked amount correction rejected", split_rejected)
    check("split-linked row remains unchanged", row(split_id)["amount"] == -100.0)

    conn = db.get_connection()
    try:
        balances_after = pd.read_sql_query("SELECT * FROM statement_balances", conn)
    finally:
        conn.close()
    check("statement balances are untouched", balances_after.equals(balances_before))

    invalid_id = insert_pending(-5.0, description="invalid amount")
    for invalid in [
        None,
        "",
        "not-a-number",
        float("nan"),
        float("inf"),
        float("-inf"),
        db.MAX_SAFE_FINANCIAL_AMOUNT + 1,
    ]:
        rejected = False
        try:
            prepared_save(invalid_id, invalid)
        except ValueError:
            rejected = True
        check(f"invalid amount rejected: {invalid!r}", rejected)
    for valid in [25, -25, 25.67, -25.67, 0]:
        prepared = prepared_save(invalid_id, valid)
        check(f"valid amount accepted: {valid}", float(prepared.iloc[0]["amount"]) == round(float(valid), 2))


if __name__ == "__main__":
    try:
        test_usd_sign_correction_and_provenance()
        test_non_usd_fx_and_stale_protection()
        test_invalid_split_balance_and_untouched_rows()
        print(f"PASS: pending amount override QA | assertions={ASSERTIONS}")
    finally:
        Path(os.environ["ARETI_DB_PATH"]).unlink(missing_ok=True)
