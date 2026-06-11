from datetime import datetime, timedelta, timezone
from html import escape
from io import BytesIO
import os
from pathlib import Path
import re
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
import zipfile

import streamlit as st

from auth import get_login_user, require_login, sign_out

st.set_page_config(page_title="Statement Management", layout="wide")

require_login()

# Keep heavy data/reporting imports after the login gate so the first screen appears quickly.
import pandas as pd

from db import (
    DB_PATH,
    USING_POSTGRES,
    add_category,
    apply_account_and_rates,
    backfill_missing_usd_amounts,
    build_statement_hash,
    dataframe_to_excel_bytes,
    exclude_transactions,
    full_reset_database,
    get_accounts,
    get_all_transactions,
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
    statement_balance_exists,
    statement_already_imported,
    update_database_rows,
    update_statement_balance_rows,
)
from utils import format_currency


_DB_CACHE_TTL_SECONDS = 90
_db_get_accounts = get_accounts
_db_get_all_transactions = get_all_transactions
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


@st.cache_data(show_spinner=False, ttl=_DB_CACHE_TTL_SECONDS)
def get_accounts():
    return _db_get_accounts()


@st.cache_data(show_spinner=False, ttl=_DB_CACHE_TTL_SECONDS)
def get_all_transactions():
    return _db_get_all_transactions()


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
    try:
        count = backfill_missing_usd_amounts()
    except Exception as exc:
        if show_message:
            st.warning(f"Could not calculate missing USD equivalents automatically: {exc}")
        return 0
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


def add_report_group_column(df, categories_df):
    out = df.copy()
    group_map = category_report_group_map(categories_df)
    out["report_group"] = out.get("category", pd.Series(dtype=str)).fillna("").astype(str).map(
        lambda value: group_map.get(value.strip().casefold(), "")
    )
    return out


def transaction_filter_controls(df, key_prefix):
    filtered = df.copy()
    if filtered.empty:
        return filtered

    dates = pd.to_datetime(filtered.get("txn_date"), errors="coerce")
    month_values = sorted(dates.dt.to_period("M").dropna().astype(str).unique(), reverse=True)
    account_values = sorted(
        value for value in filtered.get("account_name", pd.Series(dtype=str)).fillna("").astype(str).unique()
        if value
    )

    f1, f2 = st.columns(2)
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
    return filtered


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


def editable_pending_table(df, categories, subcategories, key):
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

    visible_cols = [
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
        "statement_name",
        "amount_usd",
        "match_type",
        "confidence",
    ]
    table = table[[col for col in visible_cols if col in table.columns]]

    return st.data_editor(
        table,
        key=key,
        use_container_width=True,
        hide_index=True,
        height=min(680, 105 + max(len(table), 4) * 36),
        column_config={
            "id": st.column_config.NumberColumn("ID", disabled=True, width="small"),
            "reviewed": st.column_config.CheckboxColumn("Reviewed", help="Tick only rows that are ready to save."),
            "txn_date": st.column_config.TextColumn("Date", disabled=True),
            "account_name": st.column_config.TextColumn("Account", disabled=True),
            "bank": st.column_config.TextColumn("Bank", disabled=True),
            "account_number": st.column_config.TextColumn("Account number", disabled=True),
            "statement_name": st.column_config.TextColumn("Statement", disabled=True, width="large"),
            "currency": st.column_config.TextColumn("Currency", disabled=True, width="small"),
            "amount": st.column_config.NumberColumn("Statement amount", format="%.2f", disabled=True),
            "amount_usd": st.column_config.NumberColumn("USD amount", format="%.2f", disabled=True),
            "original_description": st.column_config.TextColumn("Full statement description", disabled=True, width="large"),
            "match_type": st.column_config.TextColumn("Match", disabled=True, width="small"),
            "confidence": st.column_config.NumberColumn("Confidence", format="%.2f", disabled=True, width="small"),
            "category": st.column_config.SelectboxColumn(
                "Category",
                options=[""] + categories,
                required=False,
            ),
            "subcategory": st.column_config.TextColumn(
                "Subcategory",
                disabled=True,
                help=(
                    "Use the filtered correction panel above for subcategory changes. "
                    "That panel only shows subcategories belonging to the selected category."
                ),
            ),
        },
    )


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


