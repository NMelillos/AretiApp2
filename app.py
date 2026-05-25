from datetime import datetime
import hashlib
import hmac
from html import escape
from io import BytesIO
import os
from pathlib import Path
import re
import zipfile

import pandas as pd
import streamlit as st

from classification import classify_transactions
from db import (
    DB_PATH,
    USING_POSTGRES,
    add_category,
    apply_account_and_rates,
    build_statement_hash,
    dataframe_to_excel_bytes,
    full_reset_database,
    get_accounts,
    get_all_transactions,
    get_categories,
    get_dashboard_counts,
    get_import_history,
    get_memory,
    get_pending_transactions,
    get_rates,
    get_saved_transactions,
    get_statement_account,
    get_statement_balances,
    get_subcategories,
    import_memory_from_excel,
    init_db,
    insert_manual_transaction,
    replace_accounts_from_excel,
    replace_categories_from_excel,
    replace_rates_from_excel,
    reset_runtime_data,
    record_duplicate_statement_attempt,
    save_pending_transactions,
    save_statement_balance,
    save_reviewed_rows,
    statement_balance_exists,
    statement_already_imported,
    update_database_rows,
    update_statement_balance_rows,
)
from parsing import extract_statement_balance, parse_csv, parse_excel, parse_pdf
from reporting import build_pdf_report, build_sample_expenses_report, get_report_groups, safe_filename
from utils import format_currency


st.set_page_config(page_title="Statement Management", layout="wide")

_DB_CACHE_TTL_SECONDS = 90
_db_get_accounts = get_accounts
_db_get_all_transactions = get_all_transactions
_db_get_categories = get_categories
_db_get_dashboard_counts = get_dashboard_counts
_db_get_import_history = get_import_history
_db_get_memory = get_memory
_db_get_pending_transactions = get_pending_transactions
_db_get_rates = get_rates
_db_get_saved_transactions = get_saved_transactions
_db_get_statement_balances = get_statement_balances
_db_get_subcategories = get_subcategories


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
def get_dashboard_counts():
    return _db_get_dashboard_counts()


@st.cache_data(show_spinner=False, ttl=_DB_CACHE_TTL_SECONDS)
def get_import_history():
    return _db_get_import_history()


@st.cache_data(show_spinner=False, ttl=_DB_CACHE_TTL_SECONDS)
def get_memory():
    return _db_get_memory()


