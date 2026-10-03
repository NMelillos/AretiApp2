"""Isolated owner UI; opening it performs no diagnostics or database connection."""
import streamlit as st
from ops_auth import AccessDenied, candidate, proof_value, require_owner, unlock, PASSWORD


def render_ops_console():
    try:
        if not candidate(st.session_state): raise AccessDenied()
        proof_value()
    except AccessDenied:
        st.session_state.pop('_ops_result', None)
        st.error('Ops Console unavailable or access denied.')
        return
    try:
        require_owner()
    except AccessDenied:
        st.session_state.pop('_ops_result', None)
        st.info('Owner verification required.')
        with st.form('ops_owner_verification'):
            st.text_input('Owner verification password', type='password', key=PASSWORD)
            st.form_submit_button('Unlock Ops Console', on_click=unlock)
        return

    st.title('ARETI OPS CONSOLE')
    st.caption('Owner-only production diagnostics · READ ONLY')
    st.info('READ ONLY')
    from ops_diagnostics import run
    selected = None
    with st.form('ops_command'):
        command = st.text_input('Ops command', placeholder='status', key='_ops_command')
        if st.form_submit_button('Run diagnostic'): selected = command
    sections = (
        ('SYSTEM', (('Health', 'health'), ('Deployment', 'deployment'), ('Release Identity', 'release-identity'), ('Status', 'status'))),
        ('DATABASE', (('Connection', 'db-connection-test'), ('Financial Schema', 'financial-schema'))),
        ('FINANCIAL CONTROL', (('Writers', 'writer-status'), ('Repair Status', 'repair-status'), ('NOMAD Status', 'nomad-status'))),
        ('DIAGNOSTICS', (('Recent Errors', 'recent-errors'), ('Help', 'help'))),
    )
    for section, choices in sections:
        st.subheader(section)
        columns = st.columns(len(choices))
        for column, (label, name) in zip(columns, choices):
            if column.button(label, key='ops_' + name): selected = name
    if selected is not None:
        try: result = run(selected)
        except AccessDenied:
            st.session_state.pop('_ops_result', None)
            st.error('Ops Console unavailable or access denied.')
            return
        st.session_state['_ops_result'] = result
    result = st.session_state.get('_ops_result')
    if result is not None:
        st.subheader('Diagnostic result')
        st.json(result)