def render_category_correction_panel(df, categories, key_prefix, title="Correct category / subcategory"):
    if df.empty or "id" not in df.columns or not categories:
        return
    working = df.dropna(subset=["id"]).copy()
    if working.empty:
        return
    working["id"] = working["id"].astype(int)
    labels = {_transaction_label(row): int(row["id"]) for _, row in working.iterrows()}

    with st.expander(title):
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
        current_subcategory = str(selected_row.get("subcategory", "") or "").strip()
        if st.session_state.get(subcategory_key) not in subcategory_options:
            st.session_state[subcategory_key] = (
                current_subcategory if current_subcategory in subcategory_options else ""
            )
        subcategory = st.selectbox("Subcategory", subcategory_options, key=subcategory_key)

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
            count = update_database_rows(update_df)
            if count:
                st.success("Transaction updated and marked as reviewed.")
                st.cache_data.clear()
                st.rerun()
            else:
                st.warning("No transaction was updated.")


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


def render_bulk_categorise_panel(df, categories, key_prefix):
    if df.empty or "id" not in df.columns or not categories:
        return
    with st.expander("Bulk categorise current filtered rows"):
        st.caption(
            "Use this after filtering, for example by description. "
            "It applies one category/subcategory to all rows currently visible below."
        )
        category_options = [""] + categories
        category = st.selectbox("Category", category_options, key=f"{key_prefix}_bulk_category")
        subcategory = st.selectbox(
            "Subcategory",
            _subcategory_options_for(category),
            key=f"{key_prefix}_bulk_subcategory",
        )
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
            count = update_database_rows(update_df)
            if count:
                st.success(f"Updated {count} transactions.")
                st.cache_data.clear()
                st.rerun()
            else:
                st.warning("No transactions were updated.")


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
    return {month: month.to_timestamp().strftime("%B %Y") for month in months}


def _executive_short_month_label(month):
    if month is None:
        return ""
    return month.to_timestamp().strftime("%b")


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


def _executive_change_pct(change, previous_amount):
    if abs(previous_amount) <= 0.005:
        return None
    return (change / previous_amount) * 100


def _executive_metric_values(frame, months, denominator=0.0):
    month_values = {
        month: float(frame.loc[frame["month"] == month, "expense_usd"].sum())
        for month in months
    }
    total_amount = float(frame["expense_usd"].sum()) if "expense_usd" in frame.columns else 0.0
    current_month = months[-1] if months else None
    previous_month = months[-2] if len(months) > 1 else None
    first_month = months[0] if months else None
    current_amount = month_values.get(current_month, 0.0)
    previous_amount = month_values.get(previous_month, 0.0)
    first_amount = month_values.get(first_month, 0.0)
    change = current_amount - previous_amount
    trend_class, trend_text = _executive_trend(change)
    period_change = current_amount - first_amount
    period_trend_class, period_trend_text = _executive_trend(period_change)
    return {
        "months": month_values,
        "current": current_amount,
        "previous": previous_amount,
        "period_start": first_amount,
        "total": total_amount,
        "share_pct": (total_amount / denominator * 100) if abs(denominator) > 0.005 else None,
        "average": (sum(month_values.values()) / len(month_values)) if month_values else 0.0,
        "change": change,
        "change_pct": _executive_change_pct(change, previous_amount),
        "trend_class": trend_class,
        "trend_text": trend_text,
        "period_change": period_change,
        "period_change_pct": _executive_change_pct(period_change, first_amount),
        "period_trend_class": period_trend_class,
        "period_trend_text": period_trend_text,
    }


def _executive_level_rows(expenses, level_column, months, extra_labels=None):
    rows = []
    extra_labels = _ordered_text_values(extra_labels or [])
    if (expenses.empty or level_column not in expenses.columns) and not extra_labels:
        return rows
    denominator = float(expenses["expense_usd"].sum()) if "expense_usd" in expenses.columns else 0.0
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
        labels = _ordered_text_values(labels + extra_labels)
    for label in labels:
        frame = (
            expenses[expenses[level_column].fillna("").astype(str).str.strip() == label].copy()
            if level_column in expenses.columns
            else expenses.iloc[0:0].copy()
        )
        if frame.empty and label not in extra_labels:
            continue
        metrics = _executive_metric_values(frame, months, denominator)
        metrics["label"] = label or "No subcategory"
        metrics["value"] = label
        rows.append(metrics)
    rows.sort(key=lambda row: (row["share_pct"] or 0.0, row["total"]), reverse=True)
    return rows


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


