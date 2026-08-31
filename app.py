from datetime import datetime, timedelta, timezone
import copy
import hashlib
from html import escape
from io import BytesIO
import json
import math
import os
from pathlib import Path
import re
import time
import urllib.error
import urllib.request
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
import zipfile

import streamlit as st

from auth import (
    get_login_user,
    get_third_report_user,
    require_login,
    require_third_report_login,
    sign_out,
    sign_out_third_report,
)

THIRD_LINK_REPORT_PAGE = "TB & NF Family Office Report"
THIRD_LINK_REPORT_PAGES = {THIRD_LINK_REPORT_PAGE, "Family Office Report", "TB & NF Family Office Expenses Report"}
_REQUESTED_PAGE = st.query_params.get("page")
_THIRD_LINK_REQUEST = _REQUESTED_PAGE in THIRD_LINK_REPORT_PAGES

st.set_page_config(
    page_title="TB & NF Family Office Expenses Platform" if _THIRD_LINK_REQUEST else "Statement Management",
    layout="wide",
)

if _THIRD_LINK_REQUEST:
    require_third_report_login()
else:
    require_login()

# Keep heavy data/reporting imports after the login gate so the first screen appears quickly.
import pandas as pd

from db import (
    ConcurrentTransactionEditError,
    DB_PATH,
    MAX_SAFE_FINANCIAL_AMOUNT,
    USING_POSTGRES,
    add_category,
    apply_account_and_rates,
    backfill_missing_usd_amounts,
    build_statement_hash,
    dataframe_to_excel_bytes,
    exclude_transactions,
    filter_financially_active_transactions,
    full_reset_database,
    get_accounts,
    get_all_transactions,
    get_app_setting,
    get_app_settings,
    get_categories,
    get_cross_statement_duplicate_audit,
    get_dashboard_counts,
    get_exact_duplicate_audit,
    get_import_history,
    get_import_transaction_audit,
    get_memory,
    get_pending_transactions,
    get_report_group_settings,
    get_rates,
    get_saved_transactions,
    get_statement_account,
    get_statement_balances,
    get_subcategories,
    get_transaction_change_log,
    get_transaction_edit_states,
    import_database_updates_from_excel,
    import_memory_from_excel,
    init_db,
    insert_manual_transaction,
    mark_duplicate_transactions,
    replace_accounts_from_excel,
    replace_categories_from_excel,
    replace_report_group_settings,
    replace_rates_from_excel,
    reset_runtime_data,
    restore_transactions,
    record_duplicate_statement_attempt,
    save_pending_transactions,
    save_statement_balance,
    save_reviewed_rows,
    set_app_setting,
    split_transaction,
    statement_balance_exists,
    statement_already_imported,
    update_database_rows,
    update_statement_balance_rows,
)
from utils import format_currency


_DB_CACHE_TTL_SECONDS = 90
_db_get_accounts = get_accounts
_db_get_all_transactions = get_all_transactions
_db_get_app_settings = get_app_settings
_db_get_categories = get_categories
_db_get_cross_statement_duplicate_audit = get_cross_statement_duplicate_audit
_db_get_dashboard_counts = get_dashboard_counts
_db_get_exact_duplicate_audit = get_exact_duplicate_audit
_db_get_import_history = get_import_history
_db_get_import_transaction_audit = get_import_transaction_audit
_db_get_memory = get_memory
_db_get_pending_transactions = get_pending_transactions
_db_get_report_group_settings = get_report_group_settings
_db_get_rates = get_rates
_db_get_saved_transactions = get_saved_transactions
_db_get_statement_balances = get_statement_balances
_db_get_subcategories = get_subcategories
_db_get_transaction_change_log = get_transaction_change_log


_USD_BACKFILL_MIN_INTERVAL_SECONDS = int(os.getenv("ARETI_USD_BACKFILL_INTERVAL_SECONDS", "300"))


@st.cache_resource(show_spinner=False)
def _runtime_perf_state():
    return {"usd_backfill_checked_at": 0.0}


def _perf_log(label, started_at):
    if os.getenv("ARETI_PERF_LOG", "0").strip().casefold() in {"0", "false", "no", "off"}:
        return
    elapsed = time.perf_counter() - started_at
    print(f"[ARETI_PERF] {label}: {elapsed:.3f}s", flush=True)


def _dataframe_signature(frame, columns):
    if frame is None or frame.empty:
        return "0:empty"
    available_columns = [column for column in columns if column in frame.columns]
    if not available_columns:
        return f"{len(frame)}:no-columns"
    compact = frame[available_columns].copy()
    for column in available_columns:
        if pd.api.types.is_datetime64_any_dtype(compact[column]):
            compact[column] = pd.to_datetime(compact[column], errors="coerce").dt.strftime("%Y-%m-%d")
        else:
            compact[column] = compact[column].fillna("").astype(str)
    hashed = pd.util.hash_pandas_object(compact, index=False).values
    return f"{len(frame)}:{hashlib.sha256(hashed.tobytes()).hexdigest()}"


def _last_sunday(year, month):
    day = datetime(year, month + 1, 1, tzinfo=timezone.utc) - timedelta(days=1)
    return day - timedelta(days=(day.weekday() + 1) % 7)


def _cyprus_offset_hours(utc_now):
    dst_start = _last_sunday(utc_now.year, 3).replace(hour=1, minute=0, second=0, microsecond=0)
    dst_end = _last_sunday(utc_now.year, 10).replace(hour=1, minute=0, second=0, microsecond=0)
    return 3 if dst_start <= utc_now < dst_end else 2


def app_now():
    utc_now = datetime.now(timezone.utc)
    try:
        return utc_now.astimezone(ZoneInfo("Asia/Nicosia"))
    except ZoneInfoNotFoundError:
        return utc_now.astimezone(timezone(timedelta(hours=_cyprus_offset_hours(utc_now)), "Asia/Nicosia"))


REPORT_UNTIL_SETTING_KEY = "report_until"


def _parse_report_until(value, fallback_date):
    parsed = pd.to_datetime(str(value or ""), errors="coerce")
    if pd.isna(parsed):
        return fallback_date
    return parsed.date()


def get_configured_report_until(fallback_date=None):
    fallback = fallback_date or app_now().date()
    return _parse_report_until(get_app_setting(REPORT_UNTIL_SETTING_KEY, ""), fallback)


def active_financial_transactions(df):
    return filter_financially_active_transactions(df)


@st.cache_data(show_spinner=False, ttl=_DB_CACHE_TTL_SECONDS)
def get_accounts():
    return _db_get_accounts()


@st.cache_data(show_spinner=False, ttl=_DB_CACHE_TTL_SECONDS)
def get_all_transactions():
    return _db_get_all_transactions()


@st.cache_data(show_spinner=False, ttl=_DB_CACHE_TTL_SECONDS)
def get_app_settings():
    return _db_get_app_settings()


@st.cache_data(show_spinner=False, ttl=_DB_CACHE_TTL_SECONDS)
def get_categories(include_subcategories=False):
    return _db_get_categories(include_subcategories=include_subcategories)


@st.cache_data(show_spinner=False, ttl=_DB_CACHE_TTL_SECONDS)
def get_cross_statement_duplicate_audit():
    return _db_get_cross_statement_duplicate_audit()


@st.cache_data(show_spinner=False, ttl=_DB_CACHE_TTL_SECONDS)
def get_dashboard_counts():
    return _db_get_dashboard_counts()


@st.cache_data(show_spinner=False, ttl=_DB_CACHE_TTL_SECONDS)
def get_exact_duplicate_audit():
    return _db_get_exact_duplicate_audit()


@st.cache_data(show_spinner=False, ttl=_DB_CACHE_TTL_SECONDS)
def get_import_history():
    return _db_get_import_history()


@st.cache_data(show_spinner=False, ttl=_DB_CACHE_TTL_SECONDS)
def get_import_transaction_audit():
    return _db_get_import_transaction_audit()


@st.cache_data(show_spinner=False, ttl=_DB_CACHE_TTL_SECONDS)
def get_memory():
    return _db_get_memory()


@st.cache_data(show_spinner=False, ttl=_DB_CACHE_TTL_SECONDS)
def get_pending_transactions():
    return _db_get_pending_transactions()


@st.cache_data(show_spinner=False, ttl=_DB_CACHE_TTL_SECONDS)
def get_report_group_settings():
    return _db_get_report_group_settings()


@st.cache_data(show_spinner=False, ttl=_DB_CACHE_TTL_SECONDS)
def get_rates():
    return _db_get_rates()


@st.cache_data(show_spinner=False, ttl=_DB_CACHE_TTL_SECONDS)
def get_saved_transactions():
    return _db_get_saved_transactions()


@st.cache_data(show_spinner=False, ttl=_DB_CACHE_TTL_SECONDS)
def get_statement_balances():
    return _db_get_statement_balances()


@st.cache_data(show_spinner=False, ttl=_DB_CACHE_TTL_SECONDS)
def get_subcategories(category=None):
    return _db_get_subcategories(category=category)


@st.cache_data(show_spinner=False, ttl=_DB_CACHE_TTL_SECONDS)
def get_transaction_change_log(limit=300):
    return _db_get_transaction_change_log(limit=limit)


def ensure_usd_backfilled(show_message=False):
    state = _runtime_perf_state()
    now = time.monotonic()
    if (
        not show_message
        and _USD_BACKFILL_MIN_INTERVAL_SECONDS > 0
        and now - float(state.get("usd_backfill_checked_at", 0.0)) < _USD_BACKFILL_MIN_INTERVAL_SECONDS
    ):
        return 0
    started = time.perf_counter()
    try:
        count = backfill_missing_usd_amounts()
    except Exception as exc:
        state["usd_backfill_checked_at"] = now
        _perf_log("usd_backfill_failed", started)
        if show_message:
            st.warning(f"Could not calculate missing USD equivalents automatically: {exc}")
        return 0
    state["usd_backfill_checked_at"] = now
    _perf_log("usd_backfill", started)
    if count:
        st.cache_data.clear()
        if show_message:
            st.info(f"Calculated missing USD equivalents for {count} transaction row(s).")
    return count


st.markdown(
    """
    <style>
    :root {
        --page-bg: #edf2f7;
        --panel: #ffffff;
        --panel-muted: #f8fafc;
        --text-main: #172033;
        --text-muted: #64748b;
        --border: #d6dee8;
        --accent: #0f766e;
        --accent-dark: #0b5f59;
        --navy: #111c3d;
        --gold: #9a6a14;
        --blue: #1d4ed8;
        --danger: #b42318;
        --shadow: 0 8px 22px rgba(15, 23, 42, 0.07);
    }
    .stApp {
        background: var(--page-bg);
        color: var(--text-main);
    }
    #MainMenu, footer, header, .stDeployButton,
    [data-testid="stToolbar"], [data-testid="stDecoration"],
    [data-testid="stStatusWidget"], [data-testid="manage-app-button"] {
        display: none !important;
        visibility: hidden !important;
        height: 0 !important;
    }
    .block-container {
        padding-top: 0.85rem;
        padding-bottom: 2rem;
        max-width: 1520px;
    }
    h1, h2, h3 {
        letter-spacing: 0;
    }
    h2, h3 {
        color: var(--text-main);
    }
    .app-header {
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 16px;
        background: var(--panel);
        border: 1px solid var(--border);
        border-left: 4px solid var(--accent);
        border-radius: 8px;
        padding: 16px 18px;
        box-shadow: var(--shadow);
        margin-bottom: 14px;
    }
    .app-title-wrap {
        display: flex;
        align-items: center;
        gap: 12px;
        min-width: 0;
    }
    .app-brand-mark {
        width: 42px;
        height: 42px;
        border-radius: 8px;
        display: grid;
        place-items: center;
        background: var(--navy);
        color: #ffffff;
        font-size: 12px;
        font-weight: 800;
        letter-spacing: 0;
        flex: 0 0 auto;
    }
    .app-title {
        margin: 0 !important;
        padding: 0 !important;
        font-size: 24px;
        line-height: 1.1;
        font-weight: 780;
        color: var(--text-main);
    }
    .app-header h1 {
        margin: 0 !important;
        padding: 0 !important;
        font-size: 24px !important;
        line-height: 1.1 !important;
    }
    .app-subtitle {
        margin-top: 3px;
        color: var(--text-muted);
        font-size: 13px;
    }
    .updated-pill {
        display: inline-flex;
        align-items: center;
        background: #f0fdfa;
        color: var(--accent-dark);
        border: 1px solid #99f6e4;
        border-radius: 999px;
        padding: 8px 12px;
        font-size: 13px;
        font-weight: 700;
        white-space: nowrap;
    }
    .metric-grid {
        display: grid;
        grid-template-columns: repeat(7, minmax(120px, 1fr));
        gap: 10px;
        margin: 4px 0 14px;
    }
    .metric-card {
        border: 1px solid var(--border);
        border-radius: 8px;
        padding: 12px 13px;
        background: var(--panel);
        box-shadow: 0 2px 8px rgba(16, 32, 51, 0.05);
        min-height: 86px;
        border-top: 3px solid #e2e8f0;
    }
    .metric-row {
        display: flex;
        align-items: center;
    }
    .metric-label {
        color: var(--text-muted);
        font-size: 12px;
        font-weight: 700;
        text-transform: uppercase;
    }
    .metric-value {
        margin-top: 10px;
        color: var(--text-main);
        font-size: 27px;
        font-weight: 800;
        line-height: 1;
    }
    .summary-strip {
        display: grid;
        grid-template-columns: repeat(4, minmax(0, 1fr));
        gap: 10px;
        margin: 10px 0 16px;
    }
    .summary-item {
        background: var(--panel);
        border: 1px solid var(--border);
        border-radius: 8px;
        padding: 12px 14px;
    }
    .summary-label {
        color: var(--text-muted);
        font-size: 12px;
        font-weight: 700;
        text-transform: uppercase;
    }
    .summary-value {
        margin-top: 6px;
        color: var(--text-main);
        font-size: 20px;
        font-weight: 800;
    }
    .executive-note {
        color: var(--text-muted);
        font-size: 13px;
        margin: -4px 0 14px;
    }
    .executive-table {
        width: 100%;
        min-width: 980px;
        border-collapse: collapse;
        background: var(--panel);
        border: 1px solid var(--border);
        border-radius: 8px;
        overflow: hidden;
        box-shadow: 0 2px 8px rgba(16, 32, 51, 0.05);
    }
    .executive-scroll, .drill-scroll {
        width: 100%;
        overflow-x: auto;
        padding-bottom: 4px;
    }
    .executive-table th,
    .executive-table td {
        border-bottom: 1px solid var(--border);
        padding: 10px 12px;
        text-align: right;
        vertical-align: top;
        font-size: 13px;
    }
    .executive-table th:first-child,
    .executive-table td:first-child {
        text-align: left;
    }
    .executive-table th {
        background: #e8eef6;
        color: var(--text-main);
        font-weight: 800;
        text-transform: uppercase;
        font-size: 11px;
    }
    .executive-table tr:last-child td {
        border-bottom: 0;
    }
    .trend-up {
        background: #fff1f2;
        color: #b42318;
        font-weight: 800;
        border-left: 3px solid #dc2626;
    }
    .trend-down {
        background: #ecfdf3;
        color: #067647;
        font-weight: 800;
        border-left: 3px solid #16a34a;
    }
    .trend-flat {
        background: #f8fafc;
        color: var(--text-muted);
        font-weight: 800;
        border-left: 3px solid #94a3b8;
    }
    .drill-header {
        display: grid;
        grid-template-columns: minmax(220px, 2.4fr) repeat(5, minmax(92px, 1fr));
        gap: 4px;
        align-items: stretch;
        margin-top: 8px;
        color: var(--text-muted);
        font-size: 11px;
        font-weight: 800;
        text-transform: uppercase;
    }
    .executive-section-title {
        color: var(--text-main);
        font-size: 12px;
        font-weight: 800;
        line-height: 1.6;
        margin: 12px 0 6px;
    }
    .drill-row {
        display: grid;
        grid-template-columns: minmax(220px, 2.4fr) repeat(5, minmax(92px, 1fr));
        gap: 4px;
        align-items: stretch;
        margin-top: 4px;
    }
    .drill-cell {
        background: var(--panel);
        border: 1px solid var(--border);
        border-radius: 2px;
        min-height: 20px;
        padding: 1px 4px;
        display: flex;
        align-items: center;
        justify-content: flex-end;
        text-align: right;
        font-size: 10.5px;
        font-weight: 700;
    }
    .drill-cell:first-child {
        justify-content: flex-start;
        text-align: left;
        color: var(--text-main);
    }
    .drill-cell.trend-up {
        background: #fff1f2;
        color: #b42318;
        border-left: 3px solid #dc2626;
    }
    .drill-cell.trend-down {
        background: #ecfdf3;
        color: #067647;
        border-left: 3px solid #16a34a;
    }
    .drill-cell.trend-flat {
        background: #f8fafc;
        color: var(--text-muted);
        border-left: 3px solid #94a3b8;
    }
    .drill-breadcrumb {
        color: var(--text-muted);
        font-size: 13px;
        margin: 8px 0 4px;
    }
    .drill-inline-context {
        border-left: 3px solid var(--accent);
        color: var(--text-muted);
        font-size: 12px;
        font-weight: 650;
        margin: 2px 0 4px 10px;
        padding: 3px 8px;
    }
    .drill-total-cell {
        border-top: 2px solid var(--accent);
        font-weight: 800;
    }
    .drill-total-label {
        color: var(--navy);
    }
    .third-report-header {
        margin: 6px 0 18px;
    }
    .third-report-title {
        color: var(--text-main);
        font-size: 30px;
        font-weight: 820;
        line-height: 1.15;
        margin: 0;
    }
    .third-report-subtitle {
        color: var(--text-main);
        font-size: 14px;
        font-weight: 650;
        margin-top: 5px;
    }
    .third-report-note {
        color: var(--text-muted);
        font-size: 12px;
        margin: 2px 0 10px;
    }
    .ai-analysis-box {
        background: var(--panel);
        border: 1px solid var(--border);
        border-radius: 8px;
        padding: 12px 14px;
        margin: 8px 0 12px;
        color: var(--text-main);
        font-family: inherit;
        font-size: 13px;
        line-height: 1.55;
        font-style: normal;
    }
    .soft-panel {
        background: var(--panel);
        border: 1px solid var(--border);
        border-radius: 8px;
        padding: 14px;
        margin-bottom: 12px;
    }
    div[data-testid="stRadio"] [role="radiogroup"] {
        gap: 8px;
        background: var(--panel);
        border: 1px solid var(--border);
        border-radius: 8px;
        padding: 7px;
        box-shadow: 0 2px 8px rgba(16, 32, 51, 0.05);
    }
    div[data-testid="stRadio"] label {
        border-radius: 6px;
        padding: 7px 11px;
        color: var(--text-main);
    }
    div[data-testid="stSegmentedControl"] {
        background: var(--panel);
        border: 1px solid var(--border);
        border-radius: 8px;
        padding: 6px;
        box-shadow: 0 2px 8px rgba(16, 32, 51, 0.05);
    }
    .stButton button, .stDownloadButton button {
        border-radius: 6px;
        font-weight: 600;
        border-color: var(--border);
    }
    .stButton button[kind="primary"], .stDownloadButton button[kind="primary"] {
        background: var(--accent);
        border-color: var(--accent);
    }
    .stButton button[kind="primary"]:hover, .stDownloadButton button[kind="primary"]:hover {
        background: var(--accent-dark);
        border-color: var(--accent-dark);
    }
    div[data-testid="stAlert"] {
        border-radius: 6px;
    }
    div[data-testid="stDataFrame"], div[data-testid="stDataEditor"] {
        border: 1px solid var(--border);
        border-radius: 6px;
        overflow: hidden;
    }
    div[data-testid="stDataEditor"] [role="gridcell"],
    div[data-testid="stDataFrame"] [role="gridcell"] {
        white-space: normal !important;
        line-height: 1.35 !important;
    }
    div[data-testid="stHorizontalBlock"]:has(.drill-cell) {
        gap: 2px !important;
        margin: 0 !important;
    }
    div[data-testid="stHorizontalBlock"]:has(.drill-cell) div[data-testid="column"] {
        padding-left: 1px !important;
        padding-right: 1px !important;
    }
    div[data-testid="stHorizontalBlock"]:has(.drill-cell) div[data-testid="stMarkdown"],
    div[data-testid="stHorizontalBlock"]:has(.drill-cell) div[data-testid="stButton"] {
        margin: 0 !important;
    }
    div[data-testid="stHorizontalBlock"]:has(.drill-cell) div[data-testid="stElementContainer"] {
        margin: 0 !important;
        padding: 0 !important;
    }
    div[data-testid="stVerticalBlock"] > div:has(div[data-testid="stHorizontalBlock"] .drill-cell) {
        margin: 0 !important;
        padding: 0 !important;
    }
    div[data-testid="stHorizontalBlock"]:has(.drill-cell) button {
        min-height: 22px !important;
        padding: 1px 5px !important;
        border-radius: 2px !important;
        font-size: 10.5px !important;
        line-height: 1.1 !important;
    }
    .section-divider {
        height: 1px;
        background: var(--border);
        margin: 0.75rem 0 1rem;
    }
    .import-progress {
        display: flex;
        align-items: center;
        gap: 12px;
        background: #fff1f2;
        border: 1px solid #fecdd3;
        border-left: 4px solid #dc2626;
        color: #991b1b;
        border-radius: 8px;
        padding: 14px 16px;
        margin: 10px 0;
        font-weight: 800;
    }
    .import-runner {
        display: inline-grid;
        place-items: center;
        width: 44px;
        height: 44px;
        border-radius: 999px;
        background: #dc2626;
        color: #ffffff;
        font-size: 26px;
        line-height: 1;
        animation: runnerPulse 0.8s ease-in-out infinite alternate;
        box-shadow: 0 6px 14px rgba(220, 38, 38, 0.28);
    }
    @keyframes runnerPulse {
        from { transform: translateX(0); opacity: 0.72; }
        to { transform: translateX(8px); opacity: 1; }
    }
    [data-testid="stFileUploader"] {
        background: var(--panel);
        border: 1px solid var(--border);
        border-radius: 8px;
        padding: 10px;
    }
    .login-shell {
        min-height: calc(100vh - 5rem);
        display: grid;
        place-items: center;
        padding-top: 1.5rem;
    }
    .login-card {
        width: min(360px, 100%);
        background: var(--panel);
        border: 1px solid var(--border);
        border-radius: 8px;
        box-shadow: 0 8px 20px rgba(15, 23, 42, 0.08);
        padding: 20px 22px 18px;
    }
    .login-brand {
        display: grid;
        justify-items: center;
        gap: 8px;
        margin-bottom: 14px;
        text-align: center;
    }
    .login-brand .app-brand-mark {
        width: 38px;
        height: 38px;
    }
    .login-title {
        margin: 0;
        color: var(--text-main);
        font-size: 18px;
        line-height: 1.15;
        font-weight: 780;
    }
    .login-subtitle {
        margin-top: 4px;
        color: var(--text-muted);
        font-size: 12px;
    }
    .login-note {
        margin-top: 10px;
        color: var(--text-muted);
        font-size: 12px;
        line-height: 1.45;
        text-align: center;
    }
    .session-line {
        color: var(--text-muted);
        font-size: 12px;
        margin: -4px 0 10px;
    }
    @media (max-width: 1120px) {
        .metric-grid {
            grid-template-columns: repeat(4, minmax(120px, 1fr));
        }
        .summary-strip {
            grid-template-columns: repeat(2, minmax(0, 1fr));
        }
    }
    @media (max-width: 760px) {
        .app-header {
            align-items: flex-start;
            flex-direction: column;
        }
        .metric-grid,
        .summary-strip {
            grid-template-columns: repeat(2, minmax(0, 1fr));
        }
        .app-title {
            font-size: 21px !important;
        }
        .app-header h1 {
            font-size: 21px !important;
        }
    }
    </style>
    """,
    unsafe_allow_html=True,
)

def render_session_line():
    left, right = st.columns([8, 1])
    left.markdown(
        f"<div class=\"session-line\">Signed in as {get_login_user()}</div>",
        unsafe_allow_html=True,
    )
    if right.button("Sign out"):
        sign_out()
        st.rerun()


def render_third_report_session_line():
    left, right = st.columns([8, 1])
    user = get_third_report_user() or "report user"
    left.markdown(
        f"<div class=\"session-line\">Signed in as {escape(str(user))}</div>",
        unsafe_allow_html=True,
    )
    if right.button("Sign out", key="third_report_sign_out"):
        sign_out_third_report()
        st.rerun()


@st.cache_resource(show_spinner=False)
def ensure_database_ready():
    init_db()
    return True

try:
    ensure_database_ready()
except Exception as exc:
    st.error("Database connection is not ready. Please refresh in a moment.")
    st.caption(str(exc))
    st.stop()

SHARED_DIR = Path(os.getenv("ARETI_SHARED_FOLDER", r"C:\Users\Student\Dropbox\ARETI FILES ONE DRIVE"))
SHARED_SETUP_CANDIDATES = {
    "categories": ["Expenses categories.xlsx"],
    "accounts": ["Who made the expense (1).xlsx", "Who made the expense.xlsx"],
    "rates": ["Rates.xlsx"],
}


def latest_shared_setup_file(file_names):
    candidates = []
    if SHARED_DIR.exists():
        for file_name in file_names:
            direct = SHARED_DIR / file_name
            if direct.exists():
                candidates.append(direct)
            candidates.extend(path for path in SHARED_DIR.glob(f"*/{file_name}") if path.is_file())
    if not candidates:
        return SHARED_DIR / file_names[0]
    return max(candidates, key=lambda path: path.stat().st_mtime)


SHARED_SETUP_FILES = {
    label: latest_shared_setup_file(file_names)
    for label, file_names in SHARED_SETUP_CANDIDATES.items()
}


@st.cache_data(show_spinner=False)
def parse_statement(file_bytes, file_name):
    from parsing import parse_csv, parse_excel, parse_pdf

    handle = BytesIO(file_bytes)
    lower_name = file_name.lower()
    if lower_name.endswith(".pdf"):
        return parse_pdf(handle)
    if lower_name.endswith(".xlsx") or lower_name.endswith(".xls"):
        return parse_excel(handle)
    return parse_csv(handle)


@st.cache_data(show_spinner=False)
def parse_statement_balance(file_bytes, file_name):
    from parsing import extract_statement_balance

    handle = BytesIO(file_bytes)
    try:
        return extract_statement_balance(handle, file_name)
    except Exception:
        return {}


def classify_statement_rows(parsed, memory):
    from classification import classify_transactions

    return classify_transactions(parsed, memory)


def display_money(value, currency=""):
    if value is None or pd.isna(value):
        return "-"
    prefix = f"{currency} " if currency else ""
    try:
        return f"{prefix}{float(value):,.2f}"
    except Exception:
        return "-"


def balance_has_values(balance):
    if not balance:
        return False
    keys = [
        "period_start",
        "period_end",
        "opening_balance",
        "money_out",
        "money_in",
        "closing_balance",
        "bank",
        "account_number",
    ]
    return any(balance.get(key) not in ("", None) for key in keys)


