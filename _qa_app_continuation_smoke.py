"""Full local Streamlit authentication/navigation against synthetic SQLite only."""
from contextlib import closing
import os
from pathlib import Path
import tempfile
from unittest.mock import patch


def main():
    assert not any(os.getenv(key) for key in ('DATABASE_URL', 'POSTGRES_URL', 'SUPABASE_URL'))
    import db
    from streamlit.testing.v1 import AppTest
    assert not db.USING_POSTGRES
    with tempfile.TemporaryDirectory(dir=os.environ['TEMP']) as folder, \
            patch.object(db, 'DB_PATH', str(Path(folder) / 'app-smoke.sqlite')), \
            patch.dict(os.environ, LOGIN_USERNAME='Areti', LOGIN_PASSWORD='Synthetic-local-QA-only-2026'):
        db.init_db()
        db.add_category('Synthetic', 'General', 'Synthetic')
        with closing(db.get_connection()) as conn:
            conn.execute("INSERT INTO account_list(account_name,bank,account_number,currency,rate_type) VALUES ('Synthetic','Synthetic','QA-APP','USD','USD/USD')")
            conn.execute("INSERT INTO rates(rate_month,rate_type,rate_value) VALUES ('2026-10-01','USD/USD',1)")
            for index, match in enumerate(('exact', 'rule', 'custom')):
                conn.execute("INSERT INTO classified_transactions (row_hash,txn_date,amount,currency,status,reviewed,match_type) VALUES (?, '2026-10-01', -1, 'USD','pending',0,?)", (f'qa-app-{index}',match))
            conn.commit()
        at = AppTest.from_file('app.py', default_timeout=60).run()
        assert not at.exception
        assert [field.label for field in at.text_input] == ['Username', 'Password']
        at.text_input[0].set_value('Areti')
        at.text_input[1].set_value('wrong synthetic password')
        at.button[0].click().run()
        assert at.error and not at.exception
        at.text_input[1].set_value('Synthetic-local-QA-only-2026')
        at.button[0].click().run()
        assert not at.exception
        assert at.session_state['authenticated'] is True
        assert 'Import Statement' in [item.value for item in at.subheader]
        assert not any('Final Precheck' in button.label for button in at.button)
        for page in ('Pending Review', 'Latest Import Balances', 'Import History', 'Setup', 'Corrections'):
            at = AppTest.from_file('app.py', default_timeout=60)
            at.query_params['page'] = page
            at.run()
            at.text_input[0].set_value('Areti')
            at.text_input[1].set_value('Synthetic-local-QA-only-2026')
            at.button[0].click().run()
            assert not at.exception, [(page, item.message) for item in at.exception]
            if page == 'Pending Review':
                values = {metric.label: int(metric.value) for metric in at.metric}
                assert 'All pending backlog' in values, ([item.value for item in at.subheader],
                    [item.value for item in at.info], [item.value for item in at.warning], values)
                assert values['All pending backlog'] == values['Visible rows'] == 3
                assert sum(values[label] for label in ('Exact','Similar','New','Rule','Other')) == 3
                assert values['Rule'] == values['Other'] == 1
            if page == 'Setup':
                assert len(at.get('file_uploader')) == 3
            if page == 'Latest Import Balances':
                from latest_import_balances import NOTE
                assert any(item.value==NOTE for item in at.info)
                assert 'current Setup accounts only' in NOTE and 'not a complete reconciled total' in NOTE
            if page == 'Corrections':
                assert [item.label for item in at.checkbox]==['Load correction controls and current repair status']
                assert at.checkbox[0].value is False
                assert not any('Final Precheck' in button.label for button in at.button)
        with closing(db.get_connection()) as conn:
            assert conn.execute('SELECT COUNT(*),SUM(amount) FROM classified_transactions').fetchone() == (3,-3)
            assert conn.execute('SELECT COUNT(*) FROM statement_imports').fetchone()[0] == 0
        print('PASS full Streamlit invalid/valid local login, normal Setup uploaders without eager repair, Pending complete partition, report/history navigation and financial preservation')


if __name__ == '__main__': main()
