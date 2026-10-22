"""A successful completion has its own timestamp; a true repeat never fabricates one."""
from contextlib import closing
from datetime import datetime,timezone
from unittest.mock import patch
import unittest
import streamlit as st
import db
from _qa_urgent_duplicate_balances import BalanceTests
from duplicate_balance_import import record
from latest_import_balances import snapshot

class EventTests(BalanceTests):
    def test_successful_completion_has_actual_event_not_transaction_or_statement_date(self):
        path,content,digest,balance=self.original()
        with closing(db.get_connection()) as c:
            c.execute("UPDATE statement_balances SET closing_balance=NULL, imported_at='2026-10-01T08:00:00+00:00',updated_at='2026-10-01T08:00:00+00:00' WHERE statement_hash=?",(digest,));c.commit()
        protected=self.protected()
        old=next(r for r in snapshot(db)[0] if r['Account number']==balance['account_number'])
        class Clock(datetime):
            @classmethod
            def now(cls,tz=None):return datetime(2026,10,22,9,0,tzinfo=timezone.utc)
        with patch('duplicate_balance_import.datetime',Clock):
            self.assertEqual(record(db,content,path.name,'Synthetic operator'),'recorded')
        after=next(r for r in snapshot(db)[0] if r['Account number']==balance['account_number'])
        self.assertTrue(after['Balance Import Date'].startswith('2026-10-22'))
        self.assertEqual(after['Transaction Import Date'],old['Transaction Import Date'])
        self.assertEqual(after['Statement end date'],old['Statement end date'])
        self.assertEqual(self.protected(),protected)
        unchanged=self.snapshot()
        self.assertEqual(record(db,content,path.name,'Synthetic operator'),'already recorded')
        self.assertEqual(self.snapshot(),unchanged)

    def test_complete_repeat_truthful_ui_and_no_new_event(self):
        path,content,digest,_=self.original()
        before=self.snapshot();self.file=self.uploaded_file(path,content)
        app=self.run_app(self.app())
        with patch.object(st,'file_uploader',return_value=self.file):
            app.button(key='upload_balance_prepare').click().run()
            app.checkbox(key='upload_balance_confirm_'+digest).check().run()
            app.button(key='upload_balance_record').click().run()
        self.assertFalse(app.exception)
        self.assertTrue(any('No new balance-import event was created' in m.value for m in app.success))
        self.assertEqual(before,self.snapshot())

if __name__=='__main__':
    suite=unittest.TestSuite(EventTests(n) for n in ('test_successful_completion_has_actual_event_not_transaction_or_statement_date','test_complete_repeat_truthful_ui_and_no_new_event'))
    raise SystemExit(not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful())
