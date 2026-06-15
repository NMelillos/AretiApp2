import hashlib
import hmac
import os

import streamlit as st


DEFAULT_USERNAME = "Areti"
DEFAULT_PASSWORD_SALT = "aretiapp-login-v1"
DEFAULT_PASSWORD_HASH = "05bad6fd99f80a37b8277d0d66ba0d38415fcf3b2cbd194be8855349ec48121b"
RETIRED_PASSWORD_HASHES = {
    "99cd9990ece838f798db50d75308cc7f75c4309be343063329772bc8998aad16",
}
AUTH_BUILD_MARKER = "auth-rotated-20260606"


def _hash_password(password, salt):
    return hashlib.pbkdf2_hmac(
        "sha256",
        str(password).encode("utf-8"),
        str(salt).encode("utf-8"),
        120000,
    ).hex()


def _password_candidates(password):
    raw = str(password)
    normalized = raw
    for invisible in ("\ufeff", "\u200b", "\u200c", "\u200d"):
        normalized = normalized.replace(invisible, "")
    trimmed = normalized.strip()
    candidates = [raw, normalized, trimmed]
    return list(dict.fromkeys(candidates))


def _candidate_is_retired(candidate, configured_salt):
    salts = [DEFAULT_PASSWORD_SALT]
    if configured_salt and configured_salt != DEFAULT_PASSWORD_SALT:
        salts.append(configured_salt)
    return any(
        hmac.compare_digest(_hash_password(candidate, salt), retired_hash)
        for salt in salts
        for retired_hash in RETIRED_PASSWORD_HASHES
    )


def _username_matches(username):
    entered = str(username).strip().casefold()
    configured = os.getenv("LOGIN_USERNAME", "").strip()
    allowed = [DEFAULT_USERNAME]
    if configured:
        allowed.insert(0, configured)
    return any(hmac.compare_digest(entered, candidate.casefold()) for candidate in allowed)


def _password_matches(password):
    candidates = _password_candidates(password)
    configured_password = os.getenv("LOGIN_PASSWORD", "")
    configured_hash = os.getenv("LOGIN_PASSWORD_HASH", "")
    configured_salt = os.getenv("LOGIN_PASSWORD_SALT", DEFAULT_PASSWORD_SALT)

    if any(_candidate_is_retired(candidate, configured_salt) for candidate in candidates):
        return False

    if configured_password:
        for candidate in candidates:
            if hmac.compare_digest(candidate, configured_password):
                return True
            if hmac.compare_digest(candidate, configured_password.strip()):
                return True

    if configured_hash:
        for candidate in candidates:
            if hmac.compare_digest(_hash_password(candidate, configured_salt), configured_hash):
                return True

    return any(
        hmac.compare_digest(_hash_password(candidate, DEFAULT_PASSWORD_SALT), DEFAULT_PASSWORD_HASH)
        for candidate in candidates
    )


def credentials_are_valid(username, password):
    return _username_matches(username) and _password_matches(password)


def _specific_username_matches(username, configured_username):
    configured = str(configured_username or "").strip()
    if not configured:
        return False
    return hmac.compare_digest(str(username).strip().casefold(), configured.casefold())


def credentials_are_valid_for_env(
    username,
    password,
    username_env,
    password_env,
    password_hash_env,
    salt_env,
):
    configured_username = os.getenv(username_env, "")
    if not _specific_username_matches(username, configured_username):
        return False

    candidates = _password_candidates(password)
    configured_password = os.getenv(password_env, "")
    configured_hash = os.getenv(password_hash_env, "")
    configured_salt = os.getenv(salt_env, DEFAULT_PASSWORD_SALT)

    if configured_password:
        for candidate in candidates:
            if hmac.compare_digest(candidate, configured_password):
                return True
            if hmac.compare_digest(candidate, configured_password.strip()):
                return True

    if configured_hash:
        for candidate in candidates:
            if hmac.compare_digest(_hash_password(candidate, configured_salt), configured_hash):
                return True

    return False


def get_login_user():
    return st.session_state.get("login_user", DEFAULT_USERNAME)


def get_third_report_user():
    return st.session_state.get("third_report_user", os.getenv("THIRD_REPORT_USERNAME", ""))


def sign_out():
    for key in ["authenticated", "login_user", "login_error", "login_username", "login_password"]:
        st.session_state.pop(key, None)


def sign_out_third_report():
    for key in [
        "third_report_authenticated",
        "third_report_user",
        "third_report_error",
        "third_report_username",
        "third_report_password",
    ]:
        st.session_state.pop(key, None)