def _save_executive_visible_groups(all_report_groups, visible_groups):
    settings_df = pd.DataFrame({
        "report_group": all_report_groups,
        "visible": [group in visible_groups for group in all_report_groups],
    })
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


def _set_executive_selection(level, value):
    st.session_state[f"executive_{level}"] = value
    if level == "group":
        st.session_state.pop("executive_category", None)
        st.session_state.pop("executive_subcategory", None)
    elif level == "category":
        st.session_state.pop("executive_subcategory", None)


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


def _render_executive_click_rows(title, rows, level, months, month_labels, show_all_months=False):
    st.markdown(f"#### {title}")
    if not rows:
        st.info("No rows available for this level.")
        return
    display_months = months if show_all_months else months[-1:]
    trend_start = _executive_short_month_label(months[0] if months else None)
    trend_end = _executive_short_month_label(months[-1] if months else None)
    if show_all_months:
        tail_defs = [
            ("change", "Change from previous month", 1.25, lambda row: _money(row["change"]), "trend_class"),
            ("change_pct", "% change", 0.85, lambda row: _percent(row["change_pct"]), "trend_class"),
            ("trend_text", "Status from previous month", 1.25, lambda row: row["trend_text"], "trend_class"),
            ("period_change", f"Trend {trend_start}-{trend_end}", 1.25, lambda row: _money(row["period_change"]), "period_trend_class"),
            ("period_change_pct", "% trend", 0.85, lambda row: _percent(row["period_change_pct"]), "period_trend_class"),
            ("period_trend_text", "Trend status", 1.15, lambda row: row["period_trend_text"], "period_trend_class"),
        ]
    else:
        tail_defs = [
            ("change", "Change from previous month", 1.25, lambda row: _money(row["change"]), "trend_class"),
            ("trend_text", "Status from previous month", 1.25, lambda row: row["trend_text"], "trend_class"),
            ("period_change", f"Trend {trend_start}-{trend_end}", 1.25, lambda row: _money(row["period_change"]), "period_trend_class"),
        ]
    base_defs = [("total", "SUM", 1, lambda row: _money(row["total"]))]
    if not show_all_months:
        base_defs.extend([
            ("share_pct", "%", 0.75, lambda row: _percent(row["share_pct"])),
            ("average", "Average", 1, lambda row: _money(row["average"])),
        ])
    widths = [2.4] + [width for _, _, width, _ in base_defs] + [1 for _ in display_months] + [
        width for _, _, width, _, _ in tail_defs
    ]
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

    for idx, row in enumerate(rows):
        cols = st.columns(widths)
        col_idx = 0
        with cols[col_idx]:
            if st.button(row["label"], key=f"executive_{level}_{idx}", use_container_width=True):
                _set_executive_selection(level, row["value"])
                st.rerun()
        for _, _, _, formatter in base_defs:
            col_idx += 1
            cols[col_idx].markdown(f"<div class=\"drill-cell\">{formatter(row)}</div>", unsafe_allow_html=True)
        for month in display_months:
            col_idx += 1
            cols[col_idx].markdown(
                f"<div class=\"drill-cell\">{_money(row['months'].get(month, 0.0))}</div>",
                unsafe_allow_html=True,
            )
        for _, _, _, formatter, class_key in tail_defs:
            col_idx += 1
            css_class = row.get(class_key, "trend-flat")
            cols[col_idx].markdown(
                f"<div class=\"drill-cell {css_class}\">{formatter(row)}</div>",
                unsafe_allow_html=True,
            )
    zero_rows = []
    for row in rows:
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
    if zero_rows:
        with st.expander(f"Why {title.lower()} rows show $0", expanded=False):
            st.dataframe(pd.DataFrame(zero_rows), use_container_width=True, hide_index=True)
            st.caption(
                "$0 rows are not hidden. They stay visible so setup groups, categories, and subcategories "
                "can be checked even when there is no activity in the selected period."
            )


