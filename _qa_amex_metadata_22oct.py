"""Exact accessible AMEX PDF + prior CSV proof; disposable-only metadata persistence."""
from pathlib import Path
from io import BytesIO
from contextlib import closing
from decimal import Decimal
from hashlib import sha256
from unittest.mock import patch
import json,os,unittest
import pandas as pd
import db,parsing,streamlit as st
from _qa_post_import_message import CompletionTests
from import_history import commit_statement,completed_history
from latest_import_balances import snapshot
import amex_balance_metadata as amex

class MetadataTests(CompletionTests):
    def sources(self):
        root=Path(os.environ['ARETI_ISOLATION_ROOT'])
        proof=json.loads((root/'source-probe-private.json').read_text(encoding='utf-8'))
        info=next(p for p in proof if p['key']=='amex_accessible')
        p=Path(r'C:/Users/Student/Downloads')/info['name']
        csv_path=Path(r'C:/Users/Student/Downloads/2026.09.11 activity (10).csv')
        return p,p.read_bytes(),csv_path,csv_path.read_bytes(),info

    def seed(self):
        p,pdf,c,csv,info=self.sources()
        state=json.loads((Path(os.environ['ARETI_ISOLATION_ROOT'])/'import-state-private.json').read_text(encoding='utf-8'))
        account=next(a for a in state['accounts'] if a['id']==918)
        with closing(db.get_connection()) as conn:
            ddl=conn.execute("SELECT sql FROM sqlite_master WHERE name='statement_balances'").fetchone()[0]
            conn.execute('DROP TABLE statement_balances');conn.execute(ddl.replace(' REAL',' TEXT'))
            conn.execute('INSERT INTO account_list(id,account_name,bank,account_number,currency,rate_type) VALUES(?,?,?,?,?,?)',tuple(account[k] for k in ('id','account_name','bank','account_number','currency','rate_type')));conn.commit()
        db.init_db()  # Restore existing indexes after the disposable precision fixture.
        frame=parsing.parse_csv(BytesIO(csv));mapped=db.apply_account_and_rates(frame,account)
        self.assertEqual(commit_statement(db,mapped,c.name,sha256(csv).hexdigest(),{},account)[0],len(frame))
        return p,pdf,csv,info,account

    def protected(self):
        with closing(db.get_connection()) as c:
            tables=[r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT IN ('statement_balances','statement_imports','sqlite_sequence')")]
            return {t:c.execute('SELECT * FROM '+t).fetchall() for t in tables}

    def test_source_format_balance_signs_period_and_exact_mapping_proof(self):
        p,pdf,csv,info,account=self.seed()
        self.assertTrue(amex.recognized(pdf))
        source,evidence,matched,event=amex.prepare(db,pdf,csv)
        self.assertEqual(matched['id'],account['id'])
        for k in ('opening_balance','money_in','money_out','closing_balance'):self.assertEqual(source[k],abs(Decimal(info['balance'][k])))
        self.assertEqual(source['period_end'],info['balance']['period_end'])
        self.assertEqual((__import__('datetime').date.fromisoformat(source['period_end'])-__import__('datetime').date.fromisoformat(source['period_start'])).days+1,source['billing_days'])
        billing=Path(r'C:/Users/Student/Downloads/2026-09-11 (2)billing.pdf').read_bytes()
        with self.assertRaisesRegex(ValueError,'Screen Reader'):amex.parse(billing)

    def test_pdf_only_record_preserves_csv_and_has_valid_history_and_three_dates(self):
        p,pdf,csv,_,account=self.seed();before=self.protected()
        with closing(db.get_connection()) as c:
            csv_history=c.execute('SELECT * FROM statement_imports WHERE statement_hash=?',(sha256(csv).hexdigest(),)).fetchone()
            csv_balance=c.execute('SELECT * FROM statement_balances WHERE statement_hash=?',(sha256(csv).hexdigest(),)).fetchone()
        with patch.object(parsing,'parse_pdf',side_effect=AssertionError('AMEX metadata must not parse activity')):
            self.assertEqual(amex.record(db,pdf,p.name,csv,'Synthetic operator'),'recorded')
        self.assertEqual(before,self.protected())
        digest=sha256(pdf).hexdigest()
        with closing(db.get_connection()) as c:
            self.assertEqual(c.execute('SELECT COUNT(*) FROM classified_transactions WHERE statement_hash=?',(digest,)).fetchone()[0],0)
            self.assertEqual(c.execute('SELECT * FROM statement_imports WHERE statement_hash=?',(sha256(csv).hexdigest(),)).fetchone(),csv_history)
            self.assertEqual(c.execute('SELECT * FROM statement_balances WHERE statement_hash=?',(sha256(csv).hexdigest(),)).fetchone(),csv_balance)
        history=completed_history(db,db.get_import_history())
        self.assertEqual(len(history[history.statement_hash==digest]),1)
        self.assertEqual(history.loc[history.statement_hash==digest,'transaction_count'].iloc[0],0)
        rows,_,_=snapshot(db);row=next(r for r in rows if r['Account number']==account['account_number'])
        self.assertEqual(row['Status'],'IMPORTED')
        self.assertNotEqual(row['Balance Import Date'],'—');self.assertNotEqual(row['Transaction Import Date'],'—')
        unchanged=self.snapshot();self.assertEqual(amex.record(db,pdf,p.name,csv,'Synthetic operator'),'already recorded');self.assertEqual(unchanged,self.snapshot())

    def test_missing_csv_wrong_card_wrong_setup_and_conflict_reject(self):
        p,pdf,csv,_,account=self.seed()
        before=self.snapshot()
        with self.assertRaises(ValueError):amex.prepare(db,pdf,csv.replace(b'-76007',b'-99999'))
        self.assertEqual(before,self.snapshot())
        with closing(db.get_connection()) as c:c.execute("UPDATE account_list SET account_number='unrelated' WHERE id=?",(account['id'],));c.commit()
        before=self.snapshot()
        with self.assertRaises(ValueError):amex.prepare(db,pdf,csv)
        self.assertEqual(before,self.snapshot())

    def test_rollback_and_tampered_metadata_fail_closed(self):
        p,pdf,csv,_,account=self.seed();before=self.snapshot()
        with patch.object(db,'save_statement_balance',side_effect=RuntimeError('Synthetic failed balance save')):
            with self.assertRaisesRegex(RuntimeError,'Synthetic'):amex.record(db,pdf,p.name,csv,'Synthetic operator')
        self.assertEqual(before,self.snapshot())
        self.assertEqual(amex.record(db,pdf,p.name,csv,'Synthetic operator'),'recorded')
        with closing(db.get_connection()) as c:c.execute("UPDATE statement_balances SET closing_balance='1' WHERE statement_hash=?",(sha256(pdf).hexdigest(),));c.commit()
        before=self.snapshot()
        with self.assertRaisesRegex(ValueError,'conflicts'):amex.record(db,pdf,p.name,csv,'Synthetic operator')
        self.assertEqual(before,self.snapshot())
        history=completed_history(db,db.get_import_history());self.assertNotIn(sha256(pdf).hexdigest(),history.statement_hash.tolist())

    def test_amex_pdf_route_cannot_offer_pdf_transaction_import(self):
        p,pdf,csv,_,_=self.seed();self.file=BytesIO(pdf);self.file.name=p.name
        before=self.snapshot();app=self.run_app(self.app())
        self.assertFalse(any(b.label=='Import statement' for b in app.button))
        self.assertTrue(any('CSV for transactions' in m.value for m in app.info))
        self.assertEqual(before,self.snapshot())

if __name__=='__main__':
    names=[n for n in MetadataTests.__dict__ if n.startswith('test_')]
    raise SystemExit(not unittest.TextTestRunner(verbosity=2).run(unittest.TestSuite(MetadataTests(n) for n in names)).wasSuccessful())
