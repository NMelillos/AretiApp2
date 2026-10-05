"""Local synthetic backlog measurement; not a live-host latency acceptance."""
from contextlib import closing
import os
from pathlib import Path
from statistics import median
from time import perf_counter
import tempfile
from unittest.mock import patch


def main():
    assert not any(os.getenv(key) for key in ('DATABASE_URL','POSTGRES_URL','SUPABASE_URL'))
    import db
    from streamlit.testing.v1 import AppTest
    assert not db.USING_POSTGRES
    with tempfile.TemporaryDirectory(dir=os.environ['TEMP']) as folder, \
            patch.object(db,'DB_PATH',str(Path(folder)/'pending-performance.sqlite')), \
            patch.dict(os.environ,LOGIN_USERNAME='Areti',LOGIN_PASSWORD='Synthetic-local-QA-only-2026'):
        db.init_db()
        db.add_category('Synthetic','General','Synthetic')
        with closing(db.get_connection()) as conn:
            conn.execute("INSERT INTO account_list(account_name,bank,account_number,currency,rate_type) VALUES('Synthetic','Synthetic','QA-PERF','USD','USD/USD')")
            conn.execute("INSERT INTO rates(rate_month,rate_type,rate_value) VALUES('2026-10-01','USD/USD',1)")
            conn.executemany("INSERT INTO classified_transactions(row_hash,txn_date,amount,amount_usd,currency,status,reviewed,match_type) VALUES(?,'2026-10-01',-1,-1,'USD','pending',0,'new')",[(f'qa-perf-{i}',) for i in range(5141)])
            conn.commit()
            before='\n'.join(conn.iterdump())
        timings=[]
        expected=5141-len(set(db._hidden_transaction_ids()) & set(range(1,5142)))
        for _ in range(5):
            with patch.object(db,'get_connection',wraps=db.get_connection) as connect:
                start=perf_counter(); frame=db.get_pending_transactions(); timings.append(perf_counter()-start)
                assert len(frame)==expected and connect.call_count==1
                assert not set(frame['id']) & set(db._hidden_transaction_ids())
        at=AppTest.from_file('app.py',default_timeout=60)
        at.query_params['page']='Pending Review';at.run()
        at.text_input[0].set_value('Areti');at.text_input[1].set_value('Synthetic-local-QA-only-2026')
        with patch.object(db,'get_pending_transactions',wraps=db.get_pending_transactions) as pending_read:
            start=perf_counter();at.button[0].click().run(); elapsed=perf_counter()-start
            assert pending_read.call_count==1,pending_read.call_count
        assert not at.exception and at.session_state['authenticated'] is True
        values={item.label:int(item.value) for item in at.metric}
        assert values['Visible rows']==values['All pending backlog']==values['New']==expected
        with closing(db.get_connection()) as conn:assert '\n'.join(conn.iterdump())==before
        print(f'PASS 5141 stored rows / {expected} visible SQLite pending read: median={median(timings):.3f}s max={max(timings):.3f}s; authenticated Pending render={elapsed:.3f}s; one fresh pending read shared by header/page; protected hidden IDs retained; no writes')
        print('LIMIT: local synthetic timings do not verify production PostgreSQL/network or hosting latency')


if __name__=='__main__':main()