def render_unsafe_storage_notice():
    if USING_POSTGRES:
        return
    render_env = any(
        os.getenv(name)
        for name in ["RENDER", "RENDER_SERVICE_NAME", "RENDER_EXTERNAL_URL", "RENDER_INSTANCE_ID"]
    )
    normalized_db_path = str(DB_PATH).replace("\\", "/")
    if render_env and not normalized_db_path.startswith("/var/data/"):
        st.warning(
            "Storage is temporary on this deployment. Data can reset after a server restart or redeploy. "
            "Enable persistent storage before using live data."
        )


def render_summary_strip(items):
    cards = []
    for label, value in items:
        cards.append(
            f"<div class=\"summary-item\">"
            f"<div class=\"metric-row\">"
            f"<div class=\"summary-label\">{label}</div>"
            f"</div>"
            f"<div class=\"summary-value\">{value}</div>"
            f"</div>"
        )
    st.markdown(f"<div class=\"summary-strip\">{''.join(cards)}</div>", unsafe_allow_html=True)


def category_report_group_map(categories_df):
    if categories_df.empty or "category" not in categories_df.columns:
        return {}
    mapping = {}
    for _, row in categories_df.iterrows():
        category = str(row.get("category", "") or "").strip()
        if not category:
            continue
        group = str(row.get("report_group", "") or "").strip() if "report_group" in categories_df.columns else ""
        mapping.setdefault(category.casefold(), group)
    return mapping


def _report_group_subcategory_key(value):
    text = str(value or "").strip()
    if text.casefold() in {"no subcategory", "no sub", "none", "nan"}:
        return ""
    return text.casefold()


def category_pair_report_group_maps(categories_df):
    exact_map = {}
    category_map = {}
    if categories_df.empty or "category" not in categories_df.columns:
        return exact_map, category_map
    for _, row in categories_df.iterrows():
        category = str(row.get("category", "") or "").strip()
        if not category:
            continue
        subcategory = str(row.get("subcategory", "") or "").strip()
        group = str(row.get("report_group", "") or "").strip() if "report_group" in categories_df.columns else ""
        category_key = category.casefold()
        pair_key = (category_key, _report_group_subcategory_key(subcategory))
        if pair_key not in exact_map or (not exact_map[pair_key] and group):
            exact_map[pair_key] = group
        if category_key not in category_map or (not category_map[category_key] and group):
            category_map[category_key] = group
    return exact_map, category_map


def add_report_group_column(df, categories_df):
    out = df.copy()
    exact_group_map, category_group_map = category_pair_report_group_maps(categories_df)
    categories = out.get("category", pd.Series("", index=out.index)).fillna("").astype(str)
    subcategories = out.get("subcategory", pd.Series("", index=out.index)).fillna("").astype(str)
    groups = []
    for category, subcategory in zip(categories, subcategories):
        category_key = category.strip().casefold()
        pair_key = (category_key, _report_group_subcategory_key(subcategory))
        # Some Expenses spreadsheets leave the reporting-group cell blank on
        # subcategory rows. In that case use the category's non-empty group.
        groups.append(exact_group_map.get(pair_key) or category_group_map.get(category_key, ""))
    out["report_group"] = groups
    return out


def _setup_category_pair_reference(categories_df):
    category_keys = set()
    pair_keys = set()
    if categories_df is None or categories_df.empty or "category" not in categories_df.columns:
        return category_keys, pair_keys
    for _, row in categories_df.iterrows():
        category = str(row.get("category", "") or "").strip()
        if not category:
            continue
        category_key = category.casefold()
        category_keys.add(category_key)
        subcategory = str(row.get("subcategory", "") or "").strip()
        pair_keys.add((category_key, _report_group_subcategory_key(subcategory)))
    return category_keys, pair_keys


def report_group_consistency_audit(transactions_df, categories_df):
    columns = [
        "category",
        "subcategory",
        "transaction_rows",
        "setup_report_group",
        "status",
        "sample_ids",
    ]
    if transactions_df is None or transactions_df.empty:
        return pd.DataFrame(columns=columns)
    exact_group_map, category_group_map = category_pair_report_group_maps(categories_df)
    frame = transactions_df.copy()
    frame["_category"] = frame.get("category", pd.Series("", index=frame.index)).fillna("").astype(str).str.strip()
    frame["_subcategory"] = frame.get("subcategory", pd.Series("", index=frame.index)).fillna("").astype(str).str.strip()
    rows = []
    grouped = frame.groupby(["_category", "_subcategory"], dropna=False, sort=True)
    for (category, subcategory), group in grouped:
        category_text = str(category or "").strip()
        subcategory_text = str(subcategory or "").strip()
        category_key = category_text.casefold()
        subcategory_key = _report_group_subcategory_key(subcategory_text)
        pair_key = (category_key, subcategory_key)
        sample_ids = ""
        if "id" in group.columns:
            sample_ids = ", ".join(group["id"].dropna().astype(int).astype(str).head(8).tolist())

        if not category_text:
            setup_group = ""
            status = "Missing category on transaction"
        elif pair_key in exact_group_map:
            setup_group = exact_group_map[pair_key] or category_group_map.get(category_key, "")
            if exact_group_map[pair_key]:
                status = "OK"
            elif setup_group:
                status = "Category/subcategory exists in Setup with blank reporting group; using category fallback"
            else:
                status = "Category/subcategory exists in Setup but reporting group is blank"
        elif category_key in category_group_map:
            setup_group = category_group_map.get(category_key, "")
            status = (
                "Category/subcategory pair is not in Setup; using category fallback"
                if setup_group
                else "Category exists in Setup but reporting group is blank"
            )
        else:
            setup_group = ""
            status = "Category is not in Setup"

        if status != "OK":
            rows.append(
                {
                    "category": category_text,
                    "subcategory": subcategory_text or _NO_SUBCATEGORY_LABEL,
                    "transaction_rows": len(group),
                    "setup_report_group": setup_group,
                    "status": status,
                    "sample_ids": sample_ids,
                }
            )
    return pd.DataFrame(rows, columns=columns)


def _category_pair_status(category, subcategory, category_keys, pair_keys):
    category_text = str(category or "").strip()
    subcategory_text = str(subcategory or "").strip()
    if not category_text:
        return "Missing category"
    category_key = category_text.casefold()
    if category_key not in category_keys:
        return "Category not in Setup"
    subcategory_key = _report_group_subcategory_key(subcategory_text)
    if not subcategory_key:
        return "No subcategory"
    if (category_key, subcategory_key) not in pair_keys:
        return "Subcategory not linked to category"
    return "OK"


def _executive_row_count_verification_frame(active_database_rows, categories_df, visible_report_groups):
    columns = [
        "Reporting group",
        "Category",
        "Subcategory",
        "Rows",
        "In current Executive view",
        "Setup check",
    ]
    if active_database_rows is None or active_database_rows.empty:
        return pd.DataFrame(columns=columns)

    audit = add_report_group_column(active_database_rows, categories_df)
    category_keys, pair_keys = _setup_category_pair_reference(categories_df)
    visible_set = {str(group or "").strip() for group in (visible_report_groups or []) if str(group or "").strip()}

    audit["_report_group"] = (
        audit.get("report_group", pd.Series("", index=audit.index))
        .fillna("")
        .astype(str)
        .str.strip()
        .replace("", "Unassigned reporting group")
    )
    audit["_category"] = (
        audit.get("category", pd.Series("", index=audit.index))
        .fillna("")
        .astype(str)
        .str.strip()
        .replace("", "Unassigned category")
    )
    audit["_subcategory"] = (
        audit.get("subcategory", pd.Series("", index=audit.index))
        .fillna("")
        .astype(str)
        .str.strip()
        .replace("", "No subcategory")
    )
    audit["_setup_check"] = audit.apply(
        lambda row: _category_pair_status(
            row.get("category", ""),
            row.get("subcategory", ""),
            category_keys,
            pair_keys,
        ),
        axis=1,
    )
    audit["_in_current_view"] = (
        audit["_report_group"].isin(visible_set) if visible_set else True
    )

    counts = (
        audit.groupby(
            ["_report_group", "_category", "_subcategory", "_in_current_view", "_setup_check"],
            dropna=False,
        )
        .size()
        .reset_index(name="Rows")
        .rename(columns={
            "_report_group": "Reporting group",
            "_category": "Category",
            "_subcategory": "Subcategory",
            "_in_current_view": "In current Executive view",
            "_setup_check": "Setup check",
        })
        .sort_values(["Reporting group", "Category", "Subcategory"], kind="stable")
        .reset_index(drop=True)
    )
    return counts[columns]


def transaction_filter_controls(df, key_prefix, include_category=False):
    filtered = df.copy()
    if filtered.empty:
        return filtered

    dates = pd.to_datetime(filtered.get("txn_date"), errors="coerce")
    month_values = sorted(dates.dt.to_period("M").dropna().astype(str).unique(), reverse=True)
    account_values = sorted(
        value for value in filtered.get("account_name", pd.Series(dtype=str)).fillna("").astype(str).unique()
        if value
    )
    category_values = sorted(
        value for value in filtered.get("category", pd.Series(dtype=str)).fillna("").astype(str).str.strip().unique()
        if value
    ) if include_category and "category" in filtered.columns else []

    columns = st.columns(3 if include_category else 2)
    f1, f2 = columns[:2]
    selected_month = f1.selectbox(
        "Month",
        ["All months"] + month_values,
        key=f"{key_prefix}_month_filter",
    )
    selected_account = f2.selectbox(
        "Account",
        ["All accounts"] + account_values,
        key=f"{key_prefix}_account_filter",
    )

    if selected_month != "All months":
        filtered = filtered[dates.dt.to_period("M").astype(str) == selected_month].copy()
    if selected_account != "All accounts":
        filtered = filtered[filtered["account_name"].fillna("").astype(str) == selected_account].copy()
    if include_category:
        selected_category = columns[2].selectbox(
            "Category",
            ["All categories"] + category_values,
            key=f"{key_prefix}_category_filter",
        )
        if selected_category != "All categories":
            filtered = filtered[
                filtered["category"].fillna("").astype(str).str.strip() == selected_category
            ].copy()
    return filtered


def _search_text_mask(df, search):
    if df.empty:
        return pd.Series(False, index=df.index)
    return df.astype(str).apply(
        lambda col: col.str.contains(search, case=False, na=False)
    ).any(axis=1)


def _parse_amount_search(search):
    text = str(search or "").strip()
    if not text:
        return None
    cleaned = (
        text.replace("$", "")
        .replace("€", "")
        .replace("£", "")
        .replace(",", "")
        .strip()
    )
    if not re.fullmatch(r"[-+]?\d+(?:\.\d+)?", cleaned):
        return None
    try:
        return round(abs(float(cleaned)), 2)
    except Exception:
        return None


def database_search_mask(df, search):
    mask = _search_text_mask(df, search)
    amount_search = _parse_amount_search(search)
    if amount_search is None or df.empty:
        return mask
    for column in ["amount", "amount_usd"]:
        if column not in df.columns:
            continue
        numeric = pd.to_numeric(df[column], errors="coerce")
        mask = mask | numeric.abs().round(2).eq(amount_search)
    return mask


def flag_duplicates(df):
    return mark_duplicate_transactions(df)


def account_options(accounts):
    labels = []
    lookup = {}
    for _, row in accounts.iterrows():
        label = " | ".join([
            str(row.get("account_name", "")),
            str(row.get("bank", "")),
            str(row.get("account_number", "")),
            str(row.get("currency", "")),
        ])
        labels.append(label)
        lookup[label] = row.to_dict()
    return labels, lookup


def statement_currency_hint(text):
    head = str(text or "").upper()[:12000]
    if "EUR STATEMENT" in head or "€" in head:
        return "EUR"
    if "USD STATEMENT" in head or "$" in head:
        return "USD"
    if "GBP STATEMENT" in head or "£" in head:
        return "GBP"
    eur_pos = head.find(" EUR ")
    usd_pos = head.find(" USD ")
    gbp_pos = head.find(" GBP ")
    positions = [(pos, code) for pos, code in [(eur_pos, "EUR"), (usd_pos, "USD"), (gbp_pos, "GBP")] if pos >= 0]
    if positions:
        positions.sort()
        return positions[0][1]
    return ""


def guess_account_index(file_bytes, file_name, accounts, labels, balance_info=None):
    if accounts.empty or not labels:
        return 0
    balance_info = balance_info or {}
    sample = file_bytes[:250000].decode("latin-1", errors="ignore")
    if file_name.lower().endswith(".pdf"):
        try:
            import pdfplumber

            with pdfplumber.open(BytesIO(file_bytes)) as pdf:
                sample = "\n".join(page.extract_text() or "" for page in pdf.pages[:2]) + "\n" + sample
        except Exception:
            pass
    searchable = f"{file_name} {sample}".upper()
    currency_hint = statement_currency_hint(searchable)
    balance_currency_hint = str(balance_info.get("currency") or "").strip().upper()
    if balance_currency_hint:
        currency_hint = balance_currency_hint
    balance_bank_hint = str(balance_info.get("bank") or "").strip().upper()
    balance_account_digits = re.sub(r"\D", "", str(balance_info.get("account_number", "")))
    digits = re.sub(r"\D", "", searchable)

    if "CARD MEMBER" in searchable and ("AMEX" in searchable or "AMERICAN EXPRESS" in searchable):
        first_amex_index = None
        for idx, (_, row) in enumerate(accounts.iterrows()):
            bank = str(row.get("bank", "")).upper()
            account_number_text = str(row.get("account_number", ""))
            if "AMEX" not in bank and "AMERICAN EXPRESS" not in bank:
                continue
            if first_amex_index is None:
                first_amex_index = idx
            if "append" in account_number_text.casefold():
                return idx
        if first_amex_index is not None:
            return first_amex_index

    best_index = 0
    best_score = -1
    for idx, (_, row) in enumerate(accounts.iterrows()):
        score = 0
        account_number = re.sub(r"\D", "", str(row.get("account_number", "")))
        if balance_account_digits and account_number:
            if balance_account_digits in account_number or account_number in balance_account_digits:
                score += 18
            elif len(balance_account_digits) >= 4 and len(account_number) >= 4 and balance_account_digits[-4:] == account_number[-4:]:
                score += 8
        if account_number and account_number in digits:
            score += 10
        elif account_number and len(account_number) >= 4 and account_number[-4:] in digits:
            score += 4
        row_bank = str(row.get("bank", "")).strip().upper()
        if balance_bank_hint and row_bank:
            score += 6 if balance_bank_hint in row_bank or row_bank in balance_bank_hint else -2
        for field in ["account_name", "bank", "currency"]:
            value = str(row.get(field, "")).strip().upper()
            if value and value in searchable:
                score += 2
        row_currency = str(row.get("currency", "")).strip().upper()
        if currency_hint and row_currency:
            score += 7 if row_currency == currency_hint else -4
        if score > best_score:
            best_index = idx
            best_score = score
    return best_index


def is_amex_cardholder_statement(file_bytes, file_name):
    sample = file_bytes[:250000].decode("latin-1", errors="ignore").upper()
    return "CARD MEMBER" in sample and ("AMEX" in sample or "AMERICAN EXPRESS" in sample or file_name.lower().endswith(".csv"))


def editable_pending_table(df, categories, subcategories, key, defer_changes=False):
    table = df.copy()
    table["reviewed"] = False
    valid_categories = set(categories)

    def selected_category(row):
        category = row.get("category", "")
        suggested = row.get("suggested_category", "")
        if category in valid_categories:
            return category
        if suggested in valid_categories:
            return suggested
        return ""

    table["category"] = table.apply(selected_category, axis=1)
    table["subcategory"] = table["subcategory"].fillna(table["suggested_subcategory"]).fillna("")
    table = _with_category_pair_column(table)
    categories_df = get_categories(include_subcategories=True)
    pair_options = _category_pair_options(categories_df, categories, table)

    visible_cols = [
        "txn_date",
        "currency",
        "amount",
        "original_description",
        _CATEGORY_PAIR_COLUMN,
        "reviewed",
        "id",
        "account_name",
        "bank",
        "account_number",
        "statement_name",
        "amount_usd",
        "match_type",
        "confidence",
    ]
    table = table[[col for col in visible_cols if col in table.columns]]
    editor_key = _scoped_editor_key(key, table)
    st.session_state[f"{key}__active_editor_key"] = editor_key
    if not defer_changes:
        table = _apply_data_editor_state(table, editor_key)
        table = _refresh_category_pair_derived_columns(table, categories_df)

    editor_kwargs = {
        "key": editor_key,
        "use_container_width": True,
        "hide_index": True,
        "height": min(680, 105 + max(len(table), 4) * 36),
    }
    if not defer_changes:
        editor_kwargs.update({
            "on_change": _capture_data_editor_state,
            "args": (editor_key,),
        })

    return st.data_editor(
        table,
        **editor_kwargs,
        column_config={
            "id": st.column_config.NumberColumn("ID", disabled=True, width="small"),
            "reviewed": st.column_config.CheckboxColumn("Reviewed", help="Tick only rows that are ready to save."),
            "txn_date": st.column_config.TextColumn("Date", disabled=True),
            "account_name": st.column_config.TextColumn("Account", disabled=True),
            "bank": st.column_config.TextColumn("Bank", disabled=True),
            "account_number": st.column_config.TextColumn("Account number", disabled=True),
            "statement_name": st.column_config.TextColumn("Statement", disabled=True, width="large"),
            "currency": st.column_config.TextColumn("Currency", disabled=True, width="small"),
            "amount": st.column_config.NumberColumn(
                "Amount",
                format="%.2f",
                step=0.01,
                help=(
                    "Correct the operational statement-currency amount or sign before review. "
                    "The USD amount is recalculated when the reviewed row is saved. "
                    "Amounts on split-linked rows cannot be changed here."
                ),
            ),
            "amount_usd": st.column_config.NumberColumn("USD amount", format="%.2f", disabled=True),
            "original_description": st.column_config.TextColumn("Full statement description", disabled=True, width="large"),
            "report_group": st.column_config.TextColumn("Reporting group", disabled=True),
            "match_type": st.column_config.TextColumn("Match", disabled=True, width="small"),
            "confidence": st.column_config.NumberColumn("Confidence", format="%.2f", disabled=True, width="small"),
            "category": st.column_config.SelectboxColumn(
                "Category",
                options=[""] + categories,
                required=False,
            ),
            _CATEGORY_PAIR_COLUMN: st.column_config.SelectboxColumn(
                "Category / Subcategory",
                options=pair_options,
                required=False,
                help=(
                    "Choose a valid category/subcategory pair from Setup. "
                    "Only valid combinations are available."
                ),
            ),
        },
    )


def _prepare_pending_review_save_rows(original_df, edited_df, categories_df):
    if original_df.empty or edited_df.empty or "id" not in edited_df.columns:
        return edited_df.iloc[0:0].copy()

    edited = _refresh_category_pair_derived_columns(edited_df, categories_df)
    original = original_df.copy()
    original["id"] = pd.to_numeric(original["id"], errors="coerce")
    edited["id"] = pd.to_numeric(edited["id"], errors="coerce")
    original = original.dropna(subset=["id"]).copy()
    edited = edited.dropna(subset=["id"]).copy()
    original["id"] = original["id"].astype(int)
    edited["id"] = edited["id"].astype(int)
    original_by_id = original.drop_duplicates("id").set_index("id")
    valid_categories = set(
        categories_df.get("category", pd.Series(dtype=str)).dropna().astype(str).str.strip()
    )

    rows = []
    for _, row in edited.iterrows():
        row_id = int(row["id"])
        if row_id not in original_by_id.index:
            continue
        before = original_by_id.loc[row_id]
        raw_amount = row.get("amount")
        try:
            amount = float(raw_amount)
        except (TypeError, ValueError):
            raise ValueError(f"Transaction {row_id} has an invalid Amount.") from None
        if not math.isfinite(amount) or abs(amount) > MAX_SAFE_FINANCIAL_AMOUNT:
            raise ValueError(f"Transaction {row_id} has an invalid Amount.")
        amount = round(amount, 2)
        before_amount = float(before.get("amount"))
        amount_changed = not math.isclose(
            amount,
            before_amount,
            rel_tol=0.0,
            abs_tol=0.000001,
        )
        reviewed = bool(row.get("reviewed", False))
        if not reviewed and not amount_changed:
            continue

        before_category = str(before.get("category", "") or "").strip()
        before_subcategory = str(before.get("subcategory", "") or "").strip()
        editor_default_category = before_category
        if editor_default_category not in valid_categories:
            suggested_category = str(before.get("suggested_category", "") or "").strip()
            editor_default_category = suggested_category if suggested_category in valid_categories else ""
        editor_default_subcategory = before_subcategory or str(
            before.get("suggested_subcategory", "") or ""
        ).strip()
        category = str(row.get("category", "") or "").strip()
        subcategory = str(row.get("subcategory", "") or "").strip()
        if (
            not reviewed
            and category == editor_default_category
            and subcategory == editor_default_subcategory
        ):
            category = before_category
            subcategory = before_subcategory
        status = "reviewed" if reviewed else str(before.get("status", "pending") or "pending").strip()
        rows.append({
            "id": row_id,
            "category": category,
            "subcategory": subcategory,
            "reviewed": reviewed,
            "status": status,
            "report_group": str(row.get("report_group", "") or "").strip(),
            "amount": amount,
            "_expected_category": str(before.get("category", "") or "").strip(),
            "_expected_subcategory": str(before.get("subcategory", "") or "").strip(),
            "_expected_reviewed": bool(before.get("reviewed", False)),
            "_expected_amount": before.get("amount"),
            "_expected_amount_usd": before.get("amount_usd"),
            "_expected_fx_rate": before.get("fx_rate"),
        })

    return pd.DataFrame(rows)


def _changed_transaction_editor_rows(original_df, edited_df, categories_df, compare_columns):
    """Return only rows whose editable values differ from their ID-matched baseline."""
    if original_df.empty or edited_df.empty or "id" not in edited_df.columns:
        return edited_df.iloc[0:0].copy()

    original = _refresh_category_pair_derived_columns(original_df, categories_df)
    edited = _refresh_category_pair_derived_columns(edited_df, categories_df)
    original["id"] = pd.to_numeric(original["id"], errors="coerce")
    edited["id"] = pd.to_numeric(edited["id"], errors="coerce")
    original = original.dropna(subset=["id"]).copy()
    edited = edited.dropna(subset=["id"]).copy()
    original["id"] = original["id"].astype(int)
    edited["id"] = edited["id"].astype(int)
    original_by_id = original.drop_duplicates("id").set_index("id")

    def comparable_value(value, column):
        if column == "reviewed":
            return bool(value) if not pd.isna(value) else False
        text = "" if value is None or pd.isna(value) else str(value).strip()
        return text.casefold() if column == "status" else text

    changed_positions = []
    for position, (_, row) in enumerate(edited.iterrows()):
        row_id = int(row["id"])
        if row_id not in original_by_id.index:
            continue
        before = original_by_id.loc[row_id]
        if any(
            comparable_value(row.get(column), column)
            != comparable_value(before.get(column), column)
            for column in compare_columns
        ):
            changed_positions.append(position)

    if not changed_positions:
        return edited.iloc[0:0].copy()
    return edited.iloc[changed_positions].copy()


def _single_visible_category(df):
    if df.empty or "category" not in df.columns:
        return ""
    values = (
        df["category"]
        .fillna("")
        .astype(str)
        .str.strip()
        .replace("", pd.NA)
        .dropna()
        .unique()
        .tolist()
    )
    return values[0] if len(values) == 1 else ""


def _subcategory_column_config(category, disabled_help, existing_values=None):
    if category:
        options = _subcategory_options_for(category)
        for value in existing_values or []:
            text = str(value or "").strip()
            if text and text not in options:
                options.append(text)
        return st.column_config.SelectboxColumn(
            "Subcategory",
            options=options,
            required=False,
            help=f"Filtered to subcategories defined for {category}.",
        )
    return st.column_config.TextColumn(
        "Subcategory",
        disabled=True,
        help=disabled_help,
    )


_CATEGORY_PAIR_COLUMN = "category_subcategory"
_NO_SUBCATEGORY_LABEL = "No subcategory"


def _category_pair_label(category, subcategory):
    category_text = str(category or "").strip()
    subcategory_text = str(subcategory or "").strip()
    if not category_text:
        return ""
    return f"{category_text} / {subcategory_text or _NO_SUBCATEGORY_LABEL}"


def _parse_category_pair_label(value):
    if value is None or pd.isna(value):
        return "", ""
    text = str(value or "").strip()
    if not text:
        return "", ""
    if " / " not in text:
        return text, ""
    category, subcategory = text.split(" / ", 1)
    subcategory = "" if subcategory.strip() == _NO_SUBCATEGORY_LABEL else subcategory
    return category.strip(), subcategory.strip()


def _category_pair_options(categories_df=None, categories=None, current_df=None):
    options = [""]
    seen = {""}

    def add_pair(category, subcategory=""):
        label = _category_pair_label(category, subcategory)
        key = label.casefold()
        if label and key not in seen:
            seen.add(key)
            options.append(label)

    if categories_df is not None and not categories_df.empty:
        for _, row in categories_df.iterrows():
            category = str(row.get("category", "") or "").strip()
            subcategory = str(row.get("subcategory", "") or "").strip()
            if category:
                add_pair(category, "")
                if subcategory:
                    add_pair(category, subcategory)
    for category in categories or []:
        add_pair(category, "")
    if current_df is not None and not current_df.empty:
        for _, row in current_df.iterrows():
            add_pair(row.get("category", ""), row.get("subcategory", ""))
    return options


def _with_category_pair_column(df):
    out = df.copy()
    out[_CATEGORY_PAIR_COLUMN] = out.apply(
        lambda row: _category_pair_label(row.get("category", ""), row.get("subcategory", "")),
        axis=1,
    )
    return out


def _apply_category_pair_values(df):
    out = df.copy()
    if _CATEGORY_PAIR_COLUMN not in out.columns:
        return out
    for index, value in out[_CATEGORY_PAIR_COLUMN].items():
        category, subcategory = _parse_category_pair_label(value)
        if category:
            out.at[index, "category"] = category
            out.at[index, "subcategory"] = subcategory
    return out


def _editor_row_signature(df):
    if df is None or df.empty:
        return "empty"
    if "id" in df.columns:
        values = df["id"].fillna("").astype(str).tolist()
    else:
        values = [str(value) for value in df.index.tolist()]
    digest = hashlib.sha1("|".join(values).encode("utf-8")).hexdigest()
    return digest[:12]


def _scoped_editor_key(base_key, df):
    return f"{base_key}_{_editor_row_signature(df)}"


def _capture_data_editor_state(editor_key):
    state = st.session_state.get(editor_key)
    if not isinstance(state, dict):
        return
    captured_key = f"{editor_key}__captured_state"
    captured_state = st.session_state.get(captured_key)
    next_state = copy.deepcopy({
        "edited_rows": state.get("edited_rows") or {},
        "added_rows": state.get("added_rows") or [],
        "deleted_rows": state.get("deleted_rows") or [],
    })
    if (
        not next_state["edited_rows"]
        and isinstance(captured_state, dict)
        and captured_state.get("edited_rows")
    ):
        return
    st.session_state[captured_key] = next_state


