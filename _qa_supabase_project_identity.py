"""Actual application authorization/obsolete-route tests after diagnostic removal."""
from unittest.mock import patch


def main():
    import db
    import streamlit as st
    from streamlit.testing.v1 import AppTest
    cases = [({}, False), ({'third_report_authenticated': True, 'third_report_user': 'synthetic'}, False),
             ({'authenticated': True, 'login_user': 'Areti'}, True),
             ({'authenticated': True, 'login_user': 'Other'}, True),
             ({'authenticated': True, 'login_user': 'Areti', 'third_report_authenticated': True}, True)]
    for state, expected_normal in cases:
        def stop_at_normal_startup():
            # Keep this route probe zero-write while executing real auth and app
            # entry. Normal page functionality is covered by the complete suite.
            st.stop()
        with patch.object(db, 'init_db', side_effect=stop_at_normal_startup) as normal, \
             patch.object(db, 'get_connection', side_effect=AssertionError('Retired route queried database')) as connect:
            app = AppTest.from_file('app.py')
            for key, value in state.items():
                app.session_state[key] = value
            app.session_state['read_database_identity'] = True
            app.session_state['open_database_identity'] = True
            app.query_params.update(page='DatabaseIdentity', username='Areti', authenticated='true',
                                    action='read_database_identity', api='database_identity')
            app.run(timeout=30)
            assert not app.exception
            assert not app.table and not app.dataframe
            labels = [b.label.lower() for b in app.button]
            labels += [str(h.value).lower() for h in app.subheader]
            assert not any('database identity' in text or 'project reference' in text for text in labels)
            assert bool(normal.call_count) == expected_normal, 'Unexpected auth/normal-app path'
            if not expected_normal:
                assert any(item.label == 'Password' for item in app.text_input)
            app.run()
            assert not app.exception and not app.table
            connect.assert_not_called()
    print('PASS retired route: Areti/Other/THIRD/anonymous, forged query/widget keys, rerun, real auth, zero diagnostic queries')


if __name__ == '__main__': main()