def require_third_report_login():
    if st.session_state.get("third_report_authenticated"):
        return

    configured_username = os.getenv("THIRD_REPORT_USERNAME", "").strip()
    has_password = bool(os.getenv("THIRD_REPORT_PASSWORD", "") or os.getenv("THIRD_REPORT_PASSWORD_HASH", ""))

    st.markdown(
        """
        <style>
        .stApp { background: #edf2f7; color: #172033; }
        #MainMenu, footer, header, .stDeployButton,
        [data-testid="stToolbar"], [data-testid="stDecoration"],
        [data-testid="stStatusWidget"], [data-testid="manage-app-button"] {
            display: none !important;
            visibility: hidden !important;
            height: 0 !important;
        }
        .block-container {
            max-width: 392px !important;
            padding-top: 4.2rem !important;
            padding-bottom: 1.5rem !important;
        }
        .login-brand {
            display: grid;
            justify-items: center;
            gap: 7px;
            margin-bottom: 16px;
            text-align: center;
        }
        .login-mark {
            width: 34px;
            height: 34px;
            border-radius: 7px;
            display: grid;
            place-items: center;
            background: #4c1d95;
            color: #ffffff;
            font-size: 11px;
            font-weight: 800;
            letter-spacing: 0;
        }
        .login-title {
            color: #4c1d95;
            font-size: 20px;
            line-height: 1.15;
            font-weight: 740;
        }
        .login-subtitle,
        .login-note {
            color: #64748b;
            font-size: 12px;
        }
        .login-note {
            margin-top: 10px;
            line-height: 1.45;
            text-align: center;
        }
        div[data-testid="stTextInput"] label {
            color: #172033 !important;
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
            border-color: #7c3aed !important;
            box-shadow: 0 0 0 2px rgba(124, 58, 237, 0.14) !important;
        }
        div[data-testid="stButton"] button {
            min-height: 38px !important;
            border-radius: 4px !important;
        }
        div[data-testid="stButton"] button[kind="primary"] {
            background: #6d28d9 !important;
            border-color: #6d28d9 !important;
        }
        </style>
        <div class="login-brand">
            <div class="login-mark">TB</div>
            <div>
                <div class="login-title">TB & NF Family Office Expenses Report</div>
                <div class="login-subtitle">Secure report access</div>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    if not configured_username or not has_password:
        st.error("This report login is not configured yet. Set THIRD_REPORT_USERNAME and THIRD_REPORT_PASSWORD in deployment secrets.")
        st.stop()

    username = st.text_input("Username", key="third_report_username")
    password = st.text_input("Password", type="password", key="third_report_password")
    submitted = st.button("Sign in", type="primary", use_container_width=True)

    if submitted:
        if credentials_are_valid_for_env(
            username,
            password,
            "THIRD_REPORT_USERNAME",
            "THIRD_REPORT_PASSWORD",
            "THIRD_REPORT_PASSWORD_HASH",
            "THIRD_REPORT_PASSWORD_SALT",
        ):
            st.session_state["third_report_authenticated"] = True
            st.session_state["third_report_user"] = str(username).strip()
            st.session_state.pop("third_report_error", None)
            st.rerun()
        else:
            st.session_state["third_report_error"] = "Invalid username or password."

    if st.session_state.get("third_report_error"):
        st.error(st.session_state["third_report_error"])

    st.markdown('<div class="login-note">Authorized users only.</div>', unsafe_allow_html=True)
    st.stop()


def require_login():
    if st.session_state.get("authenticated"):
        return

    st.markdown(
        f"""
        <style>
        .stApp {{ background: #edf2f7; color: #172033; }}
        #MainMenu, footer, header, .stDeployButton,
        [data-testid="stToolbar"], [data-testid="stDecoration"],
        [data-testid="stStatusWidget"], [data-testid="manage-app-button"] {{
            display: none !important;
            visibility: hidden !important;
            height: 0 !important;
        }}
        .block-container {{
            max-width: 392px !important;
            padding-top: 4.2rem !important;
            padding-bottom: 1.5rem !important;
        }}
        .login-brand {{
            display: grid;
            justify-items: center;
            gap: 7px;
            margin-bottom: 16px;
            text-align: center;
        }}
        .login-mark {{
            width: 34px;
            height: 34px;
            border-radius: 7px;
            display: grid;
            place-items: center;
            background: #111c3d;
            color: #ffffff;
            font-size: 11px;
            font-weight: 800;
            letter-spacing: 0;
        }}
        .login-title {{
            color: #172033;
            font-size: 20px;
            line-height: 1.15;
            font-weight: 740;
        }}
        .login-subtitle,
        .login-note {{
            color: #64748b;
            font-size: 12px;
        }}
        .login-note {{
            margin-top: 10px;
            line-height: 1.45;
            text-align: center;
        }}
        div[data-testid="stForm"] {{
            border: 0 !important;
            padding: 0 !important;
            background: transparent !important;
        }}
        div[data-testid="stTextInput"] label {{
            color: #172033 !important;
            font-size: 13px !important;
        }}
        div[data-testid="stTextInput"] div[data-baseweb="input"] {{
            min-height: 38px !important;
            background: #ffffff !important;
            border: 1px solid #cbd5e1 !important;
            border-radius: 4px !important;
            box-shadow: none !important;
        }}
        div[data-testid="stTextInput"] div[data-baseweb="input"]:focus-within {{
            border-color: #0f766e !important;
            box-shadow: 0 0 0 2px rgba(20, 184, 166, 0.14) !important;
        }}
        div[data-testid="stTextInput"] input,
        div[data-testid="stFormSubmitButton"] button,
        div[data-testid="stButton"] button {{
            min-height: 38px !important;
            border-radius: 4px !important;
        }}
        div[data-testid="stFormSubmitButton"] button[kind="primary"],
        div[data-testid="stButton"] button[kind="primary"] {{
            background: #0f766e !important;
            border-color: #0f766e !important;
        }}
        </style>
        <div class="login-brand">
            <span data-login-build="{AUTH_BUILD_MARKER}" style="display:none"></span>
            <div class="login-mark">SM</div>
            <div>
                <div class="login-title">Statement Management</div>
                <div class="login-subtitle">Secure access</div>
            </div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    username = st.text_input("Username", key="login_username")
    password = st.text_input("Password", type="password", key="login_password")
    submitted = st.button("Sign in", type="primary", use_container_width=True)

    if submitted:
        if credentials_are_valid(username, password):
            st.session_state["authenticated"] = True
            st.session_state["login_user"] = str(username).strip() or DEFAULT_USERNAME
            st.session_state.pop("login_error", None)
            st.rerun()
        else:
            st.session_state["login_error"] = "Invalid username or password."

    if st.session_state.get("login_error"):
        st.error(st.session_state["login_error"])

    st.markdown('<div class="login-note">Authorized users only.</div>', unsafe_allow_html=True)
    st.stop()