def _apply_data_editor_state(df, editor_key):
    state = st.session_state.get(editor_key)
    captured_state = st.session_state.get(f"{editor_key}__captured_state")
    if isinstance(captured_state, dict) and captured_state.get("edited_rows"):
        state = captured_state
    if not isinstance(state, dict):
        return df
    edited_rows = state.get("edited_rows") or {}
    if not edited_rows:
        return df
    out = df.copy()
    columns = list(out.columns)
    for raw_position, changes in edited_rows.items():
        if not isinstance(changes, dict):
            continue
        try:
            position = int(raw_position)
        except (TypeError, ValueError):
            continue
        if position < 0 or position >= len(out):
            continue
        for column, value in changes.items():
            if column in columns:
                out.iat[position, columns.index(column)] = value
        if (
            _CATEGORY_PAIR_COLUMN in columns
            and _CATEGORY_PAIR_COLUMN not in changes
            and ("category" in changes or "subcategory" in changes)
        ):
            category = out.iat[position, columns.index("category")] if "category" in columns else ""
            subcategory = out.iat[position, columns.index("subcategory")] if "subcategory" in columns else ""
            out.iat[position, columns.index(_CATEGORY_PAIR_COLUMN)] = _category_pair_label(category, subcategory)
    return out


def _edited_data_editor_rows(df, editor_key):
    state = st.session_state.get(editor_key)
    captured_state = st.session_state.get(f"{editor_key}__captured_state")
    if isinstance(captured_state, dict) and captured_state.get("edited_rows"):
        state = captured_state
    if not isinstance(state, dict):
        return df.iloc[0:0].copy()
    positions = []
    for raw_position in (state.get("edited_rows") or {}):
        try:
            position = int(raw_position)
        except (TypeError, ValueError):
            continue
        if 0 <= position < len(df):
            positions.append(position)
    if not positions:
        return df.iloc[0:0].copy()
    return df.iloc[sorted(set(positions))].copy()


def _clear_data_editor_state(editor_key):
    st.session_state.pop(editor_key, None)
    st.session_state.pop(f"{editor_key}__captured_state", None)


def _clear_transaction_read_caches():
    for cached_reader in [
        get_all_transactions,
        get_dashboard_counts,
        get_memory,
        get_pending_transactions,
        get_saved_transactions,
        get_transaction_change_log,
    ]:
        cached_reader.clear()


def _verify_transaction_edit_save(save_df):
    def numeric_values_match(actual_value, expected_value):
        if actual_value is None or pd.isna(actual_value):
            return expected_value is None or pd.isna(expected_value)
        if expected_value is None or pd.isna(expected_value):
            return False
        return math.isclose(
            float(actual_value),
            float(expected_value),
            rel_tol=0.0,
            abs_tol=0.000001,
        )

    persisted = get_transaction_edit_states(save_df["id"].tolist())
    persisted_by_id = {
        int(row["id"]): row
        for _, row in persisted.iterrows()
    }
    for _, expected in save_df.iterrows():
        row_id = int(expected["id"])
        actual = persisted_by_id.get(row_id)
        if actual is None:
            raise RuntimeError(f"Transaction ID {row_id} was not found after saving.")
        expected_category = str(expected.get("category", "") or "").strip()
        expected_subcategory = str(expected.get("subcategory", "") or "").strip()
        expected_reviewed = bool(expected.get("reviewed", False))
        expected_status = str(expected.get("status", "") or "").strip().casefold()
        amount_matches = True
        amount_usd_matches = True
        if "amount" in save_df.columns:
            amount_matches = numeric_values_match(actual.get("amount"), expected.get("amount"))
            expected_amount_usd = expected.get("_saved_amount_usd")
            if expected_amount_usd is not None and not pd.isna(expected_amount_usd):
                amount_usd_matches = numeric_values_match(
                    actual.get("amount_usd"), expected_amount_usd
                )
        if (
            str(actual.get("category", "") or "").strip() != expected_category
            or str(actual.get("subcategory", "") or "").strip() != expected_subcategory
            or bool(actual.get("reviewed", False)) != expected_reviewed
            or str(actual.get("status", "") or "").strip().casefold() != expected_status
            or not amount_matches
            or not amount_usd_matches
        ):
            raise RuntimeError(
                f"Transaction ID {row_id} could not be verified after saving. "
                "The report was not marked as refreshed."
            )


def _add_transaction_edit_expectations(save_df, baseline_df):
    if save_df.empty or baseline_df.empty or "id" not in baseline_df.columns:
        return save_df.copy()
    baseline_columns = [
        column
        for column in ["id", "category", "subcategory", "reviewed"]
        if column in baseline_df.columns
    ]
    baseline = baseline_df[baseline_columns].copy().rename(columns={
        "category": "_expected_category",
        "subcategory": "_expected_subcategory",
        "reviewed": "_expected_reviewed",
    })
    return save_df.merge(baseline, on="id", how="left", validate="one_to_one")


def _refresh_category_pair_derived_columns(df, categories_df):
    out = _apply_category_pair_values(df)
    if {"category", "subcategory"}.issubset(out.columns):
        out = add_report_group_column(out, categories_df)
    if _CATEGORY_PAIR_COLUMN in out.columns and {"category", "subcategory"}.issubset(out.columns):
        out[_CATEGORY_PAIR_COLUMN] = out.apply(
            lambda row: _category_pair_label(row.get("category", ""), row.get("subcategory", "")),
            axis=1,
        )
    return out


def _resolve_report_group_for_pair(category, subcategory, categories_df=None):
    if categories_df is None:
        categories_df = get_categories(include_subcategories=True)
    exact_group_map, category_group_map = category_pair_report_group_maps(categories_df)
    category_key = str(category or "").strip().casefold()
    if not category_key:
        return ""
    pair_key = (category_key, _report_group_subcategory_key(subcategory))
    return exact_group_map.get(pair_key) or category_group_map.get(category_key, "")


def _render_report_group_preview(category, subcategory, key_prefix, categories_df=None):
    if not category:
        return
    report_group = _resolve_report_group_for_pair(category, subcategory, categories_df)
    label = report_group or "Unassigned reporting group"
    st.caption(f"Reporting group after save: {label}")


def render_wrapped_descriptions(df, expanded=False):
    preview = df[["txn_date", "amount", "original_description"]].head(80).copy()
    rows = []
    for _, row in preview.iterrows():
        rows.append(
            "<div class=\"soft-panel\">"
            f"<div class=\"summary-label\">{escape(str(row.get('txn_date', '')))} | "
            f"{escape(format_currency(row.get('amount', 0)))}</div>"
            f"<div style=\"margin-top:6px; line-height:1.45; white-space:normal;\">"
            f"{escape(str(row.get('original_description', '')))}</div>"
            "</div>"
        )
    if expanded:
        st.markdown("".join(rows), unsafe_allow_html=True)
    else:
        with st.expander("Full statement descriptions"):
            st.markdown("".join(rows), unsafe_allow_html=True)


def _subcategory_options_for(category):
    if not category:
        return [""]
    return [""] + get_subcategories(category=category)


def _transaction_label(row):
    tx_id = int(row.get("id")) if not pd.isna(row.get("id")) else ""
    date_text = str(row.get("txn_date", "") or "")
    amount_text = format_currency(row.get("amount", 0))
    description = re.sub(r"\s+", " ", str(row.get("original_description", "") or "")).strip()
    if len(description) > 95:
        description = description[:92] + "..."
    return f"{tx_id} | {date_text} | {amount_text} | {description}"


def render_category_correction_panel(
    df,
    categories,
    key_prefix,
    title="Correct category / subcategory",
    expanded=False,
    inline=False,
):
    if df.empty or "id" not in df.columns or not categories:
        return
    working = df.dropna(subset=["id"]).copy()
    if working.empty:
        return
    working["id"] = working["id"].astype(int)
    labels = {_transaction_label(row): int(row["id"]) for _, row in working.iterrows()}

    if inline:
        st.markdown(f"#### {title}")
        panel = st.container()
    else:
        panel = st.expander(title, expanded=expanded)

    with panel:
        st.caption(
            "Choose the transaction first, then choose the category. "
            "The subcategory list is filtered to that category only."
        )
        selected_label = st.selectbox(
            "Transaction",
            list(labels.keys()),
            key=f"{key_prefix}_transaction",
        )
        selected_id = labels[selected_label]
        selected_row = working[working["id"] == selected_id].iloc[0]

        category_options = [""] + categories
        active_key = f"{key_prefix}_active_id"
        category_key = f"{key_prefix}_category"
        subcategory_key = f"{key_prefix}_subcategory"
        if st.session_state.get(active_key) != selected_id:
            current_category = str(selected_row.get("category", "") or "").strip()
            st.session_state[category_key] = current_category if current_category in category_options else ""
            st.session_state[active_key] = selected_id

        category = st.selectbox("Category", category_options, key=category_key)
        subcategory_options = _subcategory_options_for(category)
        if category and len(subcategory_options) <= 1:
            st.info("No subcategories are defined for this category in Setup.")
        current_subcategory = str(selected_row.get("subcategory", "") or "").strip()
        if st.session_state.get(subcategory_key) not in subcategory_options:
            st.session_state[subcategory_key] = (
                current_subcategory if current_subcategory in subcategory_options else ""
            )
        subcategory = st.selectbox("Subcategory", subcategory_options, key=subcategory_key)
        _render_report_group_preview(category, subcategory, key_prefix)

        st.text_area(
            "Full statement description",
            value=str(selected_row.get("original_description", "") or ""),
            height=115,
            disabled=True,
            key=f"{key_prefix}_description_{selected_id}",
        )
        disabled = not category
        if st.button("Update selected transaction", type="primary", disabled=disabled, key=f"{key_prefix}_apply"):
            update_df = pd.DataFrame([{
                "id": selected_id,
                "category": category,
                "subcategory": subcategory,
                "reviewed": True,
                "status": "reviewed",
            }])
            update_df = _add_transaction_edit_expectations(
                update_df,
                pd.DataFrame([selected_row.to_dict()]),
            )
            try:
                count = update_database_rows(update_df)
                if count != 1:
                    raise RuntimeError("The database did not confirm the selected transaction update.")
                _verify_transaction_edit_save(update_df)
            except ConcurrentTransactionEditError as exc:
                st.error(str(exc))
            except Exception as exc:
                st.error(f"Could not update the selected transaction: {exc}")
            else:
                st.success("Transaction updated and marked as reviewed.")
                _clear_transaction_read_caches()
                st.rerun()


def render_transaction_exclusion_panel(df, key_prefix):
    if df.empty or "id" not in df.columns:
        return
    working = df.dropna(subset=["id"]).copy()
    if working.empty:
        return
    working["id"] = working["id"].astype(int)
    if "status" in working.columns:
        working["_status_key"] = working["status"].fillna("pending").astype(str).str.strip().str.casefold()
    else:
        working["_status_key"] = "pending"
    active_rows = working[working["_status_key"] != "excluded"].copy()
    excluded_rows = working[working["_status_key"] == "excluded"].copy()

    with st.expander("Exclude or restore transactions"):
        st.caption(
            "Use this instead of a hard delete. Excluded rows are hidden from Pending Review, active reports, "
            "active duplicate checks, and the normal Database view, but remain in the backup and change log."
        )

        if active_rows.empty:
            st.info("No active rows are visible in the current filter.")
        else:
            active_labels = {_transaction_label(row): int(row["id"]) for _, row in active_rows.iterrows()}
            selected_exclude = st.selectbox(
                "Transaction to exclude",
                [""] + list(active_labels.keys()),
                key=f"{key_prefix}_exclude_ids",
                help="Filter/search the Database first, then choose one row only. This prevents accidental bulk deletion.",
            )
            selected_exclude_id = active_labels.get(selected_exclude) if selected_exclude else None
            if selected_exclude_id is not None:
                preview_cols = [
                    "id",
                    "txn_date",
                    "currency",
                    "amount",
                    "amount_usd",
                    "account_name",
                    "bank",
                    "account_number",
                    "original_description",
                    "category",
                    "subcategory",
                    "status",
                ]
                selected_preview = active_rows[active_rows["id"] == selected_exclude_id]
                st.dataframe(
                    selected_preview[[col for col in preview_cols if col in selected_preview.columns]],
                    use_container_width=True,
                    hide_index=True,
                )
            exclude_reason = st.text_input(
                "Reason / note",
                value="duplicate or not required",
                key=f"{key_prefix}_exclude_reason",
            )
            expected_confirm = f"EXCLUDE {selected_exclude_id}" if selected_exclude_id is not None else ""
            confirm_exclude = st.text_input(
                "Confirmation",
                value="",
                key=f"{key_prefix}_exclude_confirm",
                help=f"Type {expected_confirm} to remove this one row from active data." if expected_confirm else "Choose a transaction first.",
            )
            if st.button(
                "Exclude this transaction",
                type="primary",
                disabled=selected_exclude_id is None or confirm_exclude.strip() != expected_confirm,
                key=f"{key_prefix}_exclude_apply",
            ):
                count = exclude_transactions([selected_exclude_id], exclude_reason)
                st.success(
                    f"Excluded {count} transaction row. It is now hidden from Pending Review, active reports, "
                    "and duplicate checks, and remains recoverable in the change log."
                )
                st.cache_data.clear()
                st.rerun()

        if excluded_rows.empty:
            st.caption("No excluded rows are visible in the current filter.")
        else:
            excluded_labels = {_transaction_label(row): int(row["id"]) for _, row in excluded_rows.iterrows()}
            selected_restore = st.multiselect(
                "Excluded transactions to restore",
                list(excluded_labels.keys()),
                key=f"{key_prefix}_restore_ids",
            )
            confirm_restore = st.checkbox(
                "I confirm these selected rows should be restored to active data.",
                key=f"{key_prefix}_restore_confirm",
            )
            if st.button(
                "Restore selected transactions",
                disabled=not selected_restore or not confirm_restore,
                key=f"{key_prefix}_restore_apply",
            ):
                count = restore_transactions([excluded_labels[label] for label in selected_restore])
                st.success(f"Restored {count} transaction row(s).")
                st.cache_data.clear()
                st.rerun()


def render_bulk_categorise_panel(df, categories, key_prefix, expanded=False, inline=False):
    if df.empty or "id" not in df.columns or not categories:
        return
    saved_message = st.session_state.pop(f"{key_prefix}_bulk_save_message", "")
    if saved_message:
        st.success(saved_message)
    title = "Bulk categorise current filtered rows"
    if inline:
        st.markdown(f"#### {title}")
        panel = st.container()
    else:
        panel = st.expander(title, expanded=expanded)

    with panel:
        st.caption(
            "Use this after filtering, for example by description. "
            "It applies one category/subcategory to all rows currently visible below."
        )
        category_options = [""] + categories
        category = st.selectbox("Category", category_options, key=f"{key_prefix}_bulk_category")
        subcategory_options = _subcategory_options_for(category)
        subcategory = st.selectbox(
            "Subcategory",
            subcategory_options,
            key=f"{key_prefix}_bulk_subcategory",
        )
        if category and len(subcategory_options) <= 1:
            st.info("No subcategories are defined for this category in Setup.")
        _render_report_group_preview(category, subcategory, f"{key_prefix}_bulk")
        disabled = not category
        if st.button(
            f"Apply to {len(df)} visible rows and mark reviewed",
            type="primary",
            disabled=disabled,
            key=f"{key_prefix}_bulk_apply",
        ):
            update_df = pd.DataFrame({
                "id": df["id"].dropna().astype(int),
                "category": category,
                "subcategory": subcategory,
                "reviewed": True,
                "status": "reviewed",
            })
            original = df.dropna(subset=["id"]).copy()
            original["id"] = original["id"].astype(int)
            original_by_id = original.drop_duplicates("id").set_index("id")
            update_df["_expected_category"] = update_df["id"].map(
                lambda row_id: str(original_by_id.loc[row_id].get("category", "") or "").strip()
            )
            update_df["_expected_subcategory"] = update_df["id"].map(
                lambda row_id: str(original_by_id.loc[row_id].get("subcategory", "") or "").strip()
            )
            update_df["_expected_reviewed"] = update_df["id"].map(
                lambda row_id: bool(original_by_id.loc[row_id].get("reviewed", False))
            )
            try:
                with st.status(f"Saving {len(update_df)} transaction edits...", expanded=True) as save_status:
                    count = save_reviewed_rows(update_df)
                    _verify_transaction_edit_save(update_df)
                    save_status.update(
                        label=f"{count} transactions updated successfully.",
                        state="complete",
                    )
                _clear_transaction_read_caches()
                st.session_state[f"{key_prefix}_bulk_save_message"] = (
                    f"{count} transactions updated successfully."
                )
                st.rerun()
            except Exception as exc:
                st.error(f"No transactions were saved. {exc}")


def _parse_split_amount_input(value):
    if value is None or pd.isna(value):
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    text = str(value).strip()
    if not text:
        return None
    text = re.sub(r"[\s$€£]", "", text)
    if text.startswith("-"):
        return None
    if "," in text and "." in text:
        if text.rfind(",") > text.rfind("."):
            text = text.replace(".", "").replace(",", ".")
        else:
            text = text.replace(",", "")
    elif "," in text:
        parts = text.split(",")
        if len(parts) == 2 and 1 <= len(parts[1]) <= 2:
            text = f"{parts[0]}.{parts[1]}"
        else:
            text = "".join(parts)
    amount = pd.to_numeric(text, errors="coerce")
    if pd.isna(amount):
        return None
    return float(amount)


def render_transaction_split_panel(df, categories_df, categories, key_prefix):
    if df.empty or "id" not in df.columns or not categories:
        return
    working = df.dropna(subset=["id"]).copy()
    if working.empty:
        return
    working["id"] = working["id"].astype(int)
    if "status" in working.columns:
        working["_status_key"] = working["status"].fillna("pending").astype(str).str.strip().str.casefold()
        working = working[working["_status_key"] != "excluded"].copy()
    if "split_parent_id" in working.columns:
        split_parent_ids = pd.to_numeric(working["split_parent_id"], errors="coerce")
        working = working[split_parent_ids.isna()].copy()
    if working.empty:
        return

    with st.expander("Split one transaction into multiple allocations"):
        st.caption(
            "Choose one transaction by ID, enter positive allocation amounts, and choose valid "
            "Category / Subcategory pairs. The app keeps the original transaction sign and excludes "
            "the original row from active reports so it is not counted twice."
        )
        labels = {_transaction_label(row): int(row["id"]) for _, row in working.iterrows()}
        selected_label = st.selectbox(
            "Transaction to split",
            list(labels.keys()),
            key=f"{key_prefix}_split_transaction",
        )
        selected_id = labels[selected_label]
        selected_row = working[working["id"] == selected_id].iloc[0]
        original_amount_value = pd.to_numeric(selected_row.get("amount", 0), errors="coerce")
        original_amount = 0.0 if pd.isna(original_amount_value) else float(original_amount_value)
        target_amount = abs(original_amount)
        st.info(
            f"Original transaction ID {selected_id}: {format_currency(original_amount)}. "
            f"Allocation total must equal {format_currency(target_amount)}."
        )
        allocation_count = int(st.number_input(
            "Number of split rows",
            min_value=2,
            max_value=20,
            value=2,
            step=1,
            key=f"{key_prefix}_split_count",
        ))
        amount_column = "amount"
        default_rows = pd.DataFrame({
            amount_column: [""] * allocation_count,
            _CATEGORY_PAIR_COLUMN: [""] * allocation_count,
        })
        pair_options = _category_pair_options(categories_df, categories)
        split_editor_key = f"{key_prefix}_split_editor_{selected_id}_{allocation_count}"
        split_edit = st.data_editor(
            default_rows,
            use_container_width=True,
            hide_index=True,
            num_rows="fixed",
            column_config={
                amount_column: st.column_config.TextColumn(
                    "Split amount",
                    help=(
                        "Enter a positive amount. Decimal dot and decimal comma are both accepted, "
                        "for example 4233.06 or 4233,06. The original transaction sign is preserved automatically."
                    ),
                ),
                _CATEGORY_PAIR_COLUMN: st.column_config.SelectboxColumn(
                    "Category / Subcategory",
                    options=pair_options,
                    required=True,
                ),
            },
            key=split_editor_key,
            on_change=_capture_data_editor_state,
            args=(split_editor_key,),
        )
        split_edit = _apply_data_editor_state(split_edit, split_editor_key)
        split_preview = _refresh_category_pair_derived_columns(split_edit, categories_df)
        parsed_amounts = split_preview[amount_column].apply(_parse_split_amount_input)
        entered_total = float(parsed_amounts.fillna(0).sum())
        difference = round(target_amount - entered_total, 2)
        c1, c2, c3 = st.columns(3)
        c1.metric("Original amount", format_currency(target_amount))
        c2.metric("Split total", format_currency(entered_total))
        c3.metric("Difference", format_currency(difference))

        parsed_pairs = []
        duplicate_pairs = set()
        seen_pairs = set()
        for _, row in split_preview.iterrows():
            category = str(row.get("category", "") or "").strip()
            subcategory = str(row.get("subcategory", "") or "").strip()
            if category:
                pair_key = (category.casefold(), subcategory.casefold())
                if pair_key in seen_pairs:
                    duplicate_pairs.add(_category_pair_label(category, subcategory))
                seen_pairs.add(pair_key)
            parsed_pairs.append((category, subcategory))
        invalid_rows = []
        for position, (_, row) in enumerate(split_preview.iterrows()):
            amount = parsed_amounts.iloc[position]
            category, _ = parsed_pairs[position]
            if amount is None or pd.isna(amount) or float(amount) <= 0 or not category:
                invalid_rows.append(position + 1)
        if invalid_rows:
            st.warning(
                f"Rows {', '.join(map(str, invalid_rows))} need a positive amount and category. "
                "Use decimal dot or comma, for example 4233.06 or 4233,06."
            )
        if duplicate_pairs:
            st.warning("Duplicate allocations are not allowed: " + ", ".join(sorted(duplicate_pairs)))
        if abs(difference) > 0.005:
            st.warning("The split total must exactly equal the original transaction amount before saving.")
        if "report_group" in split_preview.columns and split_preview["category"].fillna("").astype(str).str.strip().any():
            st.dataframe(
                split_preview[[
                    col for col in [amount_column, _CATEGORY_PAIR_COLUMN, "report_group"] if col in split_preview.columns
                ]],
                use_container_width=True,
                hide_index=True,
            )

        can_save = not invalid_rows and not duplicate_pairs and abs(difference) <= 0.005
        if st.button(
            "Create split allocations",
            type="primary",
            disabled=not can_save,
            key=f"{key_prefix}_split_apply",
        ):
            allocations = []
            for position, (_, row) in enumerate(split_preview.iterrows()):
                category, subcategory = _parse_category_pair_label(row.get(_CATEGORY_PAIR_COLUMN, ""))
                allocations.append({
                    "amount": parsed_amounts.iloc[position],
                    "category": category,
                    "subcategory": subcategory,
                })
            try:
                result = split_transaction(selected_id, allocations)
                st.success(
                    f"Transaction {selected_id} was split into {result['inserted']} allocation rows. "
                    "The original row remains traceable and is excluded from active reports to avoid double counting."
                )
                st.cache_data.clear()
                st.rerun()
            except Exception as exc:
                st.error(str(exc))


def render_manual_transaction_form(categories, subcategories):
    with st.expander("Add manual transaction"):
        if not categories:
            st.info("Load expense categories in Setup before adding manual transactions.")
            return
        manual_accounts = get_accounts()
        manual_labels, manual_lookup = (
            account_options(manual_accounts) if not manual_accounts.empty else ([""], {"": {}})
        )
        mc1, mc2, mc3 = st.columns(3)
        manual_date = mc1.date_input("Date", key="manual_date")
        manual_account_label = mc2.selectbox("Account", manual_labels, key="manual_account")
        manual_amount = mc3.number_input("Amount", value=0.0, step=1.0, format="%.2f", key="manual_amount")
        manual_description = st.text_input("Full statement description", key="manual_description")
        manual_category = st.selectbox("Category", categories, key="manual_category")
        manual_subcategory_options = _subcategory_options_for(manual_category)
        if st.session_state.get("manual_subcategory") not in manual_subcategory_options:
            st.session_state["manual_subcategory"] = ""
        manual_subcategory = st.selectbox(
            "Subcategory",
            manual_subcategory_options,
            key="manual_subcategory",
        )
        _render_report_group_preview(manual_category, manual_subcategory, "manual")
        if st.button("Save manual transaction", type="primary", key="manual_save"):
            inserted = insert_manual_transaction(
                manual_date,
                manual_description,
                manual_amount,
                manual_category,
                manual_subcategory,
                manual_lookup.get(manual_account_label, {}),
            )
            if inserted:
                st.success("Manual transaction saved.")
                st.cache_data.clear()
                st.rerun()
            else:
                st.warning("This manual transaction already exists.")


