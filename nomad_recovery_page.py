"""Authenticated recovery-only page; financial imports require a deliberate click."""
import hashlib

import streamlit as st

PDF_SHA256 = 'b8413ee856c8bbc14529662297e70f7057ea9b992335c8269c5b43a8f5bf4d0f'
FAILURE = 'Verification did not complete. Writers remain protected. Do not rerun Repair.'


def render_nomad_recovery_page():
    if (st.session_state.get('authenticated') is not True
            or st.session_state.get('login_user') != 'Areti'
            or st.session_state.get('third_report_authenticated')):
        st.error('Authenticated main Areti session required.')
        st.stop()
        return

    st.title('NOMAD Committed Repair Verification')
    st.info('The financial repair is already committed. This page performs independent verification only. It does not rerun Repair.')
    # This helper uses only the standard library and public Git metadata.
    from deployment_identity import release_status
    try:
        release = release_status()
        st.info('Release identity: ' + release['status']
                + '; deployed SHA: ' + (release['deployed_sha'] or 'UNAVAILABLE')
                + '; approved SHA: ' + (release['approved_sha'] or 'UNAVAILABLE'))
    except Exception:
        st.error(FAILURE)
        return

    uploaded = st.file_uploader('Original NOMAD PDF', type=['pdf'], key='nomad_runtime_pdf')
    if uploaded is None:
        return
    content = uploaded.getvalue()
    if not isinstance(content, bytes) or hashlib.sha256(content).hexdigest() != PDF_SHA256:
        st.error(FAILURE)
        return
    confirmed = st.checkbox('I confirm verification of the committed NOMAD repair',
                            key='nomad_recovery_confirm')
    if st.button('Verify committed NOMAD repair and release writers', disabled=not confirmed,
                 key='nomad_recovery_verify') and confirmed is True:
        try:
            from nomad_controlled_repair import verify_and_release
            verify_and_release(content, 'VERIFY COMMITTED NOMAD REPAIR')
            st.success('NOMAD repair independently verified. Normal writers are enabled.')
        except BaseException:
            st.error(FAILURE)
