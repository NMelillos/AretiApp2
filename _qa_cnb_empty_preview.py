"""Synthetic CNB zero-activity UI lifecycle; no production/source data."""
from contextlib import closing
from decimal import Decimal
from io import BytesIO
import unittest
from unittest.mock import patch
import db, parsing
from cnb_import import parse_cnb
from latest_import_balances import snapshot
import _qa_post_import_message as completion

TEXT = '''City National Bank
Account #: 987654321
This statement: August 31, 2026
Last statement: July 31, 2026
Account Summary Account Activity
Account number 987654321 Beginning balance (7/31/2026) $123.45
Credits + $0.00
Debits - $0.00
Ending balance (8/31/2026) $123.45
** No activity this statement period **
'''


class CNBEmptyPreviewTests(unittest.TestCase):
    setUp = completion.CompletionTests.setUp
    tearDown = completion.CompletionTests.tearDown
    csv = completion.CompletionTests.csv
    app = completion.CompletionTests.app
    run_app = completion.CompletionTests.run_app
    import_file = completion.CompletionTests.import_file
    counts = completion.CompletionTests.counts
    snapshot = completion.CompletionTests.snapshot

    def prepare(self):
        frame = parse_cnb([TEXT], {'Keywords':'0987654321'})
        self.assertTrue(frame.empty)
        with closing(db.get_connection()) as conn:
            conn.execute("INSERT INTO account_list(account_name,bank,account_number,currency,rate_type) VALUES('Synthetic CNB','CNB','987654321','USD','USD/USD')")
            conn.commit()
        from reportlab.pdfgen.canvas import Canvas
        self.file = BytesIO()
        canvas = Canvas(self.file)
        for index, line in enumerate(TEXT.splitlines()):
            canvas.drawString(40, 800-index*16, line)
        canvas.save()
        self.file.seek(0)
        self.file.name = 'synthetic-empty.pdf'
        return frame

    def test_preview_commit_history_report_and_repeat(self):
        frame = self.prepare()
        with patch.object(parsing, 'parse_pdf', side_effect=lambda _:frame.copy(deep=True)):
            app = self.app(); before = self.snapshot()
            self.run_app(app)
            self.assertFalse(app.error, [e.value for e in app.error])
            self.assertEqual(before, self.snapshot())
            self.import_file(app)
            self.assertFalse(app.error, [e.value for e in app.error])
            self.assertEqual(self.counts(), (0,1,1))
            history = db.get_import_history()
            self.assertEqual(len(history),1)
            self.assertEqual(int(history.iloc[0].transaction_count),0)
            self.assertEqual(history.iloc[0].reconciliation_status,'OK')
            row = next(r for r in snapshot(db)[0] if r['Account number']=='987654321')
            self.assertEqual(Decimal(str(row['Closing balance'])),Decimal('123.45'))
            self.assertEqual(row['Currency'],'USD')
            saved = self.snapshot()
            self.run_app(app)
            self.assertEqual(saved,self.snapshot())
            self.run_app(self.app())
            self.assertEqual(saved,self.snapshot())

    def test_failed_commit_is_rolled_back_and_retryable(self):
        frame = self.prepare()
        with patch.object(parsing, 'parse_pdf', side_effect=lambda _:frame.copy(deep=True)):
            app=self.app();self.run_app(app);before=self.snapshot()
            with patch.object(db,'save_statement_balance',side_effect=ValueError('Synthetic persistence failure')):
                self.import_file(app)
            self.assertTrue(app.error)
            self.assertEqual(before,self.snapshot())
            self.run_app(app)
            self.import_file(app)
            self.assertFalse(app.error,[e.value for e in app.error])
            self.assertEqual(self.counts(),(0,1,1))


if __name__=='__main__':unittest.main(verbosity=2)