def _render_executive_transactions(expenses, selected_group, selected_category, selected_subcategory):
    detail = expenses[
        (expenses["report_group"].fillna("").astype(str).str.strip() == selected_group)
        & (expenses["category"].fillna("").astype(str).str.strip() == selected_category)
        & (expenses["subcategory"].fillna("").astype(str).str.strip() == selected_subcategory)
    ].copy()
    if detail.empty:
        st.info("No transactions found for the selected subcategory.")
        return
    detail["txn_date"] = pd.to_datetime(detail["txn_date"], errors="coerce").dt.strftime("%Y-%m-%d")
    detail = detail.sort_values(["txn_date", "expense_usd"], ascending=[False, False])
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
        "expense_usd",
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
    categories = get_categories()
    render_summary_strip([
        ("Rows", len(visible)),
        ("Total", _money(detail_view["expense_usd"].sum())),
        ("Category", selected_category),
        ("Subcategory", selected_subcategory or "No subcategory"),
    ])
    render_wrapped_descriptions(detail_view, expanded=True)
    render_bulk_categorise_panel(detail_view, categories, "executive_detail")
    edited_detail = st.data_editor(
        visible,
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
            "expense_usd": st.column_config.NumberColumn("Report amount USD", format="%.2f", disabled=True),
            "category": st.column_config.SelectboxColumn("Category", options=[""] + categories, required=False),
            "subcategory": st.column_config.TextColumn(
                "Subcategory",
                disabled=True,
                help=(
                    "Use the bulk categorise panel above for changes. "
                    "Its subcategory list is filtered by the selected category."
                ),
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
        key="executive_detail_editor",
    )
    if st.button("Apply transaction detail edits", type="primary", key="executive_detail_apply"):
        save_cols = [col for col in ["id", "category", "subcategory", "reviewed"] if col in edited_detail.columns]
        save_df = edited_detail[save_cols].copy()
        if "reviewed" not in save_df.columns:
            save_df["reviewed"] = True
        save_df["status"] = save_df["reviewed"].map(lambda value: "reviewed" if bool(value) else "pending")
        count = update_database_rows(save_df)
        st.success(f"Updated {count} visible transactions.")
        st.cache_data.clear()
        st.rerun()
    st.download_button(
        "Download selected transactions Excel",
        data=dataframe_to_excel_bytes({"Transactions": visible}),
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


def _build_family_analysis(expenses, months, month_labels):
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

    analysis = "\n\n".join([
        (
            f"**1-family analysis through {month_labels.get(current_month, str(current_month))}**\n"
            f"- Total reviewed 1-family expenses in this report window: {_money(total)}.\n"
            f"- Current month: {_money(current_total)}; previous month: {_money(previous_total)}.\n"
            f"- Overall movement: 1-family {trend_text} by {_money(abs(change))}."
        ),
        "**Main cost drivers**\n" + "\n".join(top_category_lines),
        _format_analysis_bullets("What is going up", increase_rows, "category", "increased"),
        _format_analysis_bullets("What is going down", decrease_rows, "category", "decreased"),
        "**What to notice**\n" + "\n".join(subcategory_lines),
        "**How to cut down**\n" + "\n".join(cut_lines),
    ])

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
    if st.button("Generate 1-family AI analysis", type="primary", key="generate_family_analysis"):
        st.session_state["family_analysis_generated"] = True

    if st.session_state.get("family_analysis_generated"):
        analysis, export_df = _build_family_analysis(expenses, months, month_labels)
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


def _render_executive_drilldown(
    expenses,
    months,
    month_labels,
    visible_report_groups=None,
    categories_df=None,
    show_all_months=False,
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

    if selected_group:
        trail = f"Reporting group: {selected_group}"
        if selected_category:
            trail += f" / Category: {selected_category}"
        if selected_subcategory is not None:
            trail += f" / Subcategory: {selected_subcategory or 'No subcategory'}"
        st.markdown(f"<div class=\"drill-breadcrumb\">{escape(trail)}</div>", unsafe_allow_html=True)

    group_rows = _executive_level_rows(
        expenses,
        "report_group",
        months,
        extra_labels=visible_report_groups,
    )
    _render_executive_click_rows(
        "1. Reporting Groups",
        group_rows,
        "group",
        months,
        month_labels,
        show_all_months=show_all_months,
    )

    if not selected_group:
        return
    group_expenses = expenses[expenses["report_group"].fillna("").astype(str).str.strip() == selected_group].copy()
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
    )

    if selected_subcategory is None:
        return
    _render_executive_transactions(expenses, selected_group, selected_category, selected_subcategory)


def _render_executive_completeness_check(filtered_transactions, categories_df, visible_report_groups):
    from reporting import build_report_verification

    summary, detail = build_report_verification(filtered_transactions, categories_df)
    setup_group_count = (
        categories_df.get("report_group", pd.Series(dtype=str)).fillna("").astype(str).str.strip().replace("", pd.NA).dropna().nunique()
        if not categories_df.empty
        else 0
    )
    setup_category_count = (
        categories_df.get("category", pd.Series(dtype=str)).fillna("").astype(str).str.strip().replace("", pd.NA).dropna().nunique()
        if not categories_df.empty
        else 0
    )
    setup_subcategory_count = (
        categories_df.get("subcategory", pd.Series(dtype=str)).fillna("").astype(str).str.strip().replace("", pd.NA).dropna().nunique()
        if not categories_df.empty
        else 0
    )
    visible_set = {str(group or "").strip() for group in (visible_report_groups or [])}
    report_groups = (
        detail.get("Reporting group", pd.Series(dtype=str))
        .fillna("")
        .astype(str)
        .str.strip()
    )
    hidden_rows = int((~report_groups.isin(visible_set) & report_groups.ne("")).sum()) if visible_set else 0
    missing_groups = int(report_groups.eq("").sum()) if not detail.empty else 0
    render_summary_strip([
        ("Completeness checked", summary.get("database_rows", 0)),
        ("Represented rows", summary.get("represented_rows", 0)),
        ("Needs attention", summary.get("rows_needing_attention", 0)),
        ("Hidden by visibility", hidden_rows),
        ("Setup groups", setup_group_count),
        ("Setup categories", setup_category_count),
        ("Setup subcategories", setup_subcategory_count),
    ])
    if summary.get("rows_needing_attention", 0) or missing_groups or hidden_rows:
        st.warning(
            "Report completeness check found rows to review. Open the audit below to see whether rows are missing "
            "USD equivalents/rates, missing reporting groups, or hidden by the selected Executive visibility list."
        )
    else:
        st.success("Report completeness check passed for the current period.")

    audit_cols = [
        "Database ID",
        "Date",
        "Account",
        "Bank",
        "Account number",
        "Currency",
        "Statement amount",
        "USD equivalent",
        "Category",
        "Subcategory",
        "Reporting group",
        "Report status",
        "Full statement description",
    ]
    with st.expander("Report completeness audit", expanded=False):
        st.dataframe(
            detail[[col for col in audit_cols if col in detail.columns]],
            use_container_width=True,
            hide_index=True,
        )


def render_executive_report():
    from reporting import _prepare_report_data

    shared_report = _is_shared_executive_report_request()
    st.subheader("TB Family Office Executive Expenses Report" if shared_report else "Executive Summary")

    ensure_usd_backfilled()
    reviewed = get_saved_transactions()
    categories_df = get_categories(include_subcategories=True)
    if reviewed.empty:
        st.info("No reviewed transactions are available for the executive report yet.")
        return

    date_values = pd.to_datetime(reviewed.get("txn_date"), errors="coerce").dropna()
    default_end = date_values.max().date() if not date_values.empty else app_now().date()
    requested_end = st.query_params.get("to")
    if requested_end:
        parsed_requested_end = pd.to_datetime(requested_end, errors="coerce")
        if not pd.isna(parsed_requested_end):
            default_end = parsed_requested_end.date()

    if shared_report:
        cutoff = default_end
        st.caption(f"Report until {cutoff.strftime('%d/%m/%Y')}")
    else:
        cutoff = st.date_input("Report until", value=default_end, key="executive_report_until")
    cutoff_ts = pd.Timestamp(cutoff)
    reviewed = reviewed.copy()
    reviewed["txn_date"] = pd.to_datetime(reviewed["txn_date"], errors="coerce")
    filtered = reviewed[reviewed["txn_date"].notna() & (reviewed["txn_date"] <= cutoff_ts)].copy()
    if filtered.empty:
        st.warning("No reviewed transactions exist up to the selected date.")
        return

    _, expenses, _, _ = _prepare_report_data(filtered, categories_df, include_own_funds=True)
    all_report_groups = _executive_report_group_options(categories_df, expenses)
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
            "Analytical view: show all months and hidden columns",
            value=False,
            key="executive_show_all_months",
        )
        if report_mode.startswith("Areti"):
            visible_report_groups = all_report_groups
        else:
            visible_report_groups = _render_executive_group_visibility_control(all_report_groups)
        _render_executive_completeness_check(filtered, categories_df, visible_report_groups)

    if visible_report_groups:
        expenses = expenses[
            expenses["report_group"].fillna("").astype(str).str.strip().isin(visible_report_groups)
        ].copy()

    if expenses.empty and not visible_report_groups:
        st.info("No reviewed expense rows match the selected period.")
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
    )


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
        render_bulk_categorise_panel(pending_view, categories, "pending")
        render_category_correction_panel(
            pending_view,
            categories,
            "pending_single",
            "Correct one pending transaction with filtered subcategories",
        )
        top_save = st.button("Save reviewed rows", type="primary", key="save_reviewed_top")
        edited_pending = editable_pending_table(pending_view, categories, subcategories, "pending_editor")
        bottom_save = st.button("Save reviewed rows", type="primary", key="save_reviewed_bottom")

        if top_save or bottom_save:
            with st.spinner("Saving reviewed rows..."):
                saved = save_reviewed_rows(edited_pending)
            if saved:
                st.success(f"Saved {saved} reviewed transactions.")
                st.cache_data.clear()
                st.rerun()
            else:
                st.warning("No rows were ticked as reviewed.")