def render_app_header():
    today_at = "Today, " + app_now().strftime("%d %b %Y, %H:%M")
    st.markdown(
        f"""
        <div class="app-header">
            <div class="app-title-wrap">
                <div class="app-brand-mark">SM</div>
                <div>
                    <h1 class="app-title">Statement Management</h1>
                    <div class="app-subtitle">Transaction review, account balances, reporting, and setup in one workspace.</div>
                </div>
            </div>
            <div class="updated-pill">{today_at}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def render_status_bar():
    try:
        counts = get_dashboard_counts()
    except Exception as exc:
        st.warning(f"Dashboard counts are temporarily unavailable: {exc}")
        return
    cards = [
        ("Categories", counts["categories"]),
        ("Accounts", counts["accounts"]),
        ("Rates", counts["rates"]),
        ("Pending", counts["pending"]),
        ("Reviewed", counts["reviewed"]),
        ("Memory", counts["memory"]),
        ("Statements", counts["statements"]),
    ]
    html = []
    for label, value in cards:
        html.append(
            f"<div class=\"metric-card\">"
            f"<div class=\"metric-row\">"
            f"<div class=\"metric-label\">{label}</div>"
            f"</div>"
            f"<div class=\"metric-value\">{value}</div>"
            f"</div>"
        )
    st.markdown(f"<div class=\"metric-grid\">{''.join(html)}</div>", unsafe_allow_html=True)


def load_shared_setup_files():
    with SHARED_SETUP_FILES["categories"].open("rb") as handle:
        category_count = replace_categories_from_excel(handle)
    with SHARED_SETUP_FILES["accounts"].open("rb") as handle:
        account_count = replace_accounts_from_excel(handle)
    with SHARED_SETUP_FILES["rates"].open("rb") as handle:
        rate_count = replace_rates_from_excel(handle)
    return category_count, account_count, rate_count


def missing_setup_items(categories=None, accounts=None, rates=None):
    categories = get_categories() if categories is None else categories
    accounts = get_accounts() if accounts is None else accounts
    rates = get_rates() if rates is None else rates

    missing = []
    categories_empty = categories.empty if isinstance(categories, pd.DataFrame) else not categories
    if categories_empty:
        missing.append("expense categories")
    if accounts.empty:
        missing.append("account details")
    if rates.empty:
        missing.append("monthly rates")
    return missing


def missing_account_rate_types(accounts=None, rates=None):
    accounts = get_accounts() if accounts is None else accounts
    rates = get_rates() if rates is None else rates
    if accounts.empty:
        return []

    account_currencies = accounts.get("currency", pd.Series(dtype=str)).fillna("").astype(str).str.upper()
    required = sorted({f"{currency}/USD" for currency in account_currencies if currency and currency != "USD"})
    if not required:
        return []

    loaded = set(rates.get("rate_type", pd.Series(dtype=str)).fillna("").astype(str).str.upper())
    return [rate_type for rate_type in required if rate_type not in loaded]


def render_setup_loader(key_prefix):
    st.info(
        "First-time setup is required before importing statements. "
        "Upload the three control workbooks; after they are loaded, the statement uploader will unlock."
    )

    missing_shared = [
        label for label, path in SHARED_SETUP_FILES.items()
        if not path.exists()
    ]
    if not missing_shared:
        if st.button("Load setup from shared folder", type="primary", key=f"{key_prefix}_shared_setup"):
            category_count, account_count, rate_count = load_shared_setup_files()
            st.success(
                f"Loaded {category_count} category rows, "
                f"{account_count} accounts, and {rate_count} monthly rates."
            )
            st.cache_data.clear()
            st.rerun()

    c1, c2, c3 = st.columns(3)
    with c1:
        category_file = st.file_uploader(
            "Expense categories",
            type=["xlsx", "xls"],
            key=f"{key_prefix}_category_file",
        )
        if category_file and st.button("Replace categories", type="primary", key=f"{key_prefix}_replace_categories"):
            count = replace_categories_from_excel(category_file)
            st.success(f"Loaded {count} category rows.")
            st.cache_data.clear()
            st.rerun()

    with c2:
        account_file = st.file_uploader(
            "Who made the expense",
            type=["xlsx", "xls"],
            key=f"{key_prefix}_account_file",
        )
        if account_file and st.button("Replace accounts", type="primary", key=f"{key_prefix}_replace_accounts"):
            count = replace_accounts_from_excel(account_file)
            st.success(f"Loaded {count} account rows.")
            st.cache_data.clear()
            st.rerun()

    with c3:
        rates_file = st.file_uploader(
            "Monthly rates",
            type=["xlsx", "xls"],
            key=f"{key_prefix}_rates_file",
        )
        if rates_file and st.button("Replace rates", type="primary", key=f"{key_prefix}_replace_rates"):
            count = replace_rates_from_excel(rates_file)
            st.success(f"Loaded {count} monthly rates.")
            st.cache_data.clear()
            st.rerun()


EXECUTIVE_REPORT_PAGE = "Executive Summary"
SHARED_EXECUTIVE_REPORT_PAGES = {"Boss Report", "Read Only Report"}


def is_third_link_report_request():
    return st.query_params.get("page") in THIRD_LINK_REPORT_PAGES


def is_executive_report_request():
    return st.query_params.get("page") in {EXECUTIVE_REPORT_PAGE, *SHARED_EXECUTIVE_REPORT_PAGES}


def _is_shared_executive_report_request():
    return st.query_params.get("page") in SHARED_EXECUTIVE_REPORT_PAGES


def _money(value):
    if value is None or pd.isna(value):
        return "-"
    try:
        amount = round(float(value))
    except Exception:
        return "-"
    if amount < 0:
        return f"-${abs(amount):,.0f}"
    return f"${amount:,.0f}"


def _percent(value):
    if value is None or pd.isna(value):
        return "-"
    return f"{float(value):.1f}%"


def _executive_month_window(cutoff_month):
    start_month = pd.Period(year=cutoff_month.year, month=1, freq="M")
    return list(pd.period_range(start=start_month, end=cutoff_month, freq="M"))


def _executive_month_labels(months):
    return {month: month.to_timestamp().strftime("%b %Y").upper() for month in months}


def _executive_short_month_label(month):
    if month is None:
        return ""
    return month.to_timestamp().strftime("%b").upper()


def _executive_row_html(group, metrics, months):
    month_cells = "".join(
        f"<td>{_money(metrics['months'].get(month, 0.0))}</td>"
        for month in months
    )
    return (
        "<tr>"
        f"<td>{escape(str(group))}</td>"
        f"{month_cells}"
        f"<td>{_money(metrics['average'])}</td>"
        f"<td class=\"{metrics['trend_class']}\">{_money(metrics['change'])}</td>"
        f"<td class=\"{metrics['trend_class']}\">{_percent(metrics['change_pct'])}</td>"
        f"<td class=\"{metrics['trend_class']}\">{metrics['trend_text']}</td>"
        "</tr>"
    )


def _executive_trend(change):
    if abs(change) <= 0.005:
        return "trend-flat", "No change"
    if change > 0:
        return "trend-up", "Increasing"
    return "trend-down", "Decreasing"


def _executive_semantic_trend_class(css_class, label="", income_context=False):
    is_income = bool(income_context) or bool(re.search(r"\bincome\b", str(label or ""), re.IGNORECASE))
    if not is_income:
        return css_class
    return {
        "trend-up": "trend-down",
        "trend-down": "trend-up",
    }.get(css_class, css_class)


def _executive_change_pct(change, previous_amount):
    if abs(previous_amount) <= 0.005:
        return None
    return (change / abs(previous_amount)) * 100


def _executive_status_delta(current_amount, previous_amount):
    current = float(current_amount or 0.0)
    previous = float(previous_amount or 0.0)
    if abs(current) <= 0.005 and abs(previous) <= 0.005:
        return 0.0
    # For expense/funding rows, negative values are outflows and positive
    # values can be returns/refunds. Compare the movement as cost exposure,
    # so a return after historical outflows is a decrease, not an increase.
    if previous < -0.005 or (abs(previous) <= 0.005 and current < -0.005):
        return previous - current
    return current - previous


def _executive_status_change_pct(current_amount, previous_amount):
    if abs(previous_amount) <= 0.005:
        return None
    return (_executive_status_delta(current_amount, previous_amount) / abs(previous_amount)) * 100


def _executive_signed_amount_series(frame):
    if frame.empty:
        return pd.Series(dtype=float)
    source_column = "report_amount" if "report_amount" in frame.columns else "expense_usd"
    return pd.to_numeric(frame.get(source_column, pd.Series(dtype=float)), errors="coerce").fillna(0)


def _executive_amount_series(frame, context_frame=None):
    return _executive_signed_amount_series(frame)


def _executive_metric_values_from_month_values(month_values, months, denominator=0.0):
    total_amount = float(sum(month_values.values()))
    current_month = months[-1] if months else None
    previous_month = months[-2] if len(months) > 1 else None
    current_amount = month_values.get(current_month, 0.0)
    previous_amount = month_values.get(previous_month, 0.0)
    previous_trend_values = [month_values.get(month, 0.0) for month in months[:-1]]
    trend_baseline = (
        sum(previous_trend_values) / len(previous_trend_values)
        if previous_trend_values
        else current_amount
    )
    change = _executive_status_delta(current_amount, previous_amount)
    trend_class, trend_text = _executive_trend(change)
    period_change = _executive_status_delta(current_amount, trend_baseline)
    period_trend_class, period_trend_text = _executive_trend(period_change)
    return {
        "months": month_values,
        "current": current_amount,
        "previous": previous_amount,
        "period_start": trend_baseline,
        "total": total_amount,
        "share_pct": (abs(total_amount) / denominator * 100) if denominator > 0.005 else None,
        "average": (sum(month_values.values()) / len(month_values)) if month_values else 0.0,
        "change": change,
        "change_pct": _executive_status_change_pct(current_amount, previous_amount),
        "trend_class": trend_class,
        "trend_text": trend_text,
        "period_change": period_change,
        "period_change_pct": (
            _executive_status_change_pct(current_amount, trend_baseline)
            if previous_trend_values
            else None
        ),
        "period_trend_class": period_trend_class,
        "period_trend_text": period_trend_text,
    }


def _executive_metric_values(frame, months, denominator=0.0, context_frame=None):
    amount_series = _executive_amount_series(frame, context_frame=context_frame)
    month_values = {
        month: float(amount_series.loc[frame["month"] == month].sum())
        for month in months
    }
    # "Sum since Jan" must use the same Jan-to-report-month window as the
    # monthly columns. Summing the full frame can pull older historical rows
    # into the dashboard total while the drill-down/export period stays Jan+.
    return _executive_metric_values_from_month_values(month_values, months, denominator)


def _executive_share_denominator(expenses, level_column, labels, months=None):
    if expenses.empty or level_column not in expenses.columns:
        return 0.0
    amount_series = _executive_amount_series(expenses, context_frame=expenses)
    if months and "month" in expenses.columns:
        period_mask = expenses["month"].isin(months)
        amount_series = amount_series.loc[period_mask]
        label_series = expenses.loc[period_mask, level_column].fillna("").astype(str).str.strip()
    else:
        label_series = expenses[level_column].fillna("").astype(str).str.strip()
    totals = amount_series.groupby(label_series).sum()
    return float(sum(abs(float(totals.get(str(label or "").strip(), 0.0))) for label in labels))


def _executive_level_rows(expenses, level_column, months, extra_labels=None):
    rows = []
    extra_labels = _ordered_text_values(extra_labels or [])
    if (expenses.empty or level_column not in expenses.columns) and not extra_labels:
        return rows
    raw_labels = (
        expenses[level_column].fillna("").astype(str).str.strip().unique().tolist()
        if level_column in expenses.columns
        else []
    )
    if level_column == "subcategory":
        labels = sorted(raw_labels, key=lambda value: (value == "", value.casefold()))
    else:
        labels = sorted(value for value in raw_labels if value)
    if extra_labels:
        if level_column == "subcategory":
            # Preserve the blank bucket for real transactions without a subcategory.
            # _ordered_text_values intentionally drops blanks for normal setup labels.
            merged_labels = []
            seen = set()
            for value in labels + extra_labels:
                text = str(value or "").strip()
                key = text.casefold()
                if key in seen:
                    continue
                seen.add(key)
                merged_labels.append(text)
            labels = merged_labels
        else:
            labels = _ordered_text_values(labels + extra_labels)

    if expenses.empty or level_column not in expenses.columns or "month" not in expenses.columns:
        denominator = _executive_share_denominator(expenses, level_column, labels, months)
        for label in labels:
            frame = (
                expenses[expenses[level_column].fillna("").astype(str).str.strip() == label].copy()
                if level_column in expenses.columns
                else expenses.iloc[0:0].copy()
            )
            if frame.empty and label not in extra_labels:
                continue
            metric_context = frame if level_column == "report_group" else expenses
            metrics = _executive_metric_values(frame, months, denominator, context_frame=metric_context)
            metrics["label"] = label or "No subcategory"
            metrics["value"] = label
            rows.append(metrics)
        rows.sort(key=lambda row: (row["share_pct"] or 0.0, row["total"]), reverse=True)
        return rows

    label_series = expenses[level_column].fillna("").astype(str).str.strip()
    amount_series = _executive_amount_series(expenses, context_frame=expenses)
    grouped_source = pd.DataFrame({
        "_label": label_series,
        "_month": expenses["month"],
        "_amount": amount_series,
    })
    grouped = (
        grouped_source
        .groupby(["_label", "_month"], dropna=False)["_amount"]
        .sum()
        .unstack(fill_value=0.0)
    )
    period_columns = [month for month in months if month in grouped.columns]
    if period_columns:
        period_totals = grouped[period_columns].sum(axis=1)
    else:
        period_totals = pd.Series(0.0, index=grouped.index)
    denominator = float(sum(abs(float(period_totals.get(str(label or "").strip(), 0.0))) for label in labels))

    for label in labels:
        label_key = str(label or "").strip()
        if label_key not in grouped.index and label not in extra_labels:
            continue
        if label_key in grouped.index:
            month_row = grouped.loc[label_key]
            month_values = {month: float(month_row.get(month, 0.0)) for month in months}
        else:
            month_values = {month: 0.0 for month in months}
        metrics = _executive_metric_values_from_month_values(month_values, months, denominator)
        metrics["label"] = label or "No subcategory"
        metrics["value"] = label
        rows.append(metrics)
    rows.sort(key=lambda row: (row["share_pct"] or 0.0, row["total"]), reverse=True)
    return rows


def _executive_total_row(rows, months):
    source_rows = [row for row in rows if not row.get("is_total")]
    if not source_rows:
        return None
    month_values = {
        month: float(sum(float(row.get("months", {}).get(month, 0.0) or 0.0) for row in source_rows))
        for month in months
    }
    denominator = float(sum(abs(float(row.get("total") or 0.0)) for row in source_rows))
    total_row = _executive_metric_values_from_month_values(month_values, months, denominator)
    total_row["share_pct"] = 100.0 if denominator > 0.005 else None
    total_row.update({"label": "TOTAL", "value": "TOTAL", "is_total": True})
    return total_row


def _ordered_text_values(values):
    seen = set()
    out = []
    for value in values:
        text = str(value or "").strip()
        key = text.casefold()
        if not text or key in seen:
            continue
        seen.add(key)
        out.append(text)
    return out


def _executive_report_group_options(categories_df, expenses):
    setup_groups = (
        categories_df.get("report_group", pd.Series(dtype=str))
        .fillna("")
        .astype(str)
        .str.strip()
        .tolist()
        if not categories_df.empty
        else []
    )
    data_groups = (
        expenses.get("report_group", pd.Series(dtype=str))
        .fillna("")
        .astype(str)
        .str.strip()
        .tolist()
        if not expenses.empty
        else []
    )
    return _ordered_text_values(setup_groups + sorted(data_groups))


def _executive_category_options(categories_df, report_group):
    if categories_df.empty or "category" not in categories_df.columns:
        return []
    frame = categories_df.copy()
    if "report_group" in frame.columns:
        frame = frame[
            frame["report_group"].fillna("").astype(str).str.strip() == str(report_group or "").strip()
        ].copy()
    return _ordered_text_values(frame["category"].fillna("").astype(str).str.strip().tolist())


def _executive_subcategory_options(categories_df, category):
    if categories_df.empty or "subcategory" not in categories_df.columns:
        return []
    frame = categories_df.copy()
    if "category" in frame.columns:
        frame = frame[
            frame["category"].fillna("").astype(str).str.strip() == str(category or "").strip()
        ].copy()
    return _ordered_text_values(frame["subcategory"].fillna("").astype(str).str.strip().tolist())


def _executive_default_visible_groups(all_report_groups):
    group_settings = get_report_group_settings()
    if group_settings.empty:
        return all_report_groups
    settings = group_settings.copy()
    settings["report_group"] = settings["report_group"].fillna("").astype(str).str.strip()
    known_groups = set(settings["report_group"].tolist())
    visible_groups = (
        settings[settings["visible"].fillna(0).astype(int) == 1]["report_group"]
        .fillna("")
        .astype(str)
        .str.strip()
        .tolist()
    )
    visible_groups.extend(group for group in all_report_groups if group not in known_groups)
    return [group for group in _ordered_text_values(visible_groups) if group in all_report_groups]


def _third_link_default_visible_groups(all_report_groups):
    group_settings = get_report_group_settings()
    if not group_settings.empty and "third_link_visible" in group_settings.columns:
        settings = group_settings.copy()
        settings["report_group"] = settings["report_group"].fillna("").astype(str).str.strip()
        visible_groups = (
            settings[settings["third_link_visible"].fillna(0).astype(int) == 1]["report_group"]
            .fillna("")
            .astype(str)
            .str.strip()
            .tolist()
        )
        visible_groups = [group for group in visible_groups if group in all_report_groups]
        if visible_groups:
            return _ordered_text_values(visible_groups)

    woking_groups = [
        group for group in all_report_groups
        if "woking way llc" in str(group).casefold()
    ]
    return _ordered_text_values(woking_groups[:1])


def _report_group_prompt_lookup():
    settings = get_report_group_settings()
    if settings.empty or "ai_prompt" not in settings.columns:
        return {}
    return {
        str(row.get("report_group", "") or "").strip(): str(row.get("ai_prompt", "") or "").strip()
        for _, row in settings.iterrows()
        if str(row.get("report_group", "") or "").strip()
    }


def _save_executive_visible_groups(all_report_groups, visible_groups):
    existing = get_report_group_settings()
    existing_lookup = {}
    if not existing.empty:
        existing_lookup = {
            str(row.get("report_group", "") or "").strip(): row
            for _, row in existing.iterrows()
            if str(row.get("report_group", "") or "").strip()
        }
    settings_df = pd.DataFrame([
        {
            "report_group": group,
            "visible": group in visible_groups,
            "third_link_visible": bool(existing_lookup.get(group, {}).get("third_link_visible", 0)),
            "ai_prompt": str(existing_lookup.get(group, {}).get("ai_prompt", "") or ""),
        }
        for group in all_report_groups
    ])
    return replace_report_group_settings(settings_df)


def _render_report_group_visibility_editor(all_report_groups, default_visible_groups, key_prefix):
    editor_df = pd.DataFrame({
        "visible": [group in default_visible_groups for group in all_report_groups],
        "report_group": all_report_groups,
    })
    edited = st.data_editor(
        editor_df,
        hide_index=True,
        width="stretch",
        column_order=["visible", "report_group"],
        column_config={
            "visible": st.column_config.CheckboxColumn("Show", width="small"),
            "report_group": st.column_config.TextColumn("Reporting group", disabled=True),
        },
        disabled=["report_group"],
        key=f"{key_prefix}_visibility_table",
    )
    visible_groups = (
        edited.loc[edited["visible"].fillna(False).astype(bool), "report_group"]
        .fillna("")
        .astype(str)
        .str.strip()
        .tolist()
    )
    return _ordered_text_values(visible_groups)


def _render_executive_group_visibility_control(all_report_groups):
    if not all_report_groups:
        return []
    default_visible_groups = _executive_default_visible_groups(all_report_groups)
    if not default_visible_groups:
        default_visible_groups = all_report_groups

    with st.expander("Manage reporting groups shown", expanded=False):
        selected_visible_groups = _render_report_group_visibility_editor(
            all_report_groups,
            default_visible_groups,
            "executive_inline",
        )
        c1, c2 = st.columns([1, 1])
        with c1:
            if st.button("Save reporting group selection", type="primary", key="save_executive_groups_inline"):
                if not selected_visible_groups:
                    st.warning("Select at least one reporting group before saving.")
                else:
                    count = _save_executive_visible_groups(all_report_groups, selected_visible_groups)
                    st.success(f"Saved visibility for {count} reporting groups.")
                    st.cache_data.clear()
                    st.rerun()
        with c2:
            if st.button("Show all reporting groups", key="show_all_executive_groups_inline"):
                count = _save_executive_visible_groups(all_report_groups, all_report_groups)
                st.success(f"All {count} reporting groups are now shown.")
                st.cache_data.clear()
                st.rerun()

    return default_visible_groups


def _set_executive_selection(
    level,
    value,
    selection_key=None,
    clear_selection_keys=None,
    toggle=True,
):
    selection_key = selection_key or f"executive_{level}"
    if toggle and st.session_state.get(selection_key) == value:
        st.session_state.pop(selection_key, None)
    else:
        st.session_state[selection_key] = value

    if clear_selection_keys is None:
        clear_selection_keys = {
            "group": ["executive_category", "executive_subcategory"],
            "category": ["executive_subcategory"],
        }.get(level, [])
    for key in clear_selection_keys:
        st.session_state.pop(key, None)


def _clear_executive_selections(*keys):
    for key in keys:
        st.session_state.pop(key, None)


def _valid_executive_selection(expenses, column, value, extra_values=None):
    if value is None:
        return None
    values = set()
    if column in expenses.columns:
        values.update(expenses[column].fillna("").astype(str).str.strip().tolist())
    values.update(str(extra or "").strip() for extra in (extra_values or []))
    if column != "subcategory":
        values.discard("")
    return value if str(value).strip() in values else None


def _render_executive_click_rows(
    title,
    rows,
    level,
    months,
    month_labels,
    show_all_months=False,
    ai_prompts=None,
    show_zero_explanations=True,
    selection_key=None,
    clear_selection_keys=None,
    render_child=None,
    inline_selection=False,
    income_context=False,
):
    st.markdown(
        f"<div class=\"executive-section-title\">{escape(title)}</div>",
        unsafe_allow_html=True,
    )
    if not rows:
        st.info("No rows available for this level.")
        return
    display_months = months if show_all_months else months[-1:]
    trend_start = _executive_short_month_label(months[0] if months else None)
    trend_average_end = _executive_short_month_label(months[-2] if len(months) > 1 else None)
    trend_average_label = (
        f"AVG {trend_start}-{trend_average_end}"
        if trend_start and trend_average_end
        else "AVG PREVIOUS MONTHS"
    )
    if show_all_months:
        tail_defs = [
            ("change", "CHANGE FROM PREVIOUS MONTH", 1.25, lambda row: _money(row["change"]), "trend_class"),
            ("change_pct", "% CHANGE FROM PREVIOUS MONTH", 0.95, lambda row: _percent(row["change_pct"]), "trend_class"),
            ("trend_text", "STATUS FROM PREVIOUS MONTH", 1.25, lambda row: row["trend_text"], "trend_class"),
            ("period_change", f"TREND VS {trend_average_label}", 1.25, lambda row: _money(row["period_change"]), "period_trend_class"),
            ("period_change_pct", "% CHANGE IN TREND", 0.95, lambda row: _percent(row["period_change_pct"]), "period_trend_class"),
            ("period_trend_text", "TREND STATUS VS AVG", 1.15, lambda row: row["period_trend_text"], "period_trend_class"),
        ]
    else:
        tail_defs = [
            ("change_pct", "% CHANGE FROM PREVIOUS MONTH", 0.95, lambda row: _percent(row["change_pct"]), "trend_class"),
            ("trend_text", "STATUS FROM PREVIOUS MONTH", 1.25, lambda row: row["trend_text"], "trend_class"),
            ("period_change_pct", "% CHANGE IN TREND", 0.95, lambda row: _percent(row["period_change_pct"]), "period_trend_class"),
            ("period_trend_text", "TREND STATUS VS AVG", 1.15, lambda row: row["period_trend_text"], "period_trend_class"),
        ]
    base_defs = []
    if show_all_months:
        base_defs.extend([
            ("total", "Sum since Jan", 1, lambda row: _money(row["total"])),
            ("share_pct", "% OF TOTAL", 0.85, lambda row: _percent(row["share_pct"])),
            ("average", "Average", 1, lambda row: _money(row["average"])),
        ])
    first_col_width = 1.95 if show_all_months and ai_prompts is not None else 1.75 if show_all_months else 1.55
    widths = [first_col_width] + [width for _, _, width, _ in base_defs] + [1 for _ in display_months] + [
        width for _, _, width, _, _ in tail_defs
    ]
    if not show_all_months:
        st.markdown(
            """
            <style>
            div[data-testid="stHorizontalBlock"]:has(.drill-cell),
            div[data-testid="stHorizontalBlock"]:has(.summary-label) {
                max-width: 1120px !important;
            }
            </style>
            """,
            unsafe_allow_html=True,
        )
    header_cols = st.columns(widths)
    col_idx = 0
    header_cols[col_idx].markdown("<div class=\"summary-label\">Open</div>", unsafe_allow_html=True)
    for _, label, _, _ in base_defs:
        col_idx += 1
        header_cols[col_idx].markdown(f"<div class=\"summary-label\">{label}</div>", unsafe_allow_html=True)
    for month in display_months:
        col_idx += 1
        header_cols[col_idx].markdown(
            f"<div class=\"summary-label\">{escape(month_labels[month])}</div>",
            unsafe_allow_html=True,
        )
    for _, label, _, _, _ in tail_defs:
        col_idx += 1
        header_cols[col_idx].markdown(f"<div class=\"summary-label\">{label}</div>", unsafe_allow_html=True)

    selection_key = selection_key or f"executive_{level}"
    for idx, row in enumerate(rows):
        is_total = bool(row.get("is_total"))
        is_selected = st.session_state.get(selection_key) == row["value"]
        cols = st.columns(widths)
        col_idx = 0
        with cols[col_idx]:
            if is_total:
                st.markdown(
                    f"<div class=\"drill-cell drill-total-cell drill-total-label\">{escape(row['label'])}</div>",
                    unsafe_allow_html=True,
                )
            elif level == "group" and ai_prompts is not None:
                label_col, ai_col = st.columns([5, 1.25])
                with label_col:
                    if inline_selection:
                        st.button(
                            row["label"],
                            key=f"executive_{level}_{idx}",
                            type="primary" if is_selected else "secondary",
                            help="Collapse" if is_selected else "Open",
                            on_click=_set_executive_selection,
                            args=(level, row["value"]),
                            kwargs={
                                "selection_key": selection_key,
                                "clear_selection_keys": clear_selection_keys,
                            },
                            use_container_width=True,
                        )
                    elif st.button(row["label"], key=f"executive_{level}_{idx}", use_container_width=True):
                        _set_executive_selection(level, row["value"], toggle=False)
                        st.rerun()
                with ai_col:
                    if st.button("AI", key=f"executive_ai_{idx}", help=f"Open AI report for {row['label']}", use_container_width=True):
                        st.session_state["third_report_ai_group"] = row["value"]
                        st.session_state["third_report_ai_requested"] = True
                        st.rerun()
            else:
                if inline_selection:
                    st.button(
                        row["label"],
                        key=f"executive_{level}_{idx}",
                        type="primary" if is_selected else "secondary",
                        help="Collapse" if is_selected else "Open",
                        on_click=_set_executive_selection,
                        args=(level, row["value"]),
                        kwargs={
                            "selection_key": selection_key,
                            "clear_selection_keys": clear_selection_keys,
                        },
                        use_container_width=True,
                    )
                elif st.button(row["label"], key=f"executive_{level}_{idx}", use_container_width=True):
                    _set_executive_selection(level, row["value"], toggle=False)
                    st.rerun()
        for _, _, _, formatter in base_defs:
            col_idx += 1
            total_class = " drill-total-cell" if is_total else ""
            cols[col_idx].markdown(
                f"<div class=\"drill-cell{total_class}\">{formatter(row)}</div>",
                unsafe_allow_html=True,
            )
        for month in display_months:
            col_idx += 1
            total_class = " drill-total-cell" if is_total else ""
            cols[col_idx].markdown(
                f"<div class=\"drill-cell{total_class}\">{_money(row['months'].get(month, 0.0))}</div>",
                unsafe_allow_html=True,
            )
        for _, _, _, formatter, class_key in tail_defs:
            col_idx += 1
            css_class = _executive_semantic_trend_class(
                row.get(class_key, "trend-flat"),
                row.get("label", ""),
                income_context=income_context and not is_total,
            )
            total_class = " drill-total-cell" if is_total else ""
            cols[col_idx].markdown(
                f"<div class=\"drill-cell {css_class}{total_class}\">{formatter(row)}</div>",
                unsafe_allow_html=True,
            )
        if not is_total and render_child is not None and st.session_state.get(selection_key) == row["value"]:
            render_child(row["value"])
    zero_rows = []
    for row in rows:
        if row.get("is_total"):
            continue
        total = float(row.get("total") or 0.0)
        month_total = sum(float(value or 0.0) for value in row.get("months", {}).values())
        if abs(total) <= 0.005 and abs(month_total) <= 0.005:
            label = str(row.get("label") or "").strip()
            if label:
                reason = (
                    "Visible for completeness because it exists in Setup / Expense Excel, "
                    "but no reviewed reportable expense transactions are assigned to it in this report period."
                )
                if label.casefold() == "0-not on reports":
                    reason = (
                        "Control group kept visible for checking. It has no reviewed reportable expense activity "
                        "in the selected period."
                    )
                zero_rows.append({"Row": label, "Why it shows $0": reason})
    if show_zero_explanations and zero_rows:
        with st.expander(f"Why {title.lower()} rows show $0", expanded=False):
            st.dataframe(pd.DataFrame(zero_rows), use_container_width=True, hide_index=True)
            st.caption(
                "$0 rows are not hidden. They stay visible so setup groups, categories, and subcategories "
                "can be checked even when there is no activity in the selected period."
            )


def _executive_selected_transactions_export_sheets(visible):
    export_visible = visible.copy()
    export_total_usd = float(pd.to_numeric(
        export_visible.get("_display_amount_usd", pd.Series(dtype=float)),
        errors="coerce",
    ).fillna(0).sum())
    export_visible = export_visible.rename(columns={
        "txn_date": "Date",
        "currency": "Currency",
        "_display_amount_usd": "Dashboard amount USD",
        "amount": "Statement amount (original currency)",
        "original_description": "Full statement description",
        _CATEGORY_PAIR_COLUMN: "Category / Subcategory",
        "reviewed": "Reviewed",
        "id": "ID",
        "account_name": "Account",
        "bank": "Bank",
        "account_number": "Account number",
    })
    # The dashboard totals use signed USD report amounts. Keep the original
    # statement amount for audit, but make the matching total column explicit
    # so Excel checks reconcile with "Sum since Jan".
    export_order = [
        "Date",
        "Currency",
        "Dashboard amount USD",
        "Statement amount (original currency)",
        "Full statement description",
        "Category / Subcategory",
        "Reviewed",
        "ID",
        "Account",
        "Bank",
        "Account number",
    ]
    export_visible = export_visible[[col for col in export_order if col in export_visible.columns]]
    export_summary = pd.DataFrame([
        {"Metric": "Rows exported", "Value": len(export_visible)},
        {"Metric": "Dashboard total USD", "Value": round(export_total_usd, 2)},
        {"Metric": "Excel column to sum", "Value": "Dashboard amount USD"},
        {
            "Metric": "Statement amount note",
            "Value": "Original statement currency; do not use this column to reconcile dashboard USD totals.",
        },
    ])
    return {
        "Selected total": export_summary,
        "Transactions": export_visible,
    }


def _render_executive_transactions(
    expenses,
    selected_group,
    selected_category,
    selected_subcategory,
    months=None,
    read_only=False,
    categories_df=None,
):
    detail = expenses[
        (expenses["report_group"].fillna("").astype(str).str.strip() == selected_group)
        & (expenses["category"].fillna("").astype(str).str.strip() == selected_category)
        & (expenses["subcategory"].fillna("").astype(str).str.strip() == selected_subcategory)
    ].copy()
    if months and "month" in detail.columns:
        # Keep the visible detail grid and its Excel export on the same
        # Jan-to-report-month period used by the dashboard "Sum since Jan".
        detail = detail[detail["month"].isin(months)].copy()
    if detail.empty:
        st.info("No transactions found for the selected subcategory.")
        return
    detail["_display_amount_usd"] = _executive_signed_amount_series(detail).values
    detail["txn_date"] = pd.to_datetime(detail["txn_date"], errors="coerce").dt.strftime("%Y-%m-%d")
    detail = detail.sort_values(["txn_date", "_display_amount_usd"], ascending=[False, False])
    display_cols = [
        "txn_date",
        "currency",
        "amount",
        "original_description",
        "category",
        "subcategory",
        "reviewed",
        "id",
        "account_name",
        "bank",
        "account_number",
        "_display_amount_usd",
    ]
    st.markdown("#### Transaction Detail")
    detail_search = st.text_input(
        "Search transaction detail",
        placeholder="e.g. replit",
        key="executive_detail_search",
    )
    detail_view = detail.copy()
    if detail_search:
        mask = detail_view.astype(str).apply(
            lambda col: col.str.contains(detail_search, case=False, na=False)
        ).any(axis=1)
        detail_view = detail_view[mask].copy()

    if detail_view.empty:
        st.warning("No transactions match the current transaction detail search.")
        return

    visible = detail_view[[col for col in display_cols if col in detail_view.columns]].copy()
    visible = _with_category_pair_column(visible)
    executive_visible_cols = [
        "txn_date",
        "currency",
        "amount",
        "original_description",
        _CATEGORY_PAIR_COLUMN,
        "reviewed",
        "id",
        "account_name",
        "bank",
        "account_number",
        "_display_amount_usd",
    ]
    visible = visible[[col for col in executive_visible_cols if col in visible.columns]].copy()
    if not read_only:
        # Reuse the setup categories already loaded by the parent report. This
        # avoids extra DB/cache work when drilling into transaction detail while
        # keeping the same valid category/subcategory options.
        if categories_df is None:
            categories_df = get_categories(include_subcategories=True)
        if categories_df is not None and not categories_df.empty and "category" in categories_df.columns:
            categories = _ordered_text_values(
                categories_df["category"].fillna("").astype(str).str.strip().tolist()
            )
        else:
            categories = get_categories()
        pair_options = _category_pair_options(categories_df, categories, visible)
        render_summary_strip([
            ("Rows", len(visible)),
            ("Total", _money(detail_view["_display_amount_usd"].sum())),
            ("Category", selected_category),
            ("Subcategory", selected_subcategory or "No subcategory"),
        ])
        render_wrapped_descriptions(detail_view, expanded=False)
    if read_only:
        st.dataframe(visible, use_container_width=True, hide_index=True, height=min(520, 130 + max(len(visible), 4) * 34))
    else:
        render_category_correction_panel(
            detail_view,
            categories,
            "executive_detail_single",
            "Categorise one transaction with filtered subcategories",
            expanded=False,
            inline=False,
        )
        render_bulk_categorise_panel(detail_view, categories, "executive_detail", expanded=False, inline=False)
        st.caption(
            "Make all Category / Subcategory changes below, then apply them together. "
            "The report will not refresh between individual edits; Reporting Group is recalculated on save."
        )
        editor_visible = visible.copy()
        editor_visible["report_group"] = selected_group
        executive_editor_key = _scoped_editor_key("executive_detail_editor", editor_visible)
        editor_visible = _refresh_category_pair_derived_columns(editor_visible, categories_df)
        editor_baseline = editor_visible.copy()
        with st.form(f"{executive_editor_key}_batch_form", clear_on_submit=False):
            edited_detail = st.data_editor(
                editor_visible,
                use_container_width=True,
                hide_index=True,
                height=min(640, 130 + max(len(visible), 4) * 34),
                column_config={
                    "id": st.column_config.NumberColumn("ID", disabled=True, width="small"),
                    "txn_date": st.column_config.TextColumn("Date", disabled=True),
                    "account_name": st.column_config.TextColumn("Account", disabled=True),
                    "bank": st.column_config.TextColumn("Bank", disabled=True),
                    "account_number": st.column_config.TextColumn("Account number", disabled=True),
                    "currency": st.column_config.TextColumn("Currency", disabled=True, width="small"),
                    "amount": st.column_config.NumberColumn("Statement amount", format="%.2f", disabled=True),
                    "_display_amount_usd": st.column_config.NumberColumn("Report amount USD", format="%.2f", disabled=True),
                    "report_group": st.column_config.TextColumn("Reporting group", disabled=True),
                    _CATEGORY_PAIR_COLUMN: st.column_config.SelectboxColumn(
                        "Category / Subcategory",
                        options=pair_options,
                        required=False,
                        help="Choose a valid category/subcategory pair from Setup.",
                    ),
                    "reviewed": st.column_config.CheckboxColumn(
                        "Reviewed",
                        help="Untick to send the transaction back to Pending Review.",
                    ),
                    "original_description": st.column_config.TextColumn(
                        "Full statement description",
                        disabled=True,
                        width="large",
                    ),
                },
                key=executive_editor_key,
            )
            submit_executive_edits = st.form_submit_button(
                "Apply transaction detail edits",
                type="primary",
            )
        if submit_executive_edits:
            total_started = time.perf_counter()
            edited_detail = _apply_category_pair_values(edited_detail)
            changed_rows = _changed_transaction_editor_rows(
                editor_baseline,
                edited_detail,
                categories_df,
                ["category", "subcategory", "reviewed"],
            )
            if changed_rows.empty:
                st.info("No transaction detail changes to apply.")
            else:
                with st.status("Saving transaction edits...", expanded=True) as save_status:
                    try:
                        prepare_started = time.perf_counter()
                        save_cols = [
                            col
                            for col in ["id", "category", "subcategory", "reviewed"]
                            if col in changed_rows.columns
                        ]
                        save_df = changed_rows[save_cols].copy()
                        if "reviewed" not in save_df.columns:
                            save_df["reviewed"] = True
                        save_df["status"] = save_df["reviewed"].map(
                            lambda value: "reviewed" if bool(value) else "pending"
                        )
                        baseline = editor_baseline[
                            [
                                col
                                for col in ["id", "category", "subcategory", "reviewed"]
                                if col in editor_baseline.columns
                            ]
                        ].copy()
                        baseline = baseline.rename(columns={
                            "category": "_expected_category",
                            "subcategory": "_expected_subcategory",
                            "reviewed": "_expected_reviewed",
                        })
                        save_df = save_df.merge(baseline, on="id", how="left", validate="one_to_one")
                        _perf_log("executive_edit_prepare", prepare_started)

                        before_pairs = {
                            int(row["id"]): (
                                str(row.get("_expected_category", "") or "").strip(),
                                str(row.get("_expected_subcategory", "") or "").strip(),
                            )
                            for _, row in save_df.iterrows()
                        }
                        database_started = time.perf_counter()
                        count = update_database_rows(save_df)
                        _perf_log("executive_edit_database_update_and_commit", database_started)
                        if count != len(save_df):
                            raise RuntimeError(
                                f"Expected to save {len(save_df)} transaction(s), "
                                f"but the database confirmed {count}."
                            )
                        verification_started = time.perf_counter()
                        _verify_transaction_edit_save(save_df)
                        _perf_log("executive_edit_persistence_verification", verification_started)
                        moved_from_filter = any(
                            before_pairs[int(row["id"])]
                            != (
                                str(row.get("category", "") or "").strip(),
                                str(row.get("subcategory", "") or "").strip(),
                            )
                            for _, row in save_df.iterrows()
                        )
                        save_status.update(
                            label=(
                                f"Transaction edits saved successfully ({count}). "
                                "Refreshing the selected view..."
                            ),
                            state="complete",
                        )
                    except ConcurrentTransactionEditError as exc:
                        save_status.update(
                            label="Save stopped because the transaction changed elsewhere.",
                            state="error",
                        )
                        st.error(str(exc))
                    except Exception as exc:
                        save_status.update(label="Transaction edits were not saved.", state="error")
                        st.error(f"Could not save transaction edits: {exc}")
                    else:
                        if moved_from_filter:
                            message = (
                                f"Transaction edits saved successfully ({count}). The refreshed report is complete. "
                                "The updated transaction was removed from this view because it no longer matches "
                                "the selected filter."
                            )
                        else:
                            message = (
                                f"Transaction edits saved successfully ({count}). "
                                "The refreshed report is complete."
                            )
                        st.session_state["executive_detail_save_message"] = message
                        cache_started = time.perf_counter()
                        _clear_transaction_read_caches()
                        _perf_log("executive_edit_cache_invalidation", cache_started)
                        _perf_log("executive_edit_save_before_rerun", total_started)
                        st.rerun()
    export_sheets = _executive_selected_transactions_export_sheets(visible)
    st.download_button(
        "Download selected transactions Excel",
        data=dataframe_to_excel_bytes(export_sheets),
        file_name="executive_selected_transactions.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


def _largest_change_rows(frame, group_column, current_month, previous_month, ascending=False, limit=5):
    if frame.empty or group_column not in frame.columns:
        return []
    pivot = (
        frame.groupby([group_column, "month"])["expense_usd"]
        .sum()
        .unstack(fill_value=0)
    )
    current = pivot[current_month] if current_month in pivot.columns else 0
    previous = pivot[previous_month] if previous_month in pivot.columns else 0
    out = pd.DataFrame({
        group_column: pivot.index.astype(str),
        "current": current,
        "previous": previous,
    })
    out["change"] = out["current"] - out["previous"]
    out = out[out["change"].abs() > 0.005].copy()
    if out.empty:
        return []
    out = out.sort_values("change", ascending=ascending).head(limit)
    return out.to_dict("records")


def _format_analysis_bullets(title, rows, name_column, direction):
    if not rows:
        return f"**{title}**\n- No material movement found."
    bullets = [f"**{title}**"]
    for row in rows:
        change_text = _money(abs(row.get("change", 0)))
        current_text = _money(row.get("current", 0))
        previous_text = _money(row.get("previous", 0))
        bullets.append(
            f"- {row.get(name_column) or 'Unassigned'} {direction} by {change_text} "
            f"({previous_text} to {current_text})."
        )
    return "\n".join(bullets)


def _plain_ai_analysis_html(analysis):
    text = str(analysis or "").replace("**", "")
    lines = [escape(line) if line.strip() else "" for line in text.splitlines()]
    return "<div class=\"ai-analysis-box\">" + "<br>".join(lines) + "</div>"


def _default_reporting_group_prompt():
    return (
        "Prepare a professional financial analysis for the selected reporting group using the report data provided. "
        "Cover signed total, current month, previous month, main drivers, what to notice, and follow-up points. "
        "Use only the supplied report data and do not invent transactions or explanations."
    )


def _analysis_rows_as_text(frame, columns, max_rows=60):
    if frame is None or frame.empty:
        return "- No rows."
    output = []
    safe_columns = [column for column in columns if column in frame.columns]
    for _, row in frame.head(max_rows).iterrows():
        parts = []
        for column in safe_columns:
            value = row.get(column, "")
            if pd.isna(value):
                value = ""
            if isinstance(value, float):
                value = round(value, 2)
            parts.append(f"{column}: {value}")
        output.append("- " + " | ".join(parts))
    remaining = len(frame) - len(output)
    if remaining > 0:
        output.append(f"- {remaining} additional row(s) not shown in this context.")
    return "\n".join(output)


def _build_ai_report_data_context(
    group_expenses,
    category_totals,
    report_group,
    month_label,
    total,
    current_total,
    previous_total,
    status_delta,
):
    category_context = category_totals.copy()
    if not category_context.empty:
        category_context["_abs_sort"] = category_context["_signed_report_amount"].abs()
        category_context = category_context.sort_values("_abs_sort", ascending=False).drop(columns=["_abs_sort"])

    subcategory_context = (
        group_expenses.groupby(["category", "subcategory"], dropna=False)["_signed_report_amount"]
        .sum()
        .reset_index()
    )
    if not subcategory_context.empty:
        subcategory_context["subcategory"] = subcategory_context["subcategory"].fillna("").replace("", "No subcategory")
        subcategory_context["_abs_sort"] = subcategory_context["_signed_report_amount"].abs()
        subcategory_context = subcategory_context.sort_values("_abs_sort", ascending=False).drop(columns=["_abs_sort"])

    monthly_context = (
        group_expenses.groupby(["month", "category"], dropna=False)["_signed_report_amount"]
        .sum()
        .reset_index()
        .sort_values(["month", "_signed_report_amount"], ascending=[True, True])
    )

    transaction_context = group_expenses.copy()
    if not transaction_context.empty:
        transaction_context["_abs_sort"] = transaction_context["_signed_report_amount"].abs()
        transaction_context = transaction_context.sort_values("_abs_sort", ascending=False)
        transaction_context = transaction_context.rename(columns={
            "txn_date": "date",
            "full_statement_description": "description",
            "_signed_report_amount": "signed_amount_usd",
        })

    return (
        f"Reporting group: {report_group}\n"
        f"Report through: {month_label}\n"
        f"Signed total: {_money(total)}\n"
        f"Current month: {_money(current_total)}\n"
        f"Previous month: {_money(previous_total)}\n"
        f"Change from previous month: {_money(status_delta)}\n\n"
        "Category totals:\n"
        + _analysis_rows_as_text(category_context, ["category", "_signed_report_amount"], max_rows=80)
        + "\n\nSubcategory totals:\n"
        + _analysis_rows_as_text(subcategory_context, ["category", "subcategory", "_signed_report_amount"], max_rows=120)
        + "\n\nMonthly category totals:\n"
        + _analysis_rows_as_text(monthly_context, ["month", "category", "_signed_report_amount"], max_rows=160)
        + "\n\nLargest transaction lines by absolute signed USD amount:\n"
        + _analysis_rows_as_text(
            transaction_context,
            ["date", "signed_amount_usd", "currency", "category", "subcategory", "description", "account"],
            max_rows=120,
        )
    )


def _extract_openai_response_text(payload):
    if not isinstance(payload, dict):
        return ""
    direct_text = payload.get("output_text")
    if isinstance(direct_text, str) and direct_text.strip():
        return direct_text.strip()
    text_parts = []
    for item in payload.get("output", []) or []:
        if not isinstance(item, dict):
            continue
        for content in item.get("content", []) or []:
            if not isinstance(content, dict):
                continue
            text = content.get("text")
            if isinstance(text, str) and text.strip():
                text_parts.append(text.strip())
    return "\n\n".join(text_parts).strip()


class _AIServiceError(Exception):
    pass


def _openai_retry_delay(exc):
    retry_after = exc.headers.get("Retry-After") if getattr(exc, "headers", None) else None
    try:
        delay = float(retry_after)
    except (TypeError, ValueError):
        delay = float(os.environ.get("OPENAI_RETRY_SECONDS", "2") or "2")
    return max(0.0, min(delay, float(os.environ.get("OPENAI_MAX_RETRY_SECONDS", "5") or "5")))


def _openai_http_error_message(exc):
    detail = ""
    try:
        payload = json.loads(exc.read().decode("utf-8"))
        error = payload.get("error", {}) if isinstance(payload, dict) else {}
        message = str(error.get("message") or "").strip()
        code = str(error.get("code") or error.get("type") or "").strip()
        if message and code:
            detail = f" ({code}: {message})"
        elif message:
            detail = f" ({message})"
        elif code:
            detail = f" ({code})"
    except Exception:
        detail = ""
    if exc.code == 429:
        guidance = " This usually means the server-side AI API key has hit a rate, token, quota, or billing limit."
    elif exc.code in {401, 403}:
        guidance = " This usually means the server-side AI API key or model access is not accepted."
    else:
        guidance = ""
    return (
        f"The approved custom AI prompt could not be generated right now. HTTP {exc.code}{detail}."
        f"{guidance} No alternative AI analysis was generated."
    )


def _request_openai_report(prompt_text, data_context, model, max_output_tokens, timeout_seconds, api_key):
    body = {
        "model": model,
        "instructions": str(prompt_text or "").strip(),
        "input": str(data_context or "").strip(),
        "max_output_tokens": max_output_tokens,
    }
    request = urllib.request.Request(
        "https://api.openai.com/v1/responses",
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
        payload = json.loads(response.read().decode("utf-8"))
    text = _extract_openai_response_text(payload)
    if text:
        return text
    raise _AIServiceError("The AI service responded, but no report text was returned.")


@st.cache_data(show_spinner=False, ttl=3600, max_entries=64)
def _request_openai_report_cached(prompt_text, data_context, model, max_output_tokens, timeout_seconds, api_key):
    return _request_openai_report(prompt_text, data_context, model, max_output_tokens, timeout_seconds, api_key)


def _run_custom_ai_prompt(prompt_text, data_context):
    api_key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not api_key:
        return (
            "Custom AI prompt is saved for this reporting group, but the AI service is not configured on the server. "
            "Set OPENAI_API_KEY on Render to generate the report using Areti's prompt only."
        )

    model = os.environ.get("OPENAI_MODEL", "gpt-5.2").strip() or "gpt-5.2"
    max_output_tokens = int(os.environ.get("OPENAI_MAX_OUTPUT_TOKENS", "2200") or "2200")
    timeout_seconds = int(os.environ.get("OPENAI_TIMEOUT_SECONDS", "45") or "45")
    try:
        return _request_openai_report_cached(
            str(prompt_text or "").strip(),
            str(data_context or "").strip(),
            model,
            max_output_tokens,
            timeout_seconds,
            api_key,
        )
    except urllib.error.HTTPError as exc:
        if exc.code == 429:
            time.sleep(_openai_retry_delay(exc))
            try:
                return _request_openai_report(
                    str(prompt_text or "").strip(),
                    str(data_context or "").strip(),
                    model,
                    max_output_tokens,
                    timeout_seconds,
                    api_key,
            )
            except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError, ValueError, _AIServiceError) as retry_exc:
                if isinstance(retry_exc, urllib.error.HTTPError):
                    return _openai_http_error_message(retry_exc)
                return (
                    f"The approved custom AI prompt could not be generated right now: {type(retry_exc).__name__}. "
                    "No alternative AI analysis was generated."
                )
        return _openai_http_error_message(exc)
    except (urllib.error.URLError, TimeoutError, ValueError, _AIServiceError) as exc:
        return (
            f"The approved custom AI prompt could not be generated right now: {type(exc).__name__}. "
            "No alternative AI analysis was generated."
        )


def _build_family_analysis(expenses, months, month_labels, custom_prompt=""):
    family = expenses[
        expenses["report_group"].fillna("").astype(str).str.strip().str.casefold().eq("1-family")
    ].copy()
    if family.empty:
        return "", pd.DataFrame()

    current_month = months[-1] if months else family["month"].max()
    previous_month = months[-2] if len(months) > 1 else None
    total = float(family["expense_usd"].sum())
    current_total = float(family.loc[family["month"] == current_month, "expense_usd"].sum())
    previous_total = (
        float(family.loc[family["month"] == previous_month, "expense_usd"].sum())
        if previous_month is not None
        else 0.0
    )
    change = current_total - previous_total
    trend_text = "increased" if change > 0.005 else "decreased" if change < -0.005 else "stayed broadly stable"

    category_totals = (
        family.groupby("category")["expense_usd"]
        .sum()
        .sort_values(ascending=False)
        .reset_index()
    )
    category_totals["share"] = category_totals["expense_usd"].apply(
        lambda value: (float(value) / total * 100) if total else 0.0
    )
    top_categories = category_totals.head(5).copy()

    subcategory_totals = (
        family.groupby(["category", "subcategory"], dropna=False)["expense_usd"]
        .sum()
        .sort_values(ascending=False)
        .reset_index()
        .head(8)
    )
    subcategory_totals["subcategory"] = subcategory_totals["subcategory"].fillna("").replace("", "No subcategory")

    increase_rows = _largest_change_rows(family, "category", current_month, previous_month, ascending=False, limit=5)
    decrease_rows = _largest_change_rows(family, "category", current_month, previous_month, ascending=True, limit=5)

    top_category_lines = [
        f"- {row['category'] or 'Unassigned'}: {_money(row['expense_usd'])} ({_percent(row['share'])} of 1-family)."
        for _, row in top_categories.iterrows()
    ] or ["- No category totals available."]

    subcategory_lines = [
        f"- {row['category'] or 'Unassigned'} / {row['subcategory']}: {_money(row['expense_usd'])}."
        for _, row in subcategory_totals.iterrows()
    ] or ["- No subcategory totals available."]

    cut_focus = top_categories.head(3)["category"].dropna().astype(str).tolist()
    cut_lines = [
        f"- Start with {', '.join(cut_focus)} because these are the largest 1-family cost drivers."
        if cut_focus else "- Start with the largest visible categories once more data is reviewed.",
        "- Review categories that increased in the latest month first; these are the quickest places to find unusual one-off items.",
        "- For recurring or repeated costs, set a monthly reference amount and investigate anything above it.",
        "- Check the transaction detail under each subcategory before cutting, so essential family costs are separated from discretionary costs.",
    ]
    prompt_text = str(custom_prompt or "").strip()
    if prompt_text:
        cut_lines.insert(
            0,
            "- Apply the written family context and spending-habit instructions when judging what is unusual, recurring, or discretionary.",
        )

    sections = [
        (
            f"**1-family analysis through {month_labels.get(current_month, str(current_month))}**\n"
            f"- Total active 1-family report rows in this report window: {_money(total)}.\n"
            f"- Current month: {_money(current_total)}; previous month: {_money(previous_total)}.\n"
            f"- Overall movement: 1-family {trend_text} by {_money(abs(change))}."
        ),
        "**Main cost drivers**\n" + "\n".join(top_category_lines),
        _format_analysis_bullets("What is going up", increase_rows, "category", "increased"),
        _format_analysis_bullets("What is going down", decrease_rows, "category", "decreased"),
        "**What to notice**\n" + "\n".join(subcategory_lines),
        "**How to cut down**\n" + "\n".join(cut_lines),
    ]
    analysis = "\n\n".join(sections)

    export_rows = []
    for _, row in category_totals.iterrows():
        export_rows.append({
            "Section": "Cost driver",
            "Category": row["category"],
            "Subcategory": "",
            "Amount": float(row["expense_usd"]),
            "Share": float(row["share"]),
            "Comment": f"{row['category']} represents {_percent(row['share'])} of 1-family.",
        })
    for row in increase_rows:
        export_rows.append({
            "Section": "Going up",
            "Category": row.get("category", ""),
            "Subcategory": "",
            "Amount": float(row.get("change", 0)),
            "Share": "",
            "Comment": f"Increased from {_money(row.get('previous', 0))} to {_money(row.get('current', 0))}.",
        })
    for row in decrease_rows:
        export_rows.append({
            "Section": "Going down",
            "Category": row.get("category", ""),
            "Subcategory": "",
            "Amount": float(row.get("change", 0)),
            "Share": "",
            "Comment": f"Decreased from {_money(row.get('previous', 0))} to {_money(row.get('current', 0))}.",
        })
    if prompt_text:
        export_rows.append({
            "Section": "Areti instructions",
            "Category": "",
            "Subcategory": "",
            "Amount": "",
            "Share": "",
            "Comment": "Custom instructions from Setup were applied to the analysis. The raw prompt text is not printed in the report.",
        })

    return analysis, pd.DataFrame(export_rows)


def _render_family_analysis_button(expenses, months, month_labels):
    has_family = (
        not expenses.empty
        and "report_group" in expenses.columns
        and expenses["report_group"].fillna("").astype(str).str.strip().str.casefold().eq("1-family").any()
    )
    if not has_family:
        return

    st.markdown("### 1-family AI analysis")
    custom_prompt = _report_group_prompt_lookup().get("1-family", "")
    if st.button("Generate 1-family AI analysis", type="primary", key="generate_family_analysis"):
        st.session_state["family_analysis_generated"] = True

    if st.session_state.get("family_analysis_generated"):
        analysis, export_df = _build_family_analysis(expenses, months, month_labels, custom_prompt)
        if analysis:
            st.markdown(analysis)
            if not export_df.empty:
                st.download_button(
                    "Download 1-family AI analysis Excel",
                    data=dataframe_to_excel_bytes({"1-family analysis": export_df}),
                    file_name="1_family_analysis.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                )
        else:
            st.info("No reviewed 1-family expenses are available for this report period.")


def _build_reporting_group_analysis(expenses, months, month_labels, report_group, custom_prompt=""):
    group_expenses = expenses[
        expenses["report_group"].fillna("").astype(str).str.strip().eq(str(report_group or "").strip())
    ].copy()
    if group_expenses.empty:
        return ""
    prompt_text = str(custom_prompt or "").strip()
    if not prompt_text:
        return (
            "No approved custom AI prompt is saved for this reporting group. "
            "No alternative AI analysis was generated."
        )

    group_expenses["_signed_report_amount"] = _executive_signed_amount_series(group_expenses).values
    current_month = months[-1] if months else group_expenses["month"].max()
    previous_month = months[-2] if len(months) > 1 else None
    total = float(group_expenses["_signed_report_amount"].sum())
    current_total = float(group_expenses.loc[group_expenses["month"] == current_month, "_signed_report_amount"].sum())
    previous_total = (
        float(group_expenses.loc[group_expenses["month"] == previous_month, "_signed_report_amount"].sum())
        if previous_month is not None
        else 0.0
    )
    change = current_total - previous_total
    status_delta = _executive_status_delta(current_total, previous_total)
    trend_text = "increased" if status_delta > 0.005 else "decreased" if status_delta < -0.005 else "stayed broadly stable"

    category_totals = (
        group_expenses.groupby("category")["_signed_report_amount"]
        .sum()
        .reset_index()
    )
    category_totals["_abs_sort"] = category_totals["_signed_report_amount"].abs()
    category_totals = category_totals.sort_values("_abs_sort", ascending=False).drop(columns=["_abs_sort"])
    top_categories = category_totals.head(5).copy()

    category_lines = [
        f"- {row['category'] or 'Unassigned'}: {_money(row['_signed_report_amount'])}."
        for _, row in top_categories.iterrows()
    ] or ["- No category totals available."]

    return _build_custom_reporting_group_analysis(
        group_expenses,
        category_totals,
        report_group,
        month_labels.get(current_month, str(current_month)),
        total,
        current_total,
        previous_total,
        status_delta,
        trend_text,
        prompt_text,
    )


def _build_custom_reporting_group_analysis(
    group_expenses,
    category_totals,
    report_group,
    month_label,
    total,
    current_total,
    previous_total,
    status_delta,
    trend_text,
    prompt_text,
):
    data_context = _build_ai_report_data_context(
        group_expenses,
        category_totals,
        report_group,
        month_label,
        total,
        current_total,
        previous_total,
        status_delta,
    )
    return _run_custom_ai_prompt(prompt_text, data_context)


def _reporting_group_analysis_cache_key(expenses, months, report_group, prompt_text):
    group_key = str(report_group or "").strip()
    group_expenses = expenses[
        expenses["report_group"].fillna("").astype(str).str.strip().eq(group_key)
    ].copy()
    data_signature = _dataframe_signature(
        group_expenses,
        [
            "id",
            "txn_date",
            "month",
            "report_group",
            "category",
            "subcategory",
            "report_amount",
            "expense_usd",
            "amount_usd",
            "amount",
            "currency",
            "full_statement_description",
            "account",
            "account_name",
        ],
    )
    prompt_signature = hashlib.sha256(str(prompt_text or "").encode("utf-8")).hexdigest()
    months_signature = ",".join(str(month) for month in months)
    return "|".join([group_key.casefold(), months_signature, data_signature, prompt_signature])


def _get_reporting_group_analysis(expenses, months, month_labels, report_group, custom_prompt=""):
    started = time.perf_counter()
    cache = st.session_state.setdefault("third_report_ai_cache", {})
    cache_key = _reporting_group_analysis_cache_key(expenses, months, report_group, custom_prompt)
    if cache_key in cache:
        _perf_log("third_link.ai_report.cache_hit", started)
        return cache[cache_key], True
    analysis = _build_reporting_group_analysis(
        expenses,
        months,
        month_labels,
        report_group,
        custom_prompt,
    )
    analysis_text = str(analysis or "")
    if not analysis_text.startswith("The AI service could not generate the report right now") and not analysis_text.startswith("The approved custom AI prompt could not be generated"):
        cache[cache_key] = analysis
        # Keep the per-session cache small; entries are keyed by data+prompt, so stale reports are not reused.
        if len(cache) > 12:
            for old_key in list(cache.keys())[:-12]:
                cache.pop(old_key, None)
    _perf_log("third_link.ai_report.generated", started)
    return analysis, False


def _render_executive_drilldown(
    expenses,
    months,
    month_labels,
    visible_report_groups=None,
    categories_df=None,
    show_all_months=False,
    read_only=False,
    ai_prompts=None,
    show_zero_explanations=True,
    inline_hierarchy=False,
    show_group_total=False,
):
    visible_report_groups = visible_report_groups or []
    selected_group = _valid_executive_selection(
        expenses,
        "report_group",
        st.session_state.get("executive_group"),
        extra_values=visible_report_groups,
    )
    group_category_options = _executive_category_options(categories_df, selected_group) if selected_group else []
    selected_category = _valid_executive_selection(
        expenses[expenses["report_group"].fillna("").astype(str).str.strip() == selected_group] if selected_group else expenses.iloc[0:0],
        "category",
        st.session_state.get("executive_category"),
        extra_values=group_category_options,
    )
    category_subcategory_options = (
        _executive_subcategory_options(categories_df, selected_category) if selected_category else []
    )
    selected_subcategory = _valid_executive_selection(
        expenses[
            (expenses["report_group"].fillna("").astype(str).str.strip() == selected_group)
            & (expenses["category"].fillna("").astype(str).str.strip() == selected_category)
        ] if selected_group and selected_category else expenses.iloc[0:0],
        "subcategory",
        st.session_state.get("executive_subcategory"),
        extra_values=category_subcategory_options,
    )
    if selected_group != st.session_state.get("executive_group"):
        st.session_state.pop("executive_group", None)
    if selected_category != st.session_state.get("executive_category"):
        st.session_state.pop("executive_category", None)
    if selected_subcategory != st.session_state.get("executive_subcategory"):
        st.session_state.pop("executive_subcategory", None)

    group_rows = _executive_level_rows(
        expenses,
        "report_group",
        months,
        extra_labels=visible_report_groups,
    )
    if show_group_total:
        total_row = _executive_total_row(group_rows, months)
        if total_row is not None:
            group_rows = [*group_rows, total_row]

    if not inline_hierarchy:
        if selected_group:
            trail = f"Reporting group: {selected_group}"
            if selected_category:
                trail += f" / Category: {selected_category}"
            if selected_subcategory is not None:
                trail += f" / Subcategory: {selected_subcategory or 'No subcategory'}"
            st.markdown(f"<div class=\"drill-breadcrumb\">{escape(trail)}</div>", unsafe_allow_html=True)

        _render_executive_click_rows(
            "1. Reporting Groups",
            group_rows,
            "group",
            months,
            month_labels,
            show_all_months=show_all_months,
            ai_prompts=ai_prompts,
            show_zero_explanations=show_zero_explanations,
        )
        if not selected_group:
            return
        group_expenses = expenses[
            expenses["report_group"].fillna("").astype(str).str.strip() == selected_group
        ].copy()
        category_rows = _executive_level_rows(
            group_expenses,
            "category",
            months,
            extra_labels=group_category_options,
        )
        _render_executive_click_rows(
            "2. Categories",
            category_rows,
            "category",
            months,
            month_labels,
            show_all_months=show_all_months,
            show_zero_explanations=show_zero_explanations,
        )
        if not selected_category:
            return
        category_expenses = group_expenses[
            group_expenses["category"].fillna("").astype(str).str.strip() == selected_category
        ].copy()
        subcategory_rows = _executive_level_rows(
            category_expenses,
            "subcategory",
            months,
            extra_labels=category_subcategory_options,
        )
        _render_executive_click_rows(
            "3. Subcategories",
            subcategory_rows,
            "subcategory",
            months,
            month_labels,
            show_all_months=show_all_months,
            show_zero_explanations=show_zero_explanations,
        )
        if selected_subcategory is None:
            return
        _render_executive_transactions(
            expenses,
            selected_group,
            selected_category,
            selected_subcategory,
            months=months,
            read_only=read_only,
            categories_df=categories_df,
        )
        return

    def render_selected_subcategory(group, category, subcategory):
        trail = (
            f"Reporting group: {group} / Category: {category} / "
            f"Subcategory: {subcategory or 'No subcategory'}"
        )
        st.markdown(f"<div class=\"drill-inline-context\">{escape(trail)}</div>", unsafe_allow_html=True)
        _render_executive_transactions(
            expenses,
            group,
            category,
            subcategory,
            months=months,
            read_only=read_only,
            categories_df=categories_df,
        )

    def render_selected_category(group, group_expenses, category):
        trail = f"Reporting group: {group} / Category: {category}"
        st.markdown(f"<div class=\"drill-inline-context\">{escape(trail)}</div>", unsafe_allow_html=True)
        category_expenses = group_expenses[
            group_expenses["category"].fillna("").astype(str).str.strip() == category
        ].copy()
        subcategory_options = _executive_subcategory_options(categories_df, category)
        subcategory_rows = _executive_level_rows(
            category_expenses,
            "subcategory",
            months,
            extra_labels=subcategory_options,
        )
        _render_executive_click_rows(
            "3. Subcategories",
            subcategory_rows,
            "subcategory",
            months,
            month_labels,
            show_all_months=show_all_months,
            show_zero_explanations=show_zero_explanations,
            render_child=lambda subcategory: render_selected_subcategory(group, category, subcategory),
            inline_selection=True,
        )

    def render_selected_group(group):
        st.markdown(
            f"<div class=\"drill-inline-context\">Reporting group: {escape(group)}</div>",
            unsafe_allow_html=True,
        )
        group_expenses = expenses[
            expenses["report_group"].fillna("").astype(str).str.strip() == group
        ].copy()
        category_options = _executive_category_options(categories_df, group)
        category_rows = _executive_level_rows(
            group_expenses,
            "category",
            months,
            extra_labels=category_options,
        )
        _render_executive_click_rows(
            "2. Categories",
            category_rows,
            "category",
            months,
            month_labels,
            show_all_months=show_all_months,
            show_zero_explanations=show_zero_explanations,
            render_child=lambda category: render_selected_category(group, group_expenses, category),
            inline_selection=True,
        )

    _render_executive_click_rows(
        "1. Reporting Groups",
        group_rows,
        "group",
        months,
        month_labels,
        show_all_months=show_all_months,
        ai_prompts=ai_prompts,
        show_zero_explanations=show_zero_explanations,
        render_child=render_selected_group,
        inline_selection=True,
    )


def _render_executive_completeness_check(
    filtered_transactions,
    categories_df,
    visible_report_groups,
    active_database_rows=None,
    executive_row_count=None,
):
    reviewed_checked = len(filtered_transactions)
    active_database_count = len(active_database_rows) if active_database_rows is not None else reviewed_checked
    pending_or_unreviewed = 0
    if active_database_rows is not None and not active_database_rows.empty:
        status_key = (
            active_database_rows.get("status", pd.Series("", index=active_database_rows.index))
            .fillna("")
            .astype(str)
            .str.strip()
            .str.casefold()
        )
        reviewed_raw = active_database_rows.get(
            "reviewed",
            pd.Series(False, index=active_database_rows.index),
        ).fillna(False)
        if pd.api.types.is_numeric_dtype(reviewed_raw):
            reviewed_flag = pd.to_numeric(reviewed_raw, errors="coerce").fillna(0).astype(int).eq(1)
        elif pd.api.types.is_bool_dtype(reviewed_raw):
            reviewed_flag = reviewed_raw.astype(bool)
        else:
            reviewed_flag = reviewed_raw.astype(str).str.strip().str.casefold().isin(["1", "true", "yes", "reviewed"])
        pending_or_unreviewed = int((~reviewed_flag | status_key.ne("reviewed")).sum())
    executive_row_count = active_database_count if executive_row_count is None else int(executive_row_count)
    count_gap = active_database_count - executive_row_count
    row_count_frame = _executive_row_count_verification_frame(
        active_database_rows,
        categories_df,
        visible_report_groups,
    )
    outside_view_rows = (
        int(row_count_frame.loc[~row_count_frame["In current Executive view"], "Rows"].sum())
        if not row_count_frame.empty
        else 0
    )
    setup_issue_frame = (
        row_count_frame[~row_count_frame["Setup check"].isin(["OK", "No subcategory"])].copy()
        if not row_count_frame.empty
        else pd.DataFrame()
    )
    no_subcategory_rows = (
        int(row_count_frame.loc[row_count_frame["Setup check"].eq("No subcategory"), "Rows"].sum())
        if not row_count_frame.empty
        else 0
    )
    setup_groups_count = 0
    setup_categories_count = 0
    setup_subcategories_count = 0
    if categories_df is not None and not categories_df.empty:
        if "report_group" in categories_df.columns:
            setup_groups_count = len(_ordered_text_values(categories_df["report_group"].fillna("").astype(str).tolist()))
        if "category" in categories_df.columns:
            setup_categories_count = len(_ordered_text_values(categories_df["category"].fillna("").astype(str).tolist()))
        if "subcategory" in categories_df.columns:
            setup_subcategories_count = len(_ordered_text_values(categories_df["subcategory"].fillna("").astype(str).tolist()))
    render_summary_strip([
        ("Database rows", active_database_count),
        ("Executive report rows", executive_row_count),
        ("Difference", count_gap),
        ("Pending / unreviewed", pending_or_unreviewed),
        ("Setup groups", setup_groups_count),
        ("Setup categories", setup_categories_count),
        ("Setup subcategories", setup_subcategories_count),
    ])

    if count_gap or outside_view_rows or not setup_issue_frame.empty:
        st.warning(
            "Row-count verification needs review. Use the table below to compare the active database rows "
            "against the Executive view by reporting group, category, and subcategory."
        )
    elif no_subcategory_rows:
        st.info(
            f"All active rows are counted. {no_subcategory_rows} row(s) have no subcategory and are shown "
            "as 'No subcategory' so they cannot disappear silently."
        )
    else:
        st.success("Row-count verification is balanced for the current period.")

    if not row_count_frame.empty:
        with st.expander("Row-count verification by reporting group / category / subcategory", expanded=True):
            st.caption(
                "This is the verifiable count table: export the backup Excel, then compare the row counts "
                "by reporting group, category, and subcategory. Pending and reviewed active rows are included."
            )
            st.dataframe(row_count_frame, use_container_width=True, hide_index=True)
    if not setup_issue_frame.empty:
        with st.expander("Category / subcategory combinations needing correction", expanded=True):
            st.caption(
                "These combinations are not present in the Expense categories setup. Correct them in Database "
                "or Pending Review so the report can place them cleanly."
            )
            st.dataframe(setup_issue_frame, use_container_width=True, hide_index=True)


def _income_charity_target_message(percentage):
    if percentage is None:
        return None
    percentage = float(percentage)
    if abs(percentage - 10.0) <= 1e-9:
        return "Charity is meeting the Family’s target of 10%."
    if percentage > 10.0:
        return "Charity is exceeding the Family’s target of 10%."
    return "Charity falls below the Family’s target of 10%."


def _render_income_charity_transactions(transaction_rows):
    transaction_rows = transaction_rows.copy()
    transaction_rows["txn_date"] = pd.to_datetime(
        transaction_rows["txn_date"], errors="coerce"
    ).dt.strftime("%Y-%m-%d")
    transaction_rows["Amount USD"] = _executive_signed_amount_series(transaction_rows).values
    transaction_rows = transaction_rows.rename(columns={
        "txn_date": "Date",
        "amount": "Statement amount",
        "currency": "Currency",
        "original_description": "Full statement description",
        "category": "Category",
        "subcategory": "Subcategory",
        "report_group": "Reporting group",
        "account_name": "Account",
        "id": "ID",
    })
    detail_columns = [
        "Date",
        "Amount USD",
        "Statement amount",
        "Currency",
        "Category",
        "Subcategory",
        "Reporting group",
        "Full statement description",
        "Account",
        "ID",
    ]
    st.markdown("#### Transactions")
    st.dataframe(
        transaction_rows[[column for column in detail_columns if column in transaction_rows.columns]],
        use_container_width=True,
        hide_index=True,
    )


def _render_income_charity_section(report_rows, months, month_labels, show_all_months=False):
    from reporting import income_charity_month_values, income_charity_percentage

    scoped, monthly, cumulative = income_charity_month_values(report_rows, months)
    income_total = float(sum(monthly["Income"].values()))
    charity_total = float(sum(monthly["Charity"].values()))
    charity_income_pct = income_charity_percentage(income_total, charity_total)
    period_rows = scoped[
        scoped.get("month", pd.Series(index=scoped.index, dtype=object)).isin(months)
    ].copy()
    denominator = abs(income_total) + abs(charity_total)
    summary_rows = []
    for row_type in ["Income", "Charity"]:
        metrics = _executive_metric_values_from_month_values(
            monthly[row_type],
            months,
            denominator,
        )
        metrics["label"] = row_type
        metrics["value"] = row_type
        summary_rows.append(metrics)

    if st.session_state.get("executive_income_charity") not in {None, "Income", "Charity"}:
        _clear_executive_selections(
            "executive_income_charity",
            "executive_income_charity_category",
            "executive_income_charity_subcategory",
        )

    def render_selected_subcategory(row_type, category, category_rows, subcategory):
        trail = (
            f"{row_type} / Category: {category} / "
            f"Subcategory: {subcategory or 'No subcategory'}"
        )
        st.markdown(f"<div class=\"drill-inline-context\">{escape(trail)}</div>", unsafe_allow_html=True)
        transaction_rows = category_rows[
            category_rows["subcategory"].fillna("").astype(str).str.strip().eq(subcategory)
        ].copy()
        _render_income_charity_transactions(transaction_rows)

    def render_selected_category(row_type, type_rows, category):
        st.markdown(
            f"<div class=\"drill-inline-context\">{escape(row_type)} / Category: {escape(category)}</div>",
            unsafe_allow_html=True,
        )
        category_rows = type_rows[
            type_rows["category"].fillna("").astype(str).str.strip().eq(category)
        ].copy()
        subcategory_rows = _executive_level_rows(category_rows, "subcategory", months)
        _render_executive_click_rows(
            "Subcategories",
            subcategory_rows,
            "income_charity_subcategory",
            months,
            month_labels,
            show_all_months=show_all_months,
            show_zero_explanations=False,
            selection_key="executive_income_charity_subcategory",
            clear_selection_keys=[],
            render_child=lambda subcategory: render_selected_subcategory(
                row_type, category, category_rows, subcategory
            ),
            inline_selection=True,
            income_context=row_type == "Income",
        )

    def render_selected_type(row_type):
        st.markdown(
            f"<div class=\"drill-inline-context\">{escape(row_type)}</div>",
            unsafe_allow_html=True,
        )
        close_col, _ = st.columns([1, 5])
        with close_col:
            st.button(
                f"Close {row_type} analysis",
                key="close_income_charity_analysis",
                on_click=_clear_executive_selections,
                args=(
                    "executive_income_charity",
                    "executive_income_charity_category",
                    "executive_income_charity_subcategory",
                ),
                use_container_width=True,
            )

        type_rows = period_rows[period_rows["income_charity_type"].eq(row_type)].copy()
        target_message = _income_charity_target_message(charity_income_pct)
        if target_message:
            st.caption(target_message)
        if type_rows.empty:
            st.info(f"No {row_type} transactions exist in the current report period.")
        else:
            category_rows = _executive_level_rows(type_rows, "category", months)
            _render_executive_click_rows(
                "Categories",
                category_rows,
                "income_charity_category",
                months,
                month_labels,
                show_all_months=show_all_months,
                show_zero_explanations=False,
                selection_key="executive_income_charity_category",
                clear_selection_keys=["executive_income_charity_subcategory"],
                render_child=lambda category: render_selected_category(row_type, type_rows, category),
                inline_selection=True,
                income_context=row_type == "Income",
            )

        render_summary_strip([
            ("Income total", _money(income_total)),
            ("Charity total", _money(charity_total)),
            ("Charity / Income", _percent(charity_income_pct)),
            (f"{row_type} transactions", len(type_rows)),
        ])
        st.caption(
            "This analysis is separate from the existing Reporting Group totals. "
            "Charity / Income compares the absolute Charity amount with the absolute Income amount."
        )

        monthly_frame = pd.DataFrame([{
            "Type": row_type,
            **{month_labels[month]: monthly[row_type][month] for month in months},
        }])
        cumulative_frame = pd.DataFrame([{
            "Type": row_type,
            **{month_labels[month]: cumulative[row_type][month] for month in months},
        }])
        st.markdown("#### Monthly values")
        st.dataframe(monthly_frame, use_container_width=True, hide_index=True)
        st.markdown("#### Cumulative from January")
        st.dataframe(cumulative_frame, use_container_width=True, hide_index=True)

    _render_executive_click_rows(
        "4. Income and Charity",
        summary_rows,
        "income_charity",
        months,
        month_labels,
        show_all_months=show_all_months,
        show_zero_explanations=False,
        clear_selection_keys=[
            "executive_income_charity_category",
            "executive_income_charity_subcategory",
        ],
        render_child=render_selected_type,
        inline_selection=True,
    )


def render_executive_report():
    from reporting import _prepare_report_data

    shared_report = _is_shared_executive_report_request()
    st.subheader("TB Family Office Executive Expenses Report" if shared_report else "Executive Summary")
    if not shared_report:
        saved_message = st.session_state.pop("executive_detail_save_message", "")
        if saved_message:
            st.success(saved_message)

    ensure_usd_backfilled()
    all_transactions = get_all_transactions()
    categories_df = get_categories(include_subcategories=True)
    if all_transactions.empty:
        st.info("No transactions are available for the executive report yet.")
        return

    all_transactions = active_financial_transactions(all_transactions)
    all_transactions["txn_date"] = pd.to_datetime(all_transactions.get("txn_date"), errors="coerce")
    active_transactions = all_transactions[all_transactions["txn_date"].notna()].copy()
    if active_transactions.empty:
        st.info("No active transactions are available for the executive report yet.")
        return

    date_values = pd.to_datetime(active_transactions.get("txn_date"), errors="coerce").dropna()
    default_end = date_values.max().date() if not date_values.empty else app_now().date()
    cutoff = get_configured_report_until(default_end)
    st.caption(f"Report until {cutoff.strftime('%d/%m/%Y')} (controlled in Setup)")
    cutoff_ts = pd.Timestamp(cutoff)
    filtered = active_transactions[active_transactions["txn_date"] <= cutoff_ts].copy()
    active_database_rows = filtered.copy()
    if filtered.empty:
        st.warning("No active transactions exist up to the selected date.")
        return

    _, expenses, _, _ = _prepare_report_data(
        filtered,
        categories_df,
        include_own_funds=True,
        include_all_valid=True,
    )
    all_report_groups = _executive_report_group_options(categories_df, expenses)
    show_income_charity = False
    if shared_report:
        visible_report_groups = _executive_default_visible_groups(all_report_groups)
        if not visible_report_groups:
            visible_report_groups = all_report_groups
        show_all_months = False
    else:
        report_options = ["Areti working report (all groups)", "TB Family Office report (selected groups)"]
        report_mode = st.segmented_control(
            "Executive report type",
            report_options,
            default=report_options[0],
            key="executive_report_type",
        ) or report_options[0]
        show_all_months = st.toggle(
            "Analytical",
            value=True,
            key="executive_show_all_months",
        )
        if report_mode.startswith("Areti"):
            visible_report_groups = all_report_groups
            show_income_charity = True
        else:
            visible_report_groups = _render_executive_group_visibility_control(all_report_groups)

    if visible_report_groups:
        expenses = expenses[
            expenses["report_group"].fillna("").astype(str).str.strip().isin(visible_report_groups)
        ].copy()
    if not shared_report:
        _render_executive_completeness_check(
            filtered,
            categories_df,
            visible_report_groups,
            active_database_rows=active_database_rows,
            executive_row_count=len(expenses),
        )

    if expenses.empty and not visible_report_groups:
        st.info("No active report rows match the selected period.")
        return

    current_month = cutoff_ts.to_period("M")
    month_window = _executive_month_window(current_month)
    month_labels = _executive_month_labels(month_window)
    if not shared_report:
        _render_family_analysis_button(expenses, month_window, month_labels)
    _render_executive_drilldown(
        expenses,
        month_window,
        month_labels,
        visible_report_groups,
        categories_df=categories_df,
        show_all_months=show_all_months,
        inline_hierarchy=True,
        show_group_total=not shared_report,
    )
    if show_income_charity:
        _render_income_charity_section(
            expenses,
            month_window,
            month_labels,
            show_all_months=show_all_months,
        )


def render_third_link_report():
    from reporting import _prepare_report_data

    total_started = time.perf_counter()
    st.markdown(
        """
        <div class="third-report-header">
            <h1 class="third-report-title">TB & NF Family Office Expenses Platform</h1>
            <div class="third-report-subtitle">
                (includes personal accounts, B-Projects Ltd, TB Tribute Ltd, Tengri INC & Woking Way LLC)
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    render_third_report_session_line()
    status_slot = st.empty()
    status_slot.info("Loading report data...")

    step_started = time.perf_counter()
    ensure_usd_backfilled()
    _perf_log("third_link.ensure_usd_backfilled", step_started)
    status_slot.info("Loading setup and transactions...")
    step_started = time.perf_counter()
    all_transactions = get_all_transactions()
    categories_df = get_categories(include_subcategories=True)
    _perf_log("third_link.load_setup_and_transactions", step_started)
    if all_transactions.empty:
        status_slot.empty()
        st.info("No transactions are available for this report yet.")
        _perf_log("third_link.total_empty", total_started)
        return

    status_slot.info("Preparing active transactions...")
    step_started = time.perf_counter()
    all_transactions = active_financial_transactions(all_transactions)
    all_transactions["txn_date"] = pd.to_datetime(all_transactions.get("txn_date"), errors="coerce")
    active_transactions = all_transactions[all_transactions["txn_date"].notna()].copy()
    _perf_log("third_link.prepare_active_transactions", step_started)
    if active_transactions.empty:
        status_slot.empty()
        st.info("No active transactions are available for this report yet.")
        _perf_log("third_link.total_no_active", total_started)
        return

    status_slot.info("Applying report date...")
    step_started = time.perf_counter()
    default_end = active_transactions["txn_date"].max().date()
    cutoff = get_configured_report_until(default_end)
    cutoff_ts = pd.Timestamp(cutoff)
    filtered = active_transactions[active_transactions["txn_date"] <= cutoff_ts].copy()
    _perf_log("third_link.apply_cutoff", step_started)
    if filtered.empty:
        status_slot.empty()
        st.warning("No active transactions exist up to the configured report date.")
        _perf_log("third_link.total_no_filtered", total_started)
        return

    status_slot.info("Preparing report calculations...")
    step_started = time.perf_counter()
    _, expenses, _, _ = _prepare_report_data(
        filtered,
        categories_df,
        include_own_funds=True,
        include_all_valid=True,
    )
    _perf_log("third_link.prepare_report_data", step_started)
    step_started = time.perf_counter()
    all_report_groups = _executive_report_group_options(categories_df, expenses)
    visible_report_groups = _third_link_default_visible_groups(all_report_groups)
    _perf_log("third_link.resolve_visible_groups", step_started)
    if not visible_report_groups:
        status_slot.empty()
        st.warning("No reporting groups are selected for the third link yet. Tick at least one group in Setup.")
        _perf_log("third_link.total_no_visible_groups", total_started)
        return

    status_slot.info("Filtering visible reporting groups...")
    step_started = time.perf_counter()
    expenses = expenses[
        expenses["report_group"].fillna("").astype(str).str.strip().isin(visible_report_groups)
    ].copy()
    _perf_log("third_link.filter_visible_groups", step_started)
    if expenses.empty:
        status_slot.empty()
        st.info("No active report rows match the third-link reporting groups.")
        _perf_log("third_link.total_no_expenses", total_started)
        return

    show_all_months = st.toggle("Analytical", value=False, key="third_report_analytical")
    st.markdown(f'<div class="third-report-note">Report until {cutoff.strftime("%d/%m/%Y")}</div>', unsafe_allow_html=True)

    current_month = cutoff_ts.to_period("M")
    month_window = _executive_month_window(current_month)
    month_labels = _executive_month_labels(month_window)
    ai_prompts = _report_group_prompt_lookup()
    selected_ai_group = st.session_state.get("third_report_ai_group")
    ai_requested = bool(st.session_state.pop("third_report_ai_requested", False))
    if selected_ai_group in visible_report_groups and expenses["report_group"].fillna("").astype(str).str.strip().eq(str(selected_ai_group).strip()).any():
        status_slot.empty()
        with st.expander(f"AI report: {selected_ai_group}", expanded=True):
            selected_prompt = ai_prompts.get(selected_ai_group, "")
            ai_cache_key = _reporting_group_analysis_cache_key(
                expenses,
                month_window,
                selected_ai_group,
                selected_prompt,
            )
            ai_cache = st.session_state.setdefault("third_report_ai_cache", {})
            last_ai_result = st.session_state.get("third_report_ai_last_result", {})
            analysis = ""
            if ai_requested:
                ai_status = st.empty()
                ai_status.info("Preparing AI report...")
                with st.spinner("Generating AI report..."):
                    analysis, _from_cache = _get_reporting_group_analysis(
                        expenses,
                        month_window,
                        month_labels,
                        selected_ai_group,
                        selected_prompt,
                    )
                ai_status.empty()
                # Keep the visible result across unrelated Streamlit reruns without triggering AI again.
                st.session_state["third_report_ai_last_result"] = {
                    "cache_key": ai_cache_key,
                    "analysis": analysis,
                }
            elif ai_cache_key in ai_cache:
                analysis = ai_cache[ai_cache_key]
            elif last_ai_result.get("cache_key") == ai_cache_key:
                analysis = last_ai_result.get("analysis", "")
            else:
                st.info("Click the AI button next to a reporting group to generate this analysis.")
            if analysis:
                st.markdown(_plain_ai_analysis_html(analysis), unsafe_allow_html=True)
                if str(selected_prompt or "").strip():
                    st.caption(
                        "Only the custom AI prompt from Setup is used for this analysis. "
                        "The raw prompt text is private and is not shown in the third report."
                    )
                else:
                    st.caption(
                        "Custom AI instructions are edited in Setup, in the 'AI prompt / instructions' "
                        "column for each reporting group. The default prompt uses signed total, current "
                        "month, previous month, main drivers, what to notice, and follow-up points."
                    )
    status_slot.info("Rendering reporting groups...")
    step_started = time.perf_counter()
    _render_executive_drilldown(
        expenses,
        month_window,
        month_labels,
        visible_report_groups,
        categories_df=categories_df,
        show_all_months=show_all_months,
        read_only=True,
        ai_prompts=ai_prompts,
        show_zero_explanations=False,
    )
    status_slot.empty()
    _perf_log("third_link.render_drilldown", step_started)
    _perf_log("third_link.total", total_started)


if is_third_link_report_request():
    render_third_link_report()
    st.stop()


if is_executive_report_request():
    render_app_header()
    render_session_line()
    render_executive_report()
    st.stop()


render_app_header()
render_session_line()
render_status_bar()
render_unsafe_storage_notice()
st.markdown('<div class="section-divider"></div>', unsafe_allow_html=True)

PAGES = [
    "Import",
    "Import History",
    "Pending Review",
    "Database",
    "Balances",
    "Memory",
    "Reports",
    "Setup",
]
page = st.segmented_control(
    "Section",
    PAGES,
    default=(
        "Setup"
        if missing_setup_items()
        else (
            st.query_params.get("page")
            if st.query_params.get("page") in PAGES
            else "Import"
        )
    ),
    key="main_navigation",
    label_visibility="collapsed",
) or "Import"
if st.query_params.get("page") != page:
    st.query_params["page"] = page


if page == "Import":
    st.subheader("Import Statement")

    categories = get_categories()
    accounts = get_accounts()
    rates = get_rates()
    missing_setup = missing_setup_items(categories, accounts, rates)

    if missing_setup:
        st.warning("Import is locked until setup is complete: " + ", ".join(missing_setup) + ".")
        render_setup_loader("import")
        st.stop()

    uploaded_statement = st.file_uploader(
        "Statement file",
        type=["csv", "xlsx", "xls", "pdf"],
        key="statement_upload",
    )

    if uploaded_statement:
        file_bytes = uploaded_statement.getvalue()
        statement_hash = build_statement_hash(file_bytes)
        if statement_already_imported(statement_hash):
            record_duplicate_statement_attempt(statement_hash)
            st.warning("This statement already exists. It was not imported again.")
            st.info(
                "This exact file is already in the import history, so the app blocks it to prevent duplicate "
                "transactions. If you corrected those transactions manually in the Database, you do not need to "
                "import the same file again; the next new statement can be imported normally."
            )
            if not statement_balance_exists(statement_hash):
                existing_account = get_statement_account(statement_hash)
                duplicate_balance = parse_statement_balance(file_bytes, uploaded_statement.name)
                if balance_has_values(duplicate_balance):
                    save_statement_balance(
                        statement_hash,
                        uploaded_statement.name,
                        duplicate_balance,
                        existing_account,
                    )
                    st.info("The missing balance summary was added to the Balances page.")
                    st.cache_data.clear()
            st.stop()

        balance_info = parse_statement_balance(file_bytes, uploaded_statement.name)
        labels, lookup = account_options(accounts)
        default_account_index = guess_account_index(file_bytes, uploaded_statement.name, accounts, labels, balance_info)
        selected_label = st.selectbox("Account", labels, index=default_account_index)
        selected_account = lookup[selected_label]
        st.caption("Check this account carefully before importing. It controls the company/name, bank, account number, currency, rate type, and USD conversion.")
        statement_currency = str(balance_info.get("currency") or "").strip().upper()
        selected_currency = str(selected_account.get("currency", "") or "").strip().upper()
        if statement_currency and selected_currency and statement_currency != selected_currency:
            st.warning(
                f"The statement appears to be {statement_currency}, but the selected account is {selected_currency}. "
                "Please change the Account dropdown before importing if this is not correct."
            )
        statement_account_digits = re.sub(r"\D", "", str(balance_info.get("account_number", "")))
        selected_account_digits = re.sub(r"\D", "", str(selected_account.get("account_number", "")))
        if (
            statement_account_digits
            and selected_account_digits
            and not (
                statement_account_digits in selected_account_digits
                or selected_account_digits in statement_account_digits
                or statement_account_digits[-4:] == selected_account_digits[-4:]
            )
        ):
            st.warning(
                "The statement account number does not appear to match the selected account. "
                "Please verify the Account dropdown before importing."
            )
        if is_amex_cardholder_statement(file_bytes, uploaded_statement.name):
            st.info(
                "AMEX cardholder CSV detected. Use the AMEX parent account; the app will append "
                "the cardholder name from the CSV to each imported AMEX account number."
            )

        try:
            progress_slot = st.empty()
            progress_slot.markdown(
                '<div class="import-progress"><span class="import-runner">&#x1F3C3;</span>'
                '<span>Processing statement. Please wait until the preview appears.</span></div>',
                unsafe_allow_html=True,
            )
            parsed = parse_statement(file_bytes, uploaded_statement.name)
            parse_diagnostics = dict(getattr(parsed, "attrs", {}).get("parse_diagnostics", {}) or {})
            parsed = apply_account_and_rates(parsed, selected_account)
            parsed = flag_duplicates(parsed)
            classified = classify_statement_rows(parsed, get_memory())
            progress_slot.empty()

            st.success(f"Prepared {len(classified)} transactions for review.")

            duplicate_lines = int(classified["dup_flag"].fillna(False).astype(bool).sum()) if "dup_flag" in classified else 0
            new_import_lines = max(len(classified) - duplicate_lines, 0)
            c1, c2, c3, c4, c5 = st.columns(5)
            c1.metric("Rows", len(classified))
            c2.metric("Exact matches", int((classified["match_type"] == "exact").sum()))
            c3.metric("Similar matches", int((classified["match_type"] == "similar").sum()))
            c4.metric("Needs review", int((classified["match_type"].isin(["new", "suggestion", "rule"])).sum()))
            c5.metric("Duplicate lines", duplicate_lines)

            if parse_diagnostics:
                pending_rows = int(parse_diagnostics.get("pending_rows", 0) or 0)
                reverted_rows = int(parse_diagnostics.get("reverted_rows", 0) or 0)
                st.info(
                    "Revolut import check: "
                    f"{len(classified)} completed transaction line(s) prepared, "
                    f"{new_import_lines} new line(s), "
                    f"{duplicate_lines} duplicate overlap line(s), "
                    f"{pending_rows} pending line(s) held out, "
                    f"{reverted_rows} reverted/cancelled line(s) skipped."
                )

            if duplicate_lines:
                st.warning(
                    f"{duplicate_lines} duplicate transaction line(s) were detected from existing/overlapping "
                    "statements and will be skipped when importing."
                )
                duplicate_cols = [
                    "Date",
                    "Description",
                    "Amount",
                    "currency",
                    "amount_usd",
                    "account_name",
                    "account_number",
                    "duplicate_reason",
                    "duplicate_source_statement",
                    "duplicate_source_id",
                ]
                duplicate_preview = classified[classified["dup_flag"].fillna(False).astype(bool)]
                with st.expander("View duplicate transaction lines", expanded=True):
                    st.dataframe(
                        duplicate_preview[[col for col in duplicate_cols if col in duplicate_preview.columns]],
                        use_container_width=True,
                        hide_index=True,
                    )

            row_currencies = []
            if "currency" in classified.columns:
                row_currencies = sorted(
                    value for value in classified["currency"].fillna("").astype(str).str.upper().unique() if value
                )
            if len(row_currencies) > 1:
                st.info(
                    "Multi-currency statement detected: "
                    + ", ".join(row_currencies)
                    + ". Each transaction will use the currency shown on its own statement row."
                )
            if "amount_usd" in classified.columns:
                missing_usd = int(classified["amount_usd"].isna().sum())
                if missing_usd:
                    missing_usd_rows = classified[classified["amount_usd"].isna()]
                    missing_rate_types = sorted(
                        value
                        for value in missing_usd_rows.get("rate_type", pd.Series(dtype=str))
                        .fillna("")
                        .astype(str)
                        .str.upper()
                        .unique()
                        if value and value != "USD/USD"
                    )
                    if missing_rate_types:
                        st.error(
                            f"{missing_usd} transaction(s) do not have a USD equivalent because "
                            f"these exchange rate(s) are missing in Setup > Rates: {', '.join(missing_rate_types)}. "
                            "The selected account can still be correct; load/update the Rates workbook before importing "
                            "if USD equivalents are required."
                        )
                    else:
                        st.warning(
                            f"{missing_usd} transaction(s) do not have a USD equivalent yet. "
                            "Check that the matching exchange rate exists in Setup > Rates."
                        )

            if balance_has_values(balance_info):
                currency = balance_info.get("currency") or selected_account.get("currency", "")
                render_summary_strip([
                    ("Opening balance", display_money(balance_info.get("opening_balance"), currency)),
                    ("Money in", display_money(balance_info.get("money_in"), currency)),
                    ("Money out", display_money(balance_info.get("money_out"), currency)),
                    ("Closing balance", display_money(balance_info.get("closing_balance"), currency)),
                ])
                balance_preview = pd.DataFrame([{
                    "Bank": selected_account.get("bank", "") or balance_info.get("bank", ""),
                    "Account": selected_account.get("account_name", ""),
                    "Account number": selected_account.get("account_number", "") or balance_info.get("account_number", ""),
                    "Currency": currency,
                    "Period start": balance_info.get("period_start", ""),
                    "Period end": balance_info.get("period_end", ""),
                    "Source": balance_info.get("source", ""),
                }])
                st.dataframe(balance_preview, use_container_width=True, hide_index=True)

            preview_cols = [
                "Date",
                "Description",
                "Amount",
                "account_name",
                "account_number",
                "currency",
                "currency_source",
                "amount_usd",
                "suggested_category",
                "suggested_subcategory",
                "match_type",
                "confidence",
                "dup_flag",
                "duplicate_reason",
            ]
            st.dataframe(
                classified[[col for col in preview_cols if col in classified.columns]],
                use_container_width=True,
                height=360,
            )

            st.warning(
                "Please verify that transaction signs and amounts have been imported correctly "
                "before transferring transactions to Pending Review or the Database."
            )

            if st.button("Import to pending review", type="primary"):
                inserted, duplicate_statement, skipped_duplicates = save_pending_transactions(
                    classified,
                    uploaded_statement.name,
                    statement_hash,
                )
                if duplicate_statement:
                    st.warning("This statement already exists. It was not imported again.")
                else:
                    save_statement_balance(
                        statement_hash,
                        uploaded_statement.name,
                        balance_info,
                        selected_account,
                    )
                    st.success(f"Imported {inserted} transactions to pending review.")
                    if skipped_duplicates:
                        st.info(f"Skipped {skipped_duplicates} duplicate transaction line(s).")
                    backfilled = backfill_missing_usd_amounts()
                    if backfilled:
                        st.info(f"Filled missing USD equivalents for {backfilled} imported transaction(s).")
                    st.cache_data.clear()
        except Exception as exc:
            st.error(str(exc))


elif page == "Import History":
    st.subheader("Import History")
    history = get_import_history()

    if history.empty:
        st.info("No statements have been imported yet.")
    else:
        duplicate_attempts = int(pd.to_numeric(history["duplicate_attempts"], errors="coerce").fillna(0).sum())
        missing_balances = int((history["balance_status"] == "Missing data").sum()) if "balance_status" in history else 0
        needs_review = int((history.get("reconciliation_status", pd.Series(dtype=str)) == "Needs review").sum())
        render_summary_strip([
            ("Imported statements", len(history)),
            ("Duplicate attempts", duplicate_attempts),
            ("Balance warnings", needs_review),
            ("Missing balances", missing_balances),
        ])

        h1, h2, h3 = st.columns(3)
        month_values = sorted(
            pd.to_datetime(history["imported_at"], errors="coerce").dt.to_period("M").dropna().astype(str).unique(),
            reverse=True,
        )
        account_values = sorted(value for value in history["account_name"].fillna("").astype(str).unique() if value)
        status_values = sorted(value for value in history["duplicate_status"].fillna("").astype(str).unique() if value)
        selected_month = h1.selectbox("Imported month", ["All months"] + month_values, key="history_month")
        selected_account = h2.selectbox("Account", ["All accounts"] + account_values, key="history_account")
        selected_status = h3.selectbox("Status", ["All statuses"] + status_values, key="history_status")

        history_view = history.copy()
        if selected_month != "All months":
            imported_month = pd.to_datetime(history_view["imported_at"], errors="coerce").dt.to_period("M").astype(str)
            history_view = history_view[imported_month == selected_month].copy()
        if selected_account != "All accounts":
            history_view = history_view[history_view["account_name"].fillna("").astype(str) == selected_account].copy()
        if selected_status != "All statuses":
            history_view = history_view[history_view["duplicate_status"].fillna("").astype(str) == selected_status].copy()

        search = st.text_input("Search import history")
        if search:
            mask = history_view.astype(str).apply(
                lambda col: col.str.contains(search, case=False, na=False)
            ).any(axis=1)
            history_view = history_view[mask].copy()

        history_cols = [
            "statement_name",
            "imported_at",
            "transaction_count",
            "account_name",
            "bank",
            "account_number",
            "currency",
            "period_start",
            "period_end",
            "closing_balance",
            "duplicate_status",
            "last_duplicate_at",
            "balance_status",
            "reconciliation_difference",
        ]
        st.dataframe(
            history_view[[col for col in history_cols if col in history_view.columns]],
            use_container_width=True,
            hide_index=True,
            height=520,
        )
        st.download_button(
            "Download import history Excel",
            data=dataframe_to_excel_bytes({"Import history": history_view}),
            file_name="import_history.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )

        st.markdown("#### All Imported Rows by Statement")
        import_audit = get_import_transaction_audit()
        if import_audit.empty:
            st.info("No imported transaction rows found.")
        else:
            render_summary_strip([
                ("Database rows", int(pd.to_numeric(import_audit["database_rows"], errors="coerce").fillna(0).sum())),
                ("Pending rows", int(pd.to_numeric(import_audit["pending_rows"], errors="coerce").fillna(0).sum())),
                ("Reviewed rows", int(pd.to_numeric(import_audit["reviewed_rows"], errors="coerce").fillna(0).sum())),
                ("Excluded rows", int(pd.to_numeric(import_audit.get("excluded_rows", pd.Series(dtype=int)), errors="coerce").fillna(0).sum())),
                ("Missing USD", int(pd.to_numeric(import_audit["missing_usd_rows"], errors="coerce").fillna(0).sum())),
            ])
            audit_search = st.text_input("Search all imported rows by statement", key="import_audit_search")
            audit_view = import_audit.copy()
            if audit_search:
                mask = audit_view.astype(str).apply(
                    lambda col: col.str.contains(audit_search, case=False, na=False)
                ).any(axis=1)
                audit_view = audit_view[mask].copy()
            audit_cols = [
                "import_batch_id",
                "statement_name",
                "imported_at",
                "imported_rows_recorded",
                "database_rows",
                "pending_rows",
                "reviewed_rows",
                "excluded_rows",
                "duplicate_flagged_rows",
                "missing_usd_rows",
                "account_name",
                "bank",
                "account_number",
                "currency",
                "period_start",
                "period_end",
                "opening_balance",
                "money_in",
                "money_out",
                "closing_balance",
                "duplicate_attempts",
                "last_duplicate_at",
                "first_row_created_at",
                "last_row_created_at",
            ]
            st.dataframe(
                audit_view[[col for col in audit_cols if col in audit_view.columns]],
                use_container_width=True,
                hide_index=True,
                height=420,
            )

            exact_duplicates = get_exact_duplicate_audit()
            st.markdown("#### Exact Duplicate Groups")
            if exact_duplicates.empty:
                st.success("No high-confidence repeated row fingerprints found.")
            else:
                st.error(
                    f"{len(exact_duplicates)} high-confidence duplicate group(s) found. "
                    "Export a backup first and preserve the row with the most complete corrections."
                )
                exact_cols = [
                    "duplicate_confidence",
                    "duplicate_count",
                    "row_hash",
                    "ids",
                    "statements",
                    "import_batch_ids",
                    "import_timestamps",
                    "txn_date",
                    "currency",
                    "amount",
                    "account_name",
                    "bank",
                    "account_number",
                    "normalized_description",
                    "category_subcategory_values",
                    "category_conflict",
                    "pending_rows",
                    "reviewed_rows",
                    "earliest_period_start",
                    "latest_period_end",
                    "audit_reason",
                    "safe_cleanup_recommendation",
                ]
                st.dataframe(
                    exact_duplicates[[col for col in exact_cols if col in exact_duplicates.columns]],
                    use_container_width=True,
                    hide_index=True,
                    height=420,
                )

            cross_duplicates = get_cross_statement_duplicate_audit()
            st.markdown("#### Possible Cross-Statement Duplicates")
            if cross_duplicates.empty:
                st.success("No possible cross-statement duplicate groups detected.")
            else:
                st.warning(
                    f"{len(cross_duplicates)} possible duplicate group(s) found across different statements. "
                    "These are not confirmed duplicates. Review the IDs and source statements before changing any rows."
                )
                dup_cols = [
                    "duplicate_confidence",
                    "audit_reason",
                    "txn_date",
                    "currency",
                    "amount",
                    "duplicate_count",
                    "statement_files",
                    "account_name",
                    "bank",
                    "account_number",
                    "normalized_description",
                    "category_subcategory_values",
                    "category_conflict",
                    "pending_rows",
                    "reviewed_rows",
                    "ids",
                    "statements",
                    "import_batch_ids",
                    "import_timestamps",
                    "statement_ranges",
                    "first_seen",
                    "last_seen",
                    "safe_cleanup_recommendation",
                ]
                st.dataframe(
                    cross_duplicates[[col for col in dup_cols if col in cross_duplicates.columns]],
                    use_container_width=True,
                    hide_index=True,
                    height=420,
                )
            st.download_button(
                "Download import audit Excel",
                data=dataframe_to_excel_bytes({
                    "Imports by statement": import_audit,
                    "Exact duplicate groups": exact_duplicates,
                    "Cross statement duplicates": cross_duplicates,
                }),
                file_name="import_audit.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )


elif page == "Pending Review":
    st.subheader("Pending Review")
    pending_save_message = st.session_state.pop("pending_review_save_message", "")
    if pending_save_message:
        st.success(pending_save_message)
    pending = get_pending_transactions()
    categories = get_categories()
    subcategories = get_subcategories()

    if pending.empty:
        st.info("No pending transactions.")
    elif not categories:
        st.warning("No categories are loaded. Upload categories in Setup first.")
    else:
        statement_values = sorted(
            value
            for value in pending.get("statement_name", pd.Series(dtype=str)).fillna("").astype(str).unique()
            if value
        )
        statement_options = ["All pending statements"] + statement_values
        default_statement_index = 0
        pending_with_statement = pending[
            pending.get("statement_name", pd.Series(dtype=str)).fillna("").astype(str).str.strip() != ""
        ].copy()
        if not pending_with_statement.empty:
            if "id" in pending_with_statement.columns:
                pending_with_statement["_sort_order"] = pd.to_numeric(pending_with_statement["id"], errors="coerce")
            else:
                pending_with_statement["_sort_order"] = pd.to_datetime(
                    pending_with_statement.get("created_at", pd.Series(dtype=str)),
                    errors="coerce",
                )
            pending_with_statement = pending_with_statement.sort_values("_sort_order", na_position="first")
            latest_statement = str(pending_with_statement["statement_name"].iloc[-1])
            if latest_statement in statement_options:
                default_statement_index = statement_options.index(latest_statement)
        statement_filter = st.selectbox(
            "Statement",
            statement_options,
            index=default_statement_index,
            key="pending_statement_filter",
        )
        description_filter = st.text_input(
            "Filter by transaction description",
            placeholder="e.g. Wolt",
            key="pending_description_filter",
        )
        pending_view = pending.copy()
        if statement_filter != "All pending statements":
            pending_view = pending_view[pending_view["statement_name"].fillna("").astype(str) == statement_filter].copy()
        if description_filter:
            pending_view = pending_view[
                pending_view["original_description"].fillna("").astype(str).str.contains(
                    description_filter,
                    case=False,
                    regex=False,
                )
            ].copy()

        p1, p2, p3, p4, p5 = st.columns(5)
        p1.metric("Visible rows", len(pending_view))
        p2.metric("All pending backlog", len(pending))
        p3.metric("Exact", int((pending_view["match_type"] == "exact").sum()))
        p4.metric("Similar", int((pending_view["match_type"] == "similar").sum()))
        p5.metric("New", int((pending_view["match_type"] == "new").sum()))
        if statement_filter != "All pending statements" and len(pending) != len(pending_view):
            st.info(
                f"You are viewing {len(pending_view)} row(s) for {statement_filter}. "
                f"The {len(pending)} pending figure is the full backlog across all pending statements."
            )

        if pending_view.empty:
            st.warning("No pending transactions match the current filters.")
            st.stop()

        render_wrapped_descriptions(pending_view)
        render_category_correction_panel(
            pending_view,
            categories,
            "pending_single",
            "Categorise one pending transaction with filtered subcategories",
            expanded=False,
            inline=False,
        )
        render_bulk_categorise_panel(pending_view, categories, "pending", expanded=False, inline=False)
        st.caption(
            "Make all Category / Subcategory changes and Reviewed selections below, then save them together. "
            "The table will not refresh between individual edits."
        )
        with st.form("pending_review_batch_form", clear_on_submit=False):
            edited_pending = editable_pending_table(
                pending_view,
                categories,
                subcategories,
                "pending_editor_batch",
                defer_changes=True,
            )
            submit_pending = st.form_submit_button(
                "Apply reviewed transaction edits",
                type="primary",
            )

        if submit_pending:
            try:
                categories_df = get_categories(include_subcategories=True)
                save_df = _prepare_pending_review_save_rows(
                    pending_view,
                    edited_pending,
                    categories_df,
                )
                if save_df.empty:
                    st.warning(
                        "No rows were ticked as Reviewed and no Amount corrections were detected. "
                        "Nothing was saved."
                    )
                else:
                    with st.status(
                        f"Saving {len(save_df)} transaction edits...",
                        expanded=True,
                    ) as save_status:
                        saved = save_reviewed_rows(save_df)
                        _verify_transaction_edit_save(save_df)
                        save_status.update(
                            label=f"{saved} transaction edits saved successfully.",
                            state="complete",
                        )
                    active_editor_key = st.session_state.get(
                        "pending_editor_batch__active_editor_key"
                    )
                    if active_editor_key:
                        _clear_data_editor_state(active_editor_key)
                    _clear_transaction_read_caches()
                    if save_df["reviewed"].astype(bool).all():
                        message = (
                            f"{saved} transactions updated successfully. "
                            "Reviewed transactions were removed from Pending Review after the saved values were verified."
                        )
                    else:
                        message = (
                            f"{saved} transaction edits saved successfully. "
                            "Amount corrections on rows not marked Reviewed remain in Pending Review."
                        )
                    st.session_state["pending_review_save_message"] = message
                    st.rerun()
            except Exception as exc:
                st.error(f"No transaction edits were saved. {exc}")


elif page == "Database":
    st.subheader("Database")
    saved_message = st.session_state.pop("database_edit_save_message", "")
    if saved_message:
        st.success(saved_message)
    ensure_usd_backfilled(show_message=True)
    all_tx_raw = get_all_transactions()
    categories_df = get_categories(include_subcategories=True)
    categories = sorted(
        categories_df.get("category", pd.Series(dtype=str)).dropna().astype(str).unique().tolist()
    )
    subcategories = get_subcategories()

    if all_tx_raw.empty:
        st.info("No transactions imported yet.")
    else:
        all_tx = all_tx_raw.copy()
        all_tx["_status_key"] = all_tx["status"].fillna("pending").astype(str).str.strip().str.casefold()
        active_tx = active_financial_transactions(all_tx)
        excluded_total = len(all_tx) - len(active_tx)
        show_excluded = st.checkbox(
            "Show excluded transactions",
            value=False,
            help="Excluded rows are recoverable but hidden from active reports, Pending Review, and duplicate checks.",
        )
        if not show_excluded:
            all_tx = active_tx
        all_tx = all_tx.drop(columns=["_status_key"], errors="ignore")
        all_tx = add_report_group_column(all_tx, categories_df)
        db_filtered = transaction_filter_controls(all_tx, "database", include_category=True)
        search = st.text_input("Search database")
        db_view = db_filtered.copy()
        if search:
            db_view = db_view[database_search_mask(db_view, search)].copy()

        st.caption(f"Database path: {DB_PATH}")
        render_summary_strip([
            ("Visible rows", len(db_view)),
            ("Accounts", db_view["account_name"].replace("", pd.NA).dropna().nunique()),
            ("Pending", int((db_view["status"].fillna("pending") == "pending").sum()) if "status" in db_view else 0),
            ("Reviewed", int((db_view["status"].fillna("") == "reviewed").sum()) if "status" in db_view else 0),
            ("Excluded", excluded_total),
        ])
        report_group_audit = report_group_consistency_audit(all_tx, categories_df)
        with st.expander("Reporting group consistency check"):
            st.caption(
                "Read-only check against Setup / Categories. It lists category-subcategory pairs "
                "whose reporting group cannot be resolved exactly from the current Setup mapping."
            )
            if report_group_audit.empty:
                st.success("All visible active category/subcategory pairs resolve to a Setup reporting group.")
            else:
                st.warning(
                    f"{len(report_group_audit)} category/subcategory pair(s) need Setup/reporting-group review."
                )
                st.dataframe(report_group_audit, use_container_width=True, hide_index=True)

        def visible_missing_usd_details(frame):
            if "amount_usd" not in frame.columns or "amount" not in frame.columns:
                return pd.Series(False, index=frame.index), []
            mask = (
                pd.to_numeric(frame["amount"], errors="coerce").fillna(0).abs().gt(0.005)
                & pd.to_numeric(frame["amount_usd"], errors="coerce").isna()
            )
            missing_rate_types = set()
            if mask.any():
                rows = frame.loc[mask].copy()
                for _, missing_row in rows.iterrows():
                    rate_type = str(missing_row.get("rate_type", "") or "").strip().upper()
                    currency = str(missing_row.get("currency", "") or "").strip().upper()
                    if not rate_type and currency:
                        rate_type = "USD/USD" if currency == "USD" else f"{currency}/USD"
                    if rate_type:
                        missing_rate_types.add(rate_type)
            return mask, sorted(missing_rate_types)

        if "amount_usd" in db_view.columns and "amount" in db_view.columns:
            missing_usd_mask, missing_rate_types = visible_missing_usd_details(db_view)
            if missing_usd_mask.any():
                rate_note = f" Missing rate type(s): {', '.join(missing_rate_types)}." if missing_rate_types else ""
                st.warning(
                    f"{int(missing_usd_mask.sum())} visible row(s) still have no USD equivalent after automatic backfill."
                    f"{rate_note} Check Setup > Rates and then use Fill missing USD equivalents."
                )
        render_category_correction_panel(
            db_view,
            categories,
            "database_single",
            "Categorise one database transaction with filtered subcategories",
            expanded=False,
            inline=False,
        )
        render_bulk_categorise_panel(db_view, categories, "database", expanded=False, inline=False)
        render_transaction_split_panel(db_view, categories_df, categories, "database")
        render_transaction_exclusion_panel(db_view, "database")
        db_view = _with_category_pair_column(db_view)
        pair_options = _category_pair_options(categories_df, categories, db_view)
        editable_cols = [
            "id",
            "status",
            "reviewed",
            "txn_date",
            "account_name",
            "bank",
            "account_number",
            "currency",
            "amount",
            "amount_usd",
            "original_description",
            _CATEGORY_PAIR_COLUMN,
            "report_group",
            "match_type",
        ]
        st.caption(
            "Make all database edits below, then apply them together. The table will not refresh between "
            "individual edits; Reporting Group is recalculated on save."
        )
        db_editor_base = db_view[[col for col in editable_cols if col in db_view.columns]].copy()
        db_editor_key = _scoped_editor_key("database_editor", db_editor_base)
        db_editor_baseline = _refresh_category_pair_derived_columns(db_editor_base, categories_df)
        db_editor_frame = db_editor_baseline.copy()
        original_status = {}
        if "id" in db_view.columns and "status" in db_view.columns:
            original_status = {
                int(row["id"]): str(row.get("status", "") or "").strip().casefold()
                for _, row in db_view.dropna(subset=["id"]).iterrows()
            }
        with st.form(f"{db_editor_key}_batch_form", clear_on_submit=False):
            db_edit = st.data_editor(
                db_editor_frame,
                use_container_width=True,
                hide_index=True,
                height=620,
                column_config={
                    "id": st.column_config.NumberColumn("ID", disabled=True),
                    "status": st.column_config.SelectboxColumn("Status", options=["pending", "reviewed", "excluded"]),
                    "reviewed": st.column_config.CheckboxColumn("Reviewed"),
                    "original_description": st.column_config.TextColumn(
                        "Full statement description",
                        disabled=True,
                        width="large",
                    ),
                    _CATEGORY_PAIR_COLUMN: st.column_config.SelectboxColumn(
                        "Category / Subcategory",
                        options=pair_options,
                        required=False,
                        help=(
                            "Choose a valid category/subcategory pair from Setup. "
                            "Use 'No subcategory' when the category intentionally has no subcategory."
                        ),
                    ),
                    "report_group": st.column_config.TextColumn("Reporting group", disabled=True),
                },
                key=db_editor_key,
            )
            confirm_editor_exclude = st.checkbox(
                "Confirm any edited Status changes to excluded",
                key="database_editor_exclude_confirm",
                help="Required only when one or more edited rows are being changed to excluded.",
            )
            submit_database_edits = st.form_submit_button("Apply database edits", type="primary")
        if submit_database_edits:
            db_save = _refresh_category_pair_derived_columns(db_edit, categories_df)
            changed_rows = _changed_transaction_editor_rows(
                db_editor_baseline,
                db_save,
                categories_df,
                ["category", "subcategory", "reviewed", "status"],
            )
            edited_excluded_ids = []
            if "id" in changed_rows.columns and "status" in changed_rows.columns:
                for _, row in changed_rows.dropna(subset=["id"]).iterrows():
                    row_id = int(row["id"])
                    new_status = str(row.get("status", "") or "").strip().casefold()
                    if new_status == "excluded" and original_status.get(row_id) != "excluded":
                        edited_excluded_ids.append(row_id)
            if edited_excluded_ids and not confirm_editor_exclude:
                st.error("No changes applied. Please confirm excluded status changes first.")
            elif changed_rows.empty:
                st.info("No database changes to apply.")
            else:
                with st.status("Saving database changes...", expanded=True) as save_status:
                    save_df = _add_transaction_edit_expectations(
                        _apply_category_pair_values(changed_rows),
                        db_editor_baseline,
                    )
                    try:
                        count = update_database_rows(save_df)
                        if count != len(save_df):
                            raise RuntimeError(
                                f"Expected to save {len(save_df)} transaction(s), "
                                f"but the database confirmed {count}."
                            )
                        _verify_transaction_edit_save(save_df)
                    except ConcurrentTransactionEditError as exc:
                        save_status.update(
                            label="Save stopped because a transaction changed elsewhere.",
                            state="error",
                        )
                        st.error(str(exc))
                    except Exception as exc:
                        save_status.update(label="Database changes were not saved.", state="error")
                        st.error(f"Could not save database changes: {exc}")
                    else:
                        save_status.update(
                            label=f"Saved {count} database change(s). Refreshing the table...",
                            state="complete",
                        )
                        st.session_state["database_edit_save_message"] = (
                            f"Saved {count} database change(s). The refreshed database view is now complete."
                        )
                        _clear_transaction_read_caches()
                        st.rerun()

        if st.button("Fill missing USD equivalents"):
            missing_before, inferred_missing_rates = visible_missing_usd_details(db_view)
            count = backfill_missing_usd_amounts()
            if count:
                st.success(f"Calculated USD equivalents for {count} rows.")
            elif missing_before.any():
                rate_note = (
                    f" Missing or unresolved rate type(s): {', '.join(inferred_missing_rates)}."
                    if inferred_missing_rates
                    else ""
                )
                st.error(
                    f"{int(missing_before.sum())} visible row(s) still need USD equivalents, but no rows could be updated."
                    f"{rate_note} Please check Setup > Rates for these transaction dates."
                )
            else:
                st.info("No rows needed USD equivalent backfill.")
            st.cache_data.clear()
            st.rerun()

        corrected_upload = st.file_uploader(
            "Upload corrected database Excel",
            type=["xlsx", "xls"],
            key="database_corrections_upload",
        )
        st.info(
            "For corrected database Excel files, do not Clear or Full reset first. "
            "This import updates existing transactions by the ID column only and does not create new transactions. "
            "Keep the ID column unchanged. If you correct Account/Company, Bank, Account number, Currency, Date, "
            "or Amount, the app recalculates Rate type, FX rate, and USD amount automatically from Setup > Rates."
        )
        if st.button("Import corrected Excel updates (existing IDs only)", disabled=corrected_upload is None):
            try:
                count = import_database_updates_from_excel(corrected_upload)
                backfilled = backfill_missing_usd_amounts()
                message = f"Imported updates for {count} existing rows. No new transactions were created."
                if backfilled:
                    message += f" Filled missing USD equivalents for {backfilled} rows."
                st.success(message)
                st.cache_data.clear()
                st.rerun()
            except Exception as exc:
                st.error(str(exc))

        st.download_button(
            "Download filtered database Excel",
            data=dataframe_to_excel_bytes({"Transactions": db_view}),
            file_name="transactions_database_filtered.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        with st.expander("Recent transaction change log"):
            change_log = get_transaction_change_log()
            if change_log.empty:
                st.info("No transaction changes have been logged yet.")
            else:
                st.dataframe(change_log, use_container_width=True, hide_index=True, height=360)
                st.download_button(
                    "Download change log Excel",
                    data=dataframe_to_excel_bytes({"Change log": change_log}),
                    file_name="transaction_change_log.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                )
    render_manual_transaction_form(categories, subcategories)


elif page == "Balances":
    st.subheader("Statement Balances")
    balances = get_statement_balances()

    if balances.empty:
        st.info("No statement balance summaries have been imported yet.")
    else:
        numeric_closing = pd.to_numeric(balances["closing_balance"], errors="coerce")
        needs_review_count = int((balances["reconciliation_status"] == "Needs review").sum())
        render_summary_strip([
            ("Statements", len(balances)),
            ("With closing", int(numeric_closing.notna().sum())),
            ("Reconciled", int((balances["reconciliation_status"] == "OK").sum())),
            ("Needs review", needs_review_count),
        ])
        if needs_review_count:
            st.warning(f"{needs_review_count} statement balance summaries do not reconcile. Review the difference column.")

        search = st.text_input("Search balances")
        balance_view = balances.copy()
        if search:
            mask = balance_view.astype(str).apply(
                lambda col: col.str.contains(search, case=False, na=False)
            ).any(axis=1)
            balance_view = balance_view[mask].copy()

        editable_cols = [
            "id",
            "statement_name",
            "account_name",
            "bank",
            "account_number",
            "currency",
            "period_start",
            "period_end",
            "opening_balance",
            "money_in",
            "money_out",
            "closing_balance",
            "calculated_closing",
            "reconciliation_difference",
            "reconciliation_status",
            "source",
            "notes",
            "updated_at",
        ]
        balance_edit = st.data_editor(
            balance_view[[col for col in editable_cols if col in balance_view.columns]],
            use_container_width=True,
            hide_index=True,
            height=560,
            column_config={
                "id": st.column_config.NumberColumn("ID", disabled=True, width="small"),
                "statement_name": st.column_config.TextColumn("Statement", disabled=True, width="large"),
                "account_name": st.column_config.TextColumn("Account"),
                "bank": st.column_config.TextColumn("Bank"),
                "account_number": st.column_config.TextColumn("Account number"),
                "currency": st.column_config.TextColumn("Currency", width="small"),
                "period_start": st.column_config.TextColumn("Period start"),
                "period_end": st.column_config.TextColumn("Period end"),
                "opening_balance": st.column_config.NumberColumn("Opening balance", format="%.2f"),
                "money_in": st.column_config.NumberColumn("Money in / payments", format="%.2f"),
                "money_out": st.column_config.NumberColumn("Money out / charges", format="%.2f"),
                "closing_balance": st.column_config.NumberColumn("Closing balance", format="%.2f"),
                "calculated_closing": st.column_config.NumberColumn("Calculated closing", format="%.2f", disabled=True),
                "reconciliation_difference": st.column_config.NumberColumn("Difference", format="%.2f", disabled=True),
                "reconciliation_status": st.column_config.TextColumn("Reconciliation", disabled=True),
                "source": st.column_config.TextColumn("Source", disabled=True, width="small"),
                "notes": st.column_config.TextColumn("Notes", width="large"),
                "updated_at": st.column_config.TextColumn("Updated", disabled=True),
            },
            key="balances_editor",
        )
        if st.button("Apply balance edits", type="primary"):
            count = update_statement_balance_rows(balance_edit)
            st.success(f"Updated {count} balance rows.")
            st.cache_data.clear()
            st.rerun()

        st.download_button(
            "Download filtered balances Excel",
            data=dataframe_to_excel_bytes({"Statement balances": balance_view}),
            file_name="statement_balances_filtered.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )


elif page == "Memory":
    st.subheader("Memory")
    memory = get_memory()

    memory_upload = st.file_uploader(
        "Upload transaction memory",
        type=["xlsx", "xls"],
        key="memory_upload_file",
    )
    if memory_upload and st.button("Import memory", type="primary"):
        try:
            imported = import_memory_from_excel(memory_upload)
            st.success(f"Imported {imported} memory rows.")
            st.cache_data.clear()
            st.rerun()
        except Exception as exc:
            st.error(str(exc))

    if memory.empty:
        st.info("No learned transactions yet.")
    else:
        search = st.text_input("Search memory")
        memory_view = memory.copy()
        if search:
            mask = memory_view.astype(str).apply(
                lambda col: col.str.contains(search, case=False, na=False)
            ).any(axis=1)
            memory_view = memory_view[mask]
        st.dataframe(memory_view, use_container_width=True, height=560)
        st.download_button(
            "Download memory Excel",
            data=dataframe_to_excel_bytes({"Memory": memory}),
            file_name="transaction_memory.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )


elif page == "Reports":
    from reporting import (
        build_pdf_report,
        build_report_verification,
        build_sample_expenses_report,
        get_report_groups,
        safe_filename,
    )

    st.subheader("Sample Expenses Report")
    ensure_usd_backfilled(show_message=True)
    reviewed = get_saved_transactions()
    categories_df = get_categories(include_subcategories=True)

    if reviewed.empty:
        st.info("Review and save transactions before generating the sample expenses report.")
    else:
        filtered_reviewed = transaction_filter_controls(reviewed, "reports")
        if filtered_reviewed.empty:
            st.warning("No reviewed transactions match the selected filters.")
            st.stop()

        filtered_reviewed = filtered_reviewed.copy()
        filtered_reviewed["amount"] = pd.to_numeric(filtered_reviewed["amount"], errors="coerce").fillna(0)
        filtered_reviewed["amount_usd"] = pd.to_numeric(filtered_reviewed["amount_usd"], errors="coerce")
        verification_summary, verification_detail = build_report_verification(filtered_reviewed, categories_df)

        render_summary_strip([
            ("Reviewed rows", len(filtered_reviewed)),
            ("Expenses in report", format_currency(verification_summary["total_expenses"])),
            ("Income / deposits", format_currency(verification_summary["total_deposits"])),
            ("Own funds", verification_summary["own_funds_rows"]),
            ("Net movement", format_currency(verification_summary["net_movement"])),
            (
                "Rows verified",
                f"{verification_summary['represented_rows']} / {verification_summary['database_rows']}",
            ),
            ("Needs attention", verification_summary["rows_needing_attention"]),
        ])
        if verification_summary["rows_needing_attention"]:
            st.warning(
                f"{verification_summary['rows_needing_attention']} filtered database rows need attention before the report fully reconciles."
            )
        else:
            st.success("Report verification is OK for the selected filters.")

        report_bytes = build_sample_expenses_report(filtered_reviewed, categories_df)
        st.download_button(
            "Download sample expenses report",
            data=report_bytes,
            file_name="sample_expenses_report.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            type="primary",
        )
        st.download_button(
            "Download complete PDF report",
            data=build_pdf_report(filtered_reviewed, categories_df),
            file_name="all_categories_expenses_report.pdf",
            mime="application/pdf",
        )
        st.download_button(
            "Download report verification Excel",
            data=dataframe_to_excel_bytes({
                "Report check": pd.DataFrame([
                    {"Metric": "Database rows checked", "Value": verification_summary["database_rows"]},
                    {"Metric": "Rows represented in workbook", "Value": verification_summary["represented_rows"]},
                    {"Metric": "Expense rows included in expense totals", "Value": verification_summary["expense_rows_in_report"]},
                    {"Metric": "Income/deposit rows shown separately", "Value": verification_summary["deposit_rows"]},
                    {"Metric": "Own funds rows shown separately", "Value": verification_summary["own_funds_rows"]},
                    {"Metric": "Rows needing attention", "Value": verification_summary["rows_needing_attention"]},
                    {"Metric": "Total expenses in report", "Value": verification_summary["total_expenses"]},
                    {"Metric": "Total income/deposits", "Value": verification_summary["total_deposits"]},
                    {"Metric": "Total own funds", "Value": verification_summary["total_own_funds"]},
                    {"Metric": "Net movement", "Value": verification_summary["net_movement"]},
                ]),
                "Report verification": verification_detail,
            }),
            file_name="report_verification.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        report_groups = get_report_groups(categories_df)
        if report_groups:
            pdf_zip = BytesIO()
            with zipfile.ZipFile(pdf_zip, "w", zipfile.ZIP_DEFLATED) as archive:
                archive.writestr(
                    "all_categories_expenses_report.pdf",
                    build_pdf_report(filtered_reviewed, categories_df),
                )
                for group in report_groups:
                    archive.writestr(
                        f"{safe_filename(group)}_expenses_report.pdf",
                        build_pdf_report(filtered_reviewed, categories_df, group),
                    )
            st.download_button(
                "Download all PDF reports",
                data=pdf_zip.getvalue(),
                file_name="expense_reports_by_group.zip",
                mime="application/zip",
            )
            with st.expander("PDF reports by reporting group"):
                for group in report_groups:
                    st.download_button(
                        f"Download {group} PDF",
                        data=build_pdf_report(filtered_reviewed, categories_df, group),
                        file_name=f"{safe_filename(group)}_expenses_report.pdf",
                        mime="application/pdf",
                        key=f"pdf_report_{safe_filename(group)}",
                    )
        st.download_button(
            "Download filtered reviewed transactions Excel",
            data=dataframe_to_excel_bytes({"Reviewed transactions": filtered_reviewed}),
            file_name="reviewed_transactions_filtered.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )

        preview = filtered_reviewed[[
            "txn_date",
            "category",
            "subcategory",
            "amount",
            "amount_usd",
            "account_name",
            "original_description",
        ]].head(200)
        st.dataframe(preview, use_container_width=True, height=460)


elif page == "Setup":
    st.subheader("Setup")

    setup_categories = get_categories(include_subcategories=True)
    setup_accounts = get_accounts()
    setup_rates = get_rates()
    setup_missing = missing_setup_items(setup_categories, setup_accounts, setup_rates)
    setup_missing_rates = missing_account_rate_types(setup_accounts, setup_rates)
    missing_shared = [
        label for label, path in SHARED_SETUP_FILES.items()
        if not path.exists()
    ]
    if not missing_shared:
        if st.button("Load shared folder setup", type="primary"):
            category_count, account_count, rate_count = load_shared_setup_files()
            st.success(
                f"Loaded {category_count} category rows, "
                f"{account_count} accounts, and {rate_count} monthly rates."
            )
            st.cache_data.clear()
            st.rerun()
    elif setup_missing:
        st.warning("Shared setup files missing: " + ", ".join(missing_shared))

    c1, c2, c3 = st.columns(3)
    with c1:
        category_file = st.file_uploader("Expense categories", type=["xlsx", "xls"], key="category_file")
        if category_file and st.button("Replace categories", type="primary"):
            count = replace_categories_from_excel(category_file)
            st.success(f"Loaded {count} category rows.")
            st.cache_data.clear()
            st.rerun()

    with c2:
        account_file = st.file_uploader("Who made the expense", type=["xlsx", "xls"], key="account_file")
        if account_file and st.button("Replace accounts", type="primary"):
            count = replace_accounts_from_excel(account_file)
            st.success(f"Loaded {count} account rows.")
            st.cache_data.clear()
            st.rerun()

    with c3:
        rates_file = st.file_uploader("Monthly rates", type=["xlsx", "xls"], key="rates_file")
        if rates_file and st.button("Replace rates", type="primary"):
            count = replace_rates_from_excel(rates_file)
            st.success(f"Loaded {count} monthly rates.")
            st.cache_data.clear()
            st.rerun()

    if setup_missing_rates:
        st.warning(
            "Missing exchange rates needed by the current accounts: "
            + ", ".join(setup_missing_rates)
            + ". Replace the Rates workbook before importing statements for those currencies."
        )

    st.markdown("### Report Settings")
    configured_report_until = get_configured_report_until(app_now().date())
    setup_report_until = st.date_input(
        "Report until",
        value=configured_report_until,
        key="setup_report_until",
        help="Executive reports and the third link read this date from Setup.",
    )
    if st.button("Save report date", key="save_setup_report_until"):
        set_app_setting(REPORT_UNTIL_SETTING_KEY, setup_report_until.isoformat())
        st.success(f"Saved report date: {setup_report_until.strftime('%d/%m/%Y')}")
        st.cache_data.clear()
        st.rerun()

    setup_report_groups = sorted(
        group
        for group in setup_categories.get("report_group", pd.Series(dtype=str)).fillna("").astype(str).str.strip().unique()
        if group
    )
    group_settings = get_report_group_settings()
    setup_report_groups = _ordered_text_values(setup_report_groups)
    if setup_report_groups:
        st.markdown("### Report Group Controls")
        st.caption(
            "Use this private Setup table to choose selected-report visibility, third-link visibility, "
            "and the AI prompt for each reporting group."
        )
        with st.expander("Default AI prompt used when the group prompt is empty", expanded=False):
            st.text_area(
                "Default prompt",
                value=_default_reporting_group_prompt(),
                height=120,
                disabled=True,
                help="When a reporting group has its own AI prompt, only that prompt is used for the AI report.",
                key="setup_default_ai_prompt_preview",
            )
        settings_lookup = {}
        if not group_settings.empty:
            settings_lookup = {
                str(row.get("report_group", "") or "").strip(): row
                for _, row in group_settings.iterrows()
                if str(row.get("report_group", "") or "").strip()
            }
        settings_rows = []
        has_existing_settings = bool(settings_lookup)
        for group in setup_report_groups:
            existing = settings_lookup.get(group)
            default_third = (not has_existing_settings) and ("woking way llc" in group.casefold())
            settings_rows.append({
                "visible": True if existing is None else bool(int(existing.get("visible", 1) or 0)),
                "third_link_visible": default_third if existing is None else bool(int(existing.get("third_link_visible", 0) or 0)),
                "report_group": group,
                "ai_prompt": "" if existing is None else str(existing.get("ai_prompt", "") or ""),
            })
        settings_edit = st.data_editor(
            pd.DataFrame(settings_rows),
            hide_index=True,
            use_container_width=True,
            column_order=["visible", "third_link_visible", "report_group", "ai_prompt"],
            column_config={
                "visible": st.column_config.CheckboxColumn("Show in selected report"),
                "third_link_visible": st.column_config.CheckboxColumn("Show in third link"),
                "report_group": st.column_config.TextColumn("Reporting group", disabled=True),
                "ai_prompt": st.column_config.TextColumn("AI prompt / instructions"),
            },
            disabled=["report_group"],
            key="setup_report_group_settings_editor",
        )
        c_show, c_save = st.columns([1, 1])
        with c_show:
            if st.button("Show all selected-report groups", key="setup_show_all_executive_groups"):
                settings_edit["visible"] = True
                count = replace_report_group_settings(settings_edit)
                st.success(f"All {count} reporting groups are now shown in the selected report.")
                st.cache_data.clear()
                st.rerun()
        with c_save:
            if st.button("Save report group controls"):
                if not settings_edit["third_link_visible"].fillna(False).astype(bool).any():
                    st.warning("The third link must show at least one reporting group.")
                    st.stop()
                count = replace_report_group_settings(settings_edit)
                st.success(f"Saved controls for {count} reporting groups.")
                st.cache_data.clear()
                st.rerun()
    else:
        st.markdown("### Report Group Controls")
        st.info("No reporting groups are loaded yet. Add them through the Expense categories file or add a category row below.")
        new_visibility_group = st.text_input("Add reporting group to the visibility list")
        if st.button("Save new executive reporting group"):
            extra_group = str(new_visibility_group or "").strip()
            if not extra_group:
                st.warning("Enter a reporting group name first.")
                st.stop()
            settings_df = pd.DataFrame({
                "report_group": [extra_group],
                "visible": [True],
                "third_link_visible": [False],
                "ai_prompt": [""],
            })
            count = replace_report_group_settings(settings_df)
            st.success(f"Saved visibility for {count} reporting groups.")
            st.cache_data.clear()
            st.rerun()

    st.markdown("### Current Setup")
    setup_left, setup_middle, setup_right = st.columns(3)
    with setup_left:
        st.markdown("Categories")
        st.dataframe(setup_categories, use_container_width=True, height=260)
        new_category = st.text_input("Add category")
        new_subcategory = st.text_input("Add subcategory")
        new_report_group = st.text_input("Reporting group for new category")
        if st.button("Add category row"):
            add_category(new_category, new_subcategory, new_report_group)
            st.cache_data.clear()
            st.rerun()
    with setup_middle:
        st.markdown("Accounts")
        st.dataframe(setup_accounts, use_container_width=True, height=330)
    with setup_right:
        st.markdown("Rates")
        st.dataframe(setup_rates, use_container_width=True, height=330)

    st.markdown("### Maintenance")
    m1, m2 = st.columns(2)
    with m1:
        clear_confirm = st.text_input("Type CLEAR to clear transactions, import history, balances, and memory")
        if st.button(
            "Clear transactions and memory",
            disabled=clear_confirm.strip().upper() != "CLEAR",
        ):
            reset_runtime_data()
            st.success("Transactions and memory cleared.")
            st.cache_data.clear()
            st.rerun()
    with m2:
        reset_confirm = st.text_input("Type RESET to delete all setup and transaction data")
        if st.button(
            "Full reset",
            disabled=reset_confirm.strip().upper() != "RESET",
        ):
            full_reset_database()
            st.success("Database reset completed.")
            st.cache_data.clear()
            st.rerun()

    st.download_button(
        "Download full backup Excel",
        data=dataframe_to_excel_bytes({
            "Transactions": get_all_transactions(),
            "Statement balances": get_statement_balances(),
            "Import history": get_import_history(),
            "Memory": get_memory(),
            "Categories": get_categories(include_subcategories=True),
            "Accounts": get_accounts(),
            "Rates": get_rates(),
        }),
        file_name="statement_management_full_backup.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )
