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
for node in functions:
    node.decorator_list = []
exec(compile(ast.Module(body=functions, type_ignores=[]), "app.py", "exec"), globals())
categories = pd.DataFrame([dict(category="Tour Income", subcategory=sub, report_group="Woking Way LLC")
                           for sub in ["Walt Disney", "Other", "Todd Regan"]] +
                          [dict(category="Projects", subcategory="MISSING", report_group="Income")])
if st.checkbox("Long synthetic labels"):
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
view = st.radio("Report", ["Income / Charity", "Reporting groups"], horizontal=True)
with_ai = st.checkbox("Synthetic AI controls")
if st.checkbox("Long group label"):
    rows.loc[rows.report_group.eq("Woking Way LLC"), "report_group"] = "Investing to group companies/projects"
    categories.loc[categories.report_group.eq("Woking Way LLC"), "report_group"] = "Investing to group companies/projects"
if view == "Income / Charity":
    _render_income_charity_section(rows, months, labels, show_all_months=True, editable=True)
else:
    _render_executive_drilldown(rows, months, labels, categories_df=categories,
                              visible_report_groups=[rows.report_group.iloc[0], "Income", "Synthetic empty group"],
                              ai_prompts={} if with_ai else None,
                              show_all_months=True, read_only=True, inline_hierarchy=True,
                              show_zero_explanations=False, show_group_total=True)
