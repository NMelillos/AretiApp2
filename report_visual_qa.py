"""Local synthetic rendering of actual report functions, without app startup."""
import ast
import copy
import hashlib
from html import escape
from io import BytesIO
import json
import math
import os
from pathlib import Path
import re
import sys
from datetime import datetime, timedelta, timezone
sys.path.insert(0, str(Path(__file__).resolve().parent))
if os.getenv("DATABASE_URL") or os.getenv("POSTGRES_URL"):
    raise RuntimeError("Visual QA refuses database URLs")
for variable in ("TEMP", "ARETI_SHARED_FOLDER", "ARETI_DB_PATH"):
    if Path(os.environ.get(variable, "")).drive.upper() != "E:":
        raise RuntimeError(f"Visual QA requires {variable} on E:")
import pandas as pd
import streamlit as st
import importlib
import analytical_layout
importlib.reload(analytical_layout)
from db import dataframe_to_excel_bytes
from utils import format_currency

st.set_page_config(layout="wide")
source = Path(__file__).with_name("app.py").read_text(encoding="utf-8")
if os.getenv("ARETI_VISUAL_BASELINE"):
    import subprocess
    if os.environ["ARETI_VISUAL_BASELINE"] != "becf943051d732741ef7b50335f642bbf444897d":
        raise RuntimeError("Only the verified pre-sizing baseline is supported")
    source = subprocess.check_output(["git", "show", os.environ["ARETI_VISUAL_BASELINE"] + ":app.py"]).decode("utf-8")
tree = ast.parse(source)
for node in tree.body:
    if isinstance(node, ast.Assign):
        try:
            value = ast.literal_eval(node.value)
        except (ValueError, TypeError):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name):
                globals()[target.id] = value
    if isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
        call = node.value
        if isinstance(call.func, ast.Attribute) and call.func.attr == "markdown" and call.args:
            if isinstance(call.args[0], ast.Constant) and "<style>" in str(call.args[0].value):
                st.markdown(call.args[0].value, unsafe_allow_html=True)
functions = [node for node in tree.body if isinstance(node, ast.FunctionDef)]
cached_readers = {"get_all_transactions", "get_dashboard_counts", "get_memory",
                  "get_pending_transactions", "get_saved_transactions", "get_transaction_change_log"}
for node in functions:
    if node.name not in cached_readers:
        node.decorator_list = []
exec(compile(ast.Module(body=functions, type_ignores=[]), "app.py", "exec"), globals())
categories = pd.DataFrame([dict(category="Tour Income", subcategory=sub, report_group="Woking Way LLC")
                           for sub in ["Walt Disney", "Other", "Todd Regan"]] +
                          [dict(category="Projects", subcategory="MISSING", report_group="Income")])
if st.checkbox("Long synthetic labels"):
    categories.loc[categories.category.eq("Tour Income"), "category"] = "Synthetic International Development and Administration Income"
    categories.loc[0, "subcategory"] = "Synthetic International Development and Administration"
get_categories = lambda **kwargs: categories.copy()
def save_reviewed_rows(_rows):
    raise RuntimeError("Visual fixture is read-only; use the isolated persistence QA for Save")
months = list(pd.period_range("2026-01", periods=2, freq="M"))
labels = _executive_month_labels(months)
rows = pd.DataFrame([dict(id=i + 1, category=row.category, subcategory=row.subcategory,
                          report_group=row.report_group, txn_date="2026-02-01", month=months[-1],
                          report_amount=(i+1)*100, amount=(i+1)*100, amount_usd=(i+1)*100,
                          currency="USD", original_description="Synthetic training transaction",
                          account_name="Synthetic account", status="reviewed", reviewed=1)
                     for i, row in enumerate(categories.itertuples())])
st.caption("LOCAL SYNTHETIC QA ONLY - no production connection")
view = st.radio("Report", ["Income / Charity", "Reporting groups", "THIRD"], horizontal=True)
if st.checkbox("Enable isolated save QA", value=bool(st.query_params.get("fixture"))):
    import db
    import reporting
    import tempfile
    from contextlib import closing
    fixture_name = st.query_params.get("fixture", "")
    if fixture_name and "synthetic_save_database" not in st.session_state:
        if not re.fullmatch(r"visual-save-[a-z0-9_]+", fixture_name):
            raise RuntimeError("Invalid synthetic fixture identity")
        fixture_path = Path(os.environ["TEMP"]) / fixture_name / "synthetic.sqlite"
        if not fixture_path.is_file():
            raise RuntimeError("Synthetic fixture is unavailable")
        st.session_state.synthetic_save_database = str(fixture_path)
    if "synthetic_save_database" not in st.session_state:
        folder = tempfile.mkdtemp(prefix="visual-save-", dir=os.environ["TEMP"])
        st.session_state.synthetic_save_database = str(Path(folder) / "synthetic.sqlite")
        db.DB_PATH = st.session_state.synthetic_save_database
        assert not db.USING_POSTGRES
        db.init_db()
        for item in categories.itertuples():
            db.add_category(item.category, item.subcategory, item.report_group)
        db.add_category("Income", "Original", "Income")
        initial = rows.copy()
        initial.loc[initial.report_group.eq("Income"), ["category", "subcategory"]] = ["Income", "Original"]
        with closing(db.get_connection()) as connection, connection:
            for item in initial.itertuples():
                connection.execute("""INSERT INTO classified_transactions
                    (row_hash,txn_date,amount,amount_usd,currency,category,subcategory,
                     original_description,account_name,status,reviewed)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                    (f"visual-synthetic-{item.id}",item.txn_date,item.amount,item.amount_usd,
                     item.currency,item.category,item.subcategory,item.original_description,
                     item.account_name,item.status,item.reviewed))
        st.query_params["fixture"] = Path(folder).name
    db.DB_PATH = st.session_state.synthetic_save_database
    assert not db.USING_POSTGRES and Path(db.DB_PATH).drive.upper() == "E:"
    categories = db.get_categories(include_subcategories=True)
    get_categories = lambda **kwargs: categories.copy()
    save_reviewed_rows = db.save_reviewed_rows
    with closing(db.get_connection()) as connection:
        stored = pd.read_sql_query("SELECT * FROM classified_transactions", connection)
    _, rows, _, _ = reporting._prepare_report_data(stored, categories, include_all_valid=True)
    from review_state import counts
    counters = counts(stored)
    st.caption(f"Synthetic visible rows: {len(stored)} | Pending: {counters['pending']} | Reviewed: {counters['reviewed']}")
with_ai = st.checkbox("Synthetic AI controls")
if st.checkbox("Long group label"):
    rows.loc[rows.report_group.eq("Woking Way LLC"), "report_group"] = "Investing to group companies/projects"
    categories.loc[categories.report_group.eq("Woking Way LLC"), "report_group"] = "Investing to group companies/projects"
if view == "Income / Charity":
    _render_income_charity_section(rows, months, labels, show_all_months=True, editable=True)
else:
    third = next(node for node in functions if node.name == "render_third_link_report")
    call = next(node for node in ast.walk(third) if isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name) and node.func.id == "_render_executive_drilldown")
    third_inline = next((ast.literal_eval(item.value) for item in call.keywords
                         if item.arg == "inline_hierarchy"), False)
    _render_executive_drilldown(rows, months, labels, categories_df=categories,
                              visible_report_groups=[rows.report_group.iloc[0], "Income", "Synthetic empty group"],
                              ai_prompts={} if with_ai else None,
                              show_all_months=True, read_only=True,
                              inline_hierarchy=third_inline if view == "THIRD" else True,
                              show_zero_explanations=False, show_group_total=True)