@st.cache_data(show_spinner=False, ttl=_DB_CACHE_TTL_SECONDS)
def get_pending_transactions():
    return _db_get_pending_transactions()


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
    div[data-testid="stDataEditor"] [role="gridcell"] {
        white-space: normal !important;
        line-height: 1.35 !important;
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

DEFAULT_LOGIN_SALT = "aretiapp-login-v1"
DEFAULT_LOGIN_PASSWORD_HASH = "99cd9990ece838f798db50d75308cc7f75c4309be343063329772bc8998aad16"


def _hash_password(password, salt):
    return hashlib.pbkdf2_hmac(
        "sha256",
        str(password).encode("utf-8"),
        str(salt).encode("utf-8"),
        120000,
    ).hex()


def _login_is_valid(username, password):
    expected_username = os.getenv("LOGIN_USERNAME", "Areti")
    configured_password = os.getenv("LOGIN_PASSWORD", "")
    configured_hash = os.getenv("LOGIN_PASSWORD_HASH", DEFAULT_LOGIN_PASSWORD_HASH)
    salt = os.getenv("LOGIN_PASSWORD_SALT", DEFAULT_LOGIN_SALT)

    username_ok = hmac.compare_digest(str(username).strip(), expected_username)
    if configured_password:
        password_ok = hmac.compare_digest(str(password), configured_password)
    else:
        password_ok = hmac.compare_digest(_hash_password(password, salt), configured_hash)
    return username_ok and password_ok


def require_login():
    if st.session_state.get("authenticated"):
        return

    st.markdown(
        """
        <style>
        .block-container {
            max-width: 392px !important;
            padding-top: 4.2rem !important;
            padding-bottom: 1.5rem !important;
        }
        .login-shell {
            min-height: 0 !important;
            display: block !important;
            padding-top: 0 !important;
        }
        .login-card {
            width: 100% !important;
            background: transparent !important;
            border: 0 !important;
            border-radius: 0 !important;
            box-shadow: none !important;
            padding: 0 0 12px !important;
        }
        .login-brand {
            gap: 7px !important;
            margin-bottom: 16px !important;
        }
        .login-brand .app-brand-mark {
            width: 34px !important;
            height: 34px !important;
            border-radius: 7px !important;
            font-size: 11px !important;
        }
        .login-title {
            font-size: 20px !important;
            line-height: 1.15 !important;
            font-weight: 740 !important;
        }
        .login-subtitle {
            font-size: 12px !important;
            margin-top: 5px !important;
        }
        div[data-testid="stForm"] {
            background: #ffffff !important;
            border: 1px solid var(--border) !important;
            border-radius: 6px !important;
            box-shadow: 0 4px 16px rgba(15, 23, 42, 0.06) !important;
            padding: 18px 16px 16px !important;
        }
        div[data-testid="stForm"] [data-testid="stVerticalBlock"] {
            gap: 0.55rem !important;
        }
        div[data-testid="stTextInput"] label {
            color: var(--text-main) !important;
            font-size: 13px !important;
        }
        div[data-testid="stTextInput"] div[data-baseweb="input"] {
            min-height: 38px !important;
            background: #ffffff !important;
            border: 1px solid #cbd5e1 !important;
            border-radius: 4px !important;
            box-shadow: none !important;
        }
        div[data-testid="stTextInput"] div[data-baseweb="input"]:focus-within {
            border-color: var(--accent) !important;
            box-shadow: 0 0 0 2px rgba(20, 184, 166, 0.14) !important;
        }
        div[data-testid="stTextInput"] input {
            min-height: 38px !important;
            border-radius: 4px !important;
        }
        div[data-testid="stFormSubmitButton"] button {
            min-height: 38px !important;
            border-radius: 4px !important;
        }
        </style>
        <div class="login-brand">
            <div class="app-brand-mark">SM</div>
            <div>
                <div class="login-title">Statement Management</div>
                <div class="login-subtitle">Secure access</div>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )
    with st.form("login_form", clear_on_submit=False):
        username = st.text_input("Username")
        password = st.text_input("Password", type="password")
        submitted = st.form_submit_button("Sign in", type="primary", use_container_width=True)

    if submitted:
        if _login_is_valid(username, password):
            st.session_state["authenticated"] = True
            st.session_state["login_user"] = str(username).strip()
            st.rerun()
        else:
            st.error("Invalid username or password.")

    st.markdown(
        """
        <div class="login-note">Authorized users only.</div>
        """,
        unsafe_allow_html=True,
    )
    st.stop()


def render_session_line():
    left, right = st.columns([8, 1])
    left.markdown(
        f"<div class=\"session-line\">Signed in as {st.session_state.get('login_user', 'Areti')}</div>",
        unsafe_allow_html=True,
    )
    if right.button("Sign out"):
        st.session_state.pop("authenticated", None)
        st.session_state.pop("login_user", None)
        st.rerun()


@st.cache_resource(show_spinner=False)
def ensure_database_ready():
    init_db()
    return True


require_login()
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
    handle = BytesIO(file_bytes)
    lower_name = file_name.lower()
    if lower_name.endswith(".pdf"):
        return parse_pdf(handle)
    if lower_name.endswith(".xlsx") or lower_name.endswith(".xls"):
        return parse_excel(handle)
    return parse_csv(handle)


@st.cache_data(show_spinner=False)
def parse_statement_balance(file_bytes, file_name):
    handle = BytesIO(file_bytes)
    try:
        return extract_statement_balance(handle, file_name)
    except Exception:
        return {}


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
    out = df.copy()
    subset = [col for col in ["Date", "Amount", "normalized_description"] if col in out.columns]
    out["dup_flag"] = out.duplicated(subset=subset, keep=False) if subset else False
    return out


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


def guess_account_index(file_bytes, file_name, accounts, labels):
    if accounts.empty or not labels:
        return 0
    sample = file_bytes[:250000].decode("latin-1", errors="ignore")
    if file_name.lower().endswith(".pdf"):
        try:
            import pdfplumber

            with pdfplumber.open(BytesIO(file_bytes)) as pdf:
                sample = "\n".join(page.extract_text() or "" for page in pdf.pages[:2]) + "\n" + sample
        except Exception:
            pass
    searchable = f"{file_name} {sample}".upper()
    digits = re.sub(r"\D", "", searchable)

    best_index = 0
    best_score = -1
    for idx, (_, row) in enumerate(accounts.iterrows()):
        score = 0
        account_number = re.sub(r"\D", "", str(row.get("account_number", "")))
        if account_number and account_number in digits:
            score += 10
        elif account_number and len(account_number) >= 4 and account_number[-4:] in digits:
            score += 4
        for field in ["account_name", "bank", "currency"]:
            value = str(row.get(field, "")).strip().upper()
            if value and value in searchable:
                score += 2
        if score > best_score:
            best_index = idx
            best_score = score
    return best_index


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
            "currency": st.column_config.TextColumn("Currency", disabled=True, width="small"),
            "amount": st.column_config.NumberColumn("Statement amount", format="%.2f", disabled=True),
            "amount_usd": st.column_config.NumberColumn("USD amount", format="%.2f", disabled=True),
            "original_description": st.column_config.TextColumn("Full statement description", disabled=True, width="large"),
            "match_type": st.column_config.TextColumn("Match", disabled=True, width="small"),
            "confidence": st.column_config.NumberColumn("Confidence", format="%.2f", disabled=True, width="small"),
            "category": st.column_config.SelectboxColumn("Category", options=[""] + categories, required=False),
            "subcategory": st.column_config.SelectboxColumn(
                "Subcategory",
                options=[""] + subcategories,
                required=False,
            ),
        },
    )


def render_wrapped_descriptions(df):
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
    with st.expander("Full statement descriptions"):
        st.markdown("".join(rows), unsafe_allow_html=True)


def render_manual_transaction_form(categories, subcategories):
    with st.expander("Add manual transaction"):
        if not categories:
            st.info("Load expense categories in Setup before adding manual transactions.")
            return
        manual_accounts = get_accounts()
        manual_labels, manual_lookup = (
            account_options(manual_accounts) if not manual_accounts.empty else ([""], {"": {}})
        )
        with st.form("manual_transaction_form", clear_on_submit=True):
            mc1, mc2, mc3 = st.columns(3)
            manual_date = mc1.date_input("Date")
            manual_account_label = mc2.selectbox("Account", manual_labels)
            manual_amount = mc3.number_input("Amount", value=0.0, step=1.0, format="%.2f")
            manual_description = st.text_input("Full statement description")
            manual_category = st.selectbox("Category", categories)
            manual_subcategory = st.selectbox("Subcategory", [""] + subcategories)
            submitted = st.form_submit_button("Save manual transaction", type="primary")
        if submitted:
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
    today_at = "Today, " + datetime.now().strftime("%d %b %Y, %H:%M")
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
    counts = get_dashboard_counts()
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
    if not categories:
        missing.append("expense categories")
    if accounts.empty:
        missing.append("account details")
    if rates.empty:
        missing.append("monthly rates")
    return missing


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

        labels, lookup = account_options(accounts)
        default_account_index = guess_account_index(file_bytes, uploaded_statement.name, accounts, labels)
        selected_label = st.selectbox("Account", labels, index=default_account_index)
        selected_account = lookup[selected_label]
        balance_info = parse_statement_balance(file_bytes, uploaded_statement.name)

        try:
            progress_slot = st.empty()
            progress_slot.markdown(
                '<div class="import-progress"><span class="import-runner">&#x1F3C3;</span>'
                '<span>Processing statement. Please wait until the preview appears.</span></div>',
                unsafe_allow_html=True,
            )
            parsed = parse_statement(file_bytes, uploaded_statement.name)
            parsed = flag_duplicates(parsed)
            parsed = apply_account_and_rates(parsed, selected_account)
            classified = classify_transactions(parsed, get_memory())
            progress_slot.empty()

            st.success(f"Prepared {len(classified)} transactions for review.")

            c1, c2, c3, c4 = st.columns(4)
            c1.metric("Rows", len(classified))
            c2.metric("Exact matches", int((classified["match_type"] == "exact").sum()))
            c3.metric("Similar matches", int((classified["match_type"] == "similar").sum()))
            c4.metric("Needs review", int((classified["match_type"].isin(["new", "suggestion", "rule"])).sum()))

            if balance_has_values(balance_info):
                currency = balance_info.get("currency") or selected_account.get("currency", "")
                render_summary_strip([
                    ("Opening balance", display_money(balance_info.get("opening_balance"), currency)),
                    ("Money in", display_money(balance_info.get("money_in"), currency)),
                    ("Money out", display_money(balance_info.get("money_out"), currency)),
                    ("Closing balance", display_money(balance_info.get("closing_balance"), currency)),
                ])
                balance_preview = pd.DataFrame([{
                    "Bank": balance_info.get("bank") or selected_account.get("bank", ""),
                    "Account": selected_account.get("account_name", ""),
                    "Account number": balance_info.get("account_number") or selected_account.get("account_number", ""),
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
                "amount_usd",
                "suggested_category",
                "suggested_subcategory",
                "match_type",
                "confidence",
                "dup_flag",
            ]
            st.dataframe(
                classified[[col for col in preview_cols if col in classified.columns]],
                use_container_width=True,
                height=360,
            )

            if st.button("Import to pending review", type="primary"):
                inserted, duplicate_statement = save_pending_transactions(
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
        p1, p2, p3, p4 = st.columns(4)
        p1.metric("Pending rows", len(pending))
        p2.metric("Exact", int((pending["match_type"] == "exact").sum()))
        p3.metric("Similar", int((pending["match_type"] == "similar").sum()))
        p4.metric("New", int((pending["match_type"] == "new").sum()))

        description_filter = st.text_input(
            "Filter by transaction description",
            placeholder="e.g. Wolt",
            key="pending_description_filter",
        )
        pending_view = pending.copy()
        if description_filter:
            pending_view = pending_view[
                pending_view["original_description"].fillna("").astype(str).str.contains(
                    description_filter,
                    case=False,
                    regex=False,
                )
            ].copy()

        if pending_view.empty:
            st.warning("No pending transactions match the current description filter.")
            st.stop()

        render_wrapped_descriptions(pending_view)
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
    all_tx = get_all_transactions()
    categories = get_categories()
    subcategories = get_subcategories()

    if all_tx.empty:
        st.info("No transactions imported yet.")
    else:
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
        ])
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
            "match_type",
        ]
        db_edit = st.data_editor(
            db_view[[col for col in editable_cols if col in db_view.columns]],
            use_container_width=True,
            hide_index=True,
            height=620,
            column_config={
                "id": st.column_config.NumberColumn("ID", disabled=True),
                "status": st.column_config.SelectboxColumn("Status", options=["pending", "reviewed"]),
                "reviewed": st.column_config.CheckboxColumn("Reviewed"),
                "category": st.column_config.SelectboxColumn("Category", options=categories),
                "subcategory": st.column_config.SelectboxColumn("Subcategory", options=[""] + subcategories),
            },
            key="database_editor",
        )
        if st.button("Apply database edits", type="primary"):
            count = update_database_rows(db_edit)
            st.success(f"Updated {count} rows.")
            st.cache_data.clear()
            st.rerun()

        st.download_button(
            "Download filtered database Excel",
            data=dataframe_to_excel_bytes({"Transactions": db_view}),
            file_name="transactions_database_filtered.xlsx",
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
    st.subheader("Sample Expenses Report")
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
        total_usd = filtered_reviewed["amount_usd"].fillna(filtered_reviewed["amount"]).sum()

        r1, r2, r3 = st.columns(3)
        r1.metric("Reviewed rows", len(filtered_reviewed))
        r2.metric("Total USD movement", format_currency(total_usd))
        r3.metric("Categories", filtered_reviewed["category"].replace("", pd.NA).dropna().nunique())

        report_bytes = build_sample_expenses_report(filtered_reviewed, categories_df)
        st.download_button(
            "Download sample expenses report",
            data=report_bytes,
            file_name="sample_expenses_report.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            type="primary",
        )
        report_groups = get_report_groups(categories_df)
        if report_groups:
            pdf_zip = BytesIO()
            with zipfile.ZipFile(pdf_zip, "w", zipfile.ZIP_DEFLATED) as archive:
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

    setup_missing = missing_setup_items()
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

    st.markdown("### Current Setup")
    setup_left, setup_middle, setup_right = st.columns(3)
    with setup_left:
        st.markdown("Categories")
        st.dataframe(get_categories(include_subcategories=True), use_container_width=True, height=260)
        new_category = st.text_input("Add category")
        new_subcategory = st.text_input("Add subcategory")
        if st.button("Add category row"):
            add_category(new_category, new_subcategory)
            st.cache_data.clear()
            st.rerun()
    with setup_middle:
        st.markdown("Accounts")
        st.dataframe(get_accounts(), use_container_width=True, height=330)
    with setup_right:
        st.markdown("Rates")
        st.dataframe(get_rates(), use_container_width=True, height=330)

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
