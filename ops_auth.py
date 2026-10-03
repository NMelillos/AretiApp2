"""Owner step-up: the existing main password is shared between login aliases."""
import hashlib
import hmac
import os
import re
import time

PROOF = '_ops_owner_proof'
PASSWORD = '_ops_owner_password'
ATTEMPTS = '_ops_owner_attempts'
HASH = re.compile(r'pbkdf2_sha256\$600000\$([0-9a-f]{32})\$([0-9a-f]{64})')


class AccessDenied(ValueError):
    def __init__(self): super().__init__('Ops Console unavailable or access denied.')


def candidate(state):
    owner = os.getenv('OPS_OWNER_USERNAME', '')
    return bool(owner and state.get('authenticated') is True
                and state.get('login_user') == owner and not state.get('third_report_authenticated'))


def proof_value():
    owner = os.getenv('OPS_OWNER_USERNAME', '')
    encoded = os.getenv('OPS_OWNER_PASSWORD_HASH', '')
    if not owner or not HASH.fullmatch(encoded): raise AccessDenied()
    return hashlib.sha256((owner + '\0' + encoded).encode()).hexdigest()


def require_owner(state=None):
    if state is None:
        import streamlit as st
        state = st.session_state
    if not candidate(state): raise AccessDenied()
    expected = proof_value()
    claimed = state.get(PROOF)
    if not isinstance(claimed, str) or not hmac.compare_digest(claimed, expected): raise AccessDenied()


def unlock():
    import streamlit as st
    state = st.session_state
    password = state.pop(PASSWORD, '')
    state.pop(PROOF, None)
    try:
        if not candidate(state): raise AccessDenied()
        proof = proof_value()
        now = time.monotonic()
        attempts = [t for t in state.get(ATTEMPTS, []) if now - t < 300]
        state[ATTEMPTS] = attempts
        if len(attempts) >= 5: raise AccessDenied()
        attempts.append(now)
        match = HASH.fullmatch(os.environ['OPS_OWNER_PASSWORD_HASH'])
        actual = hashlib.pbkdf2_hmac('sha256', password.encode(), bytes.fromhex(match[1]), 600000).hex()
        if not hmac.compare_digest(actual, match[2]): raise AccessDenied()
        state[PROOF] = proof
        state.pop(ATTEMPTS, None)
    except Exception:
        # No password, hash or exception details enter UI/logs.
        pass