elif page == "Database":
    st.subheader("Database")
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
        excluded_total = int((all_tx["_status_key"] == "excluded").sum())
        show_excluded = st.checkbox(
            "Show excluded transactions",
            value=False,
            help="Excluded rows are recoverable but hidden from active reports, Pending Review, and duplicate checks.",
        )
        if not show_excluded:
            all_tx = all_tx[all_tx["_status_key"] != "excluded"].copy()
        all_tx = all_tx.drop(columns=["_status_key"], errors="ignore")
        all_tx = add_report_group_column(all_tx, categories_df)
        db_filtered = transaction_filter_controls(all_tx, "database")
        search = st.text_input("Search database")
        db_view = db_filtered.copy()
        if search:
            mask = db_view.astype(str).apply(
                lambda col: col.str.contains(search, case=False, na=False)
            ).any(axis=1)
            db_view = db_view[mask].copy()

        st.caption(f"Database path: {DB_PATH}")
        render_summary_strip([
            ("Visible rows", len(db_view)),
            ("Accounts", db_view["account_name"].replace("", pd.NA).dropna().nunique()),
            ("Pending", int((db_view["status"].fillna("pending") == "pending").sum()) if "status" in db_view else 0),
            ("Reviewed", int((db_view["status"].fillna("") == "reviewed").sum()) if "status" in db_view else 0),
            ("Excluded", excluded_total),
        ])
        if "amount_usd" in db_view.columns and "amount" in db_view.columns:
            missing_usd_mask = (
                pd.to_numeric(db_view["amount"], errors="coerce").fillna(0).abs().gt(0.005)
                & pd.to_numeric(db_view["amount_usd"], errors="coerce").isna()
            )
            if missing_usd_mask.any():
                missing_rate_types = sorted(
                    value
                    for value in db_view.loc[missing_usd_mask, "rate_type"].fillna("").astype(str).str.strip().unique()
                    if value
                ) if "rate_type" in db_view.columns else []
                rate_note = f" Missing rate type(s): {', '.join(missing_rate_types)}." if missing_rate_types else ""
                st.warning(
                    f"{int(missing_usd_mask.sum())} visible row(s) still have no USD equivalent after automatic backfill."
                    f"{rate_note} Check Setup > Rates and then use Fill missing USD equivalents."
                )
        render_bulk_categorise_panel(db_view, categories, "database")
        render_category_correction_panel(
            db_view,
            categories,
            "database_single",
            "Correct one database transaction with filtered subcategories",
        )
        render_transaction_exclusion_panel(db_view, "database")
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
            "category",
            "subcategory",
            "report_group",
            "match_type",
        ]
        db_edit = st.data_editor(
            db_view[[col for col in editable_cols if col in db_view.columns]],
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
                "category": st.column_config.SelectboxColumn(
                    "Category",
                    options=[""] + categories,
                    required=False,
                ),
                "subcategory": st.column_config.TextColumn(
                    "Subcategory",
                    disabled=True,
                    help=(
                        "Use the filtered correction panel above for subcategory changes. "
                        "That panel only shows subcategories belonging to the selected category."
                    ),
                ),
                "report_group": st.column_config.TextColumn("Reporting group", disabled=True),
            },
            key="database_editor",
        )
        original_status = {}
        if "id" in db_view.columns and "status" in db_view.columns:
            original_status = {
                int(row["id"]): str(row.get("status", "") or "").strip().casefold()
                for _, row in db_view.dropna(subset=["id"]).iterrows()
            }
        edited_excluded_ids = []
        if "id" in db_edit.columns and "status" in db_edit.columns:
            for _, row in db_edit.dropna(subset=["id"]).iterrows():
                row_id = int(row["id"])
                new_status = str(row.get("status", "") or "").strip().casefold()
                if new_status == "excluded" and original_status.get(row_id) != "excluded":
                    edited_excluded_ids.append(row_id)
        confirm_editor_exclude = True
        if edited_excluded_ids:
            st.warning(
                f"{len(edited_excluded_ids)} row(s) were changed to excluded in the editor. "
                "Confirm before applying because these rows will be hidden from active reports and review."
            )
            confirm_editor_exclude = st.checkbox(
                "Confirm editor status changes to excluded",
                key="database_editor_exclude_confirm",
            )
        if st.button("Apply database edits", type="primary"):
            if edited_excluded_ids and not confirm_editor_exclude:
                st.error("No changes applied. Please confirm excluded status changes first.")
            else:
                count = update_database_rows(db_edit)
                st.success(f"Updated {count} rows.")
                st.cache_data.clear()
                st.rerun()

        if st.button("Fill missing USD equivalents"):
            count = backfill_missing_usd_amounts()
            if count:
                st.success(f"Calculated USD equivalents for {count} rows.")
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

    setup_report_groups = sorted(
        group
        for group in setup_categories.get("report_group", pd.Series(dtype=str)).fillna("").astype(str).str.strip().unique()
        if group
    )
    group_settings = get_report_group_settings()
    setup_report_groups = _ordered_text_values(setup_report_groups)
    if setup_report_groups:
        st.markdown("### Executive Report Visibility")
        if group_settings.empty:
            default_visible_groups = setup_report_groups
        else:
            default_visible_groups = (
                group_settings[group_settings["visible"].fillna(0).astype(int) == 1]["report_group"]
                .fillna("")
                .astype(str)
                .tolist()
            )
            default_visible_groups = [group for group in default_visible_groups if group in setup_report_groups]
            known_setting_groups = set(group_settings["report_group"].fillna("").astype(str).str.strip())
            default_visible_groups.extend(
                group for group in setup_report_groups if group not in known_setting_groups
            )
        selected_visible_groups = _render_report_group_visibility_editor(
            setup_report_groups,
            default_visible_groups,
            "setup_executive",
        )
        new_visibility_group = st.selectbox(
            "Add existing reporting group to the visibility list",
            [""] + setup_report_groups,
            key="setup_add_existing_executive_group",
        )
        c_show, c_save = st.columns([1, 1])
        with c_show:
            if st.button("Show all executive report groups", key="setup_show_all_executive_groups"):
                count = _save_executive_visible_groups(setup_report_groups, setup_report_groups)
                st.success(f"All {count} reporting groups are now shown.")
                st.cache_data.clear()
                st.rerun()
        with c_save:
            if st.button("Save executive report visibility"):
                extra_group = str(new_visibility_group or "").strip()
                visible_to_save = _ordered_text_values(selected_visible_groups + ([extra_group] if extra_group else []))
                if not visible_to_save:
                    st.warning("Select at least one reporting group before saving.")
                    st.stop()
                count = _save_executive_visible_groups(setup_report_groups, visible_to_save)
                st.success(f"Saved visibility for {count} reporting groups.")
                st.cache_data.clear()
                st.rerun()
    else:
        st.markdown("### Executive Report Visibility")
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
