"""Exact October source and historical September; disposable, read-only report QA."""
from contextlib import closing
from decimal import Decimal as D
from hashlib import sha256
from io import BytesIO
from pathlib import Path
import os
import re
import unittest
from unittest.mock import patch
import db
import latest_import_balances as report
import natalia_wallet_report as authority
import _qa_revolut_sections as persistence_qa
from import_history import commit_statement, cyprus_time
from balance_report_sections import totals, grand_total


class WalletReportTests(unittest.TestCase):
    setUpClass = classmethod(persistence_qa.SectionTests.setUpClass.__func__)
    setUp = persistence_qa.SectionTests.setUp
    snapshot = persistence_qa.SectionTests.snapshot
    preview = persistence_qa.SectionTests.preview
    save = persistence_qa.SectionTests.save

    def seed(self):
        if not db.USING_POSTGRES:
            # Empty disposable fixture only: mirror exact PostgreSQL storage.
            # Production schema and binary-storage eligibility stay unchanged.
            with closing(db.get_connection()) as c:
                schema=c.execute("SELECT sql FROM sqlite_master WHERE name='statement_balances'").fetchone()[0]
                c.execute('DROP TABLE statement_balances')
                c.execute(schema.replace('opening_balance REAL','opening_balance TEXT').replace('closing_balance REAL','closing_balance TEXT'))
                c.commit()
        persistence_qa.SectionTests.seed(self)
        from _qa_revolut_urgent import source, balance, ROOT
        from revolut_account_context import effective_account
        original=source();account=db.get_accounts().iloc[0].to_dict()
        effective=effective_account(original,balance(),account)
        mapped=db.apply_account_and_rates(original,effective)
        self.september=sha256((ROOT/'urgent-revolut-original.pdf').read_bytes()).hexdigest()
        self.assertEqual(commit_statement(db,mapped,'September original.pdf',self.september,balance(),account),(167,False,0))
        self.historical=self.snapshot()

    def imported(self):
        self.assertEqual(self.save(),(122,False,0))
        with closing(db.get_connection()) as connection, closing(connection.cursor()) as c:
            # Deliberately distinct fixture events prove no report-generation date is substituted.
            for currency,hour in (('EUR','09'),('USD','10')):
                c.execute('SELECT statement_hash FROM statement_balances WHERE source=? AND currency=?',(authority.PREFIX+self.digest,currency))
                key=c.fetchone()[0]
                c.execute('UPDATE statement_imports SET imported_at=? WHERE statement_hash=?',(f'2026-11-17T{hour}:00:00+00:00',key))
                stamp=f'2026-11-17T{hour}:01:00+00:00'
                c.execute('UPDATE statement_balances SET imported_at=?,updated_at=? WHERE statement_hash=?',(stamp,stamp,key))
            connection.commit()

    def rows(self):
        result=report.snapshot(db)
        return {r['Currency']:r for r in result[0] if r['Account number']==authority.IBAN},result

    def links(self):
        with closing(db.get_connection()) as c:return authority.report_links(c)

    def test_exact_rows_dates_counts_totals_and_history_preserved(self):
        self.imported();before=self.snapshot();rows,result=self.rows()
        self.assertEqual(set(rows),{'EUR','USD'})
        for currency,values,hour in [('EUR',('6.90','33136.73','22650.96','10492.67'),'09'),('USD',('1073.78','0','549.47','524.31'),'10')]:
            r=rows[currency]
            self.assertEqual(tuple(r[k] for k in ('Opening balance','Credits / money in','Debits / money out','Closing balance')),tuple(map(D,values)))
            self.assertEqual((r['Account name'],r['Bank'],r['Account number']),(authority.OWNER,authority.BANK,authority.IBAN))
            self.assertEqual((r['Statement start date'],r['Statement end date']),('2026-10-01','2026-10-31'))
            self.assertEqual(r['Transaction Import Date'],cyprus_time(f'2026-11-17T{hour}:00:00+00:00'))
            self.assertEqual(r['Balance Import Date'],cyprus_time(f'2026-11-17T{hour}:01:00+00:00'))
        self.assertEqual(rows['USD']['Closing balance converted to USD'],D('524.31'))
        self.assertEqual(rows['EUR']['Closing balance converted to USD'],D('11751.79'))
        self.assertEqual(totals(result[0]),{'Deposits':D('12276.10'),'Debts':D('0')})
        self.assertEqual(grand_total(result[0]),D('12276.10'))
        with closing(db.get_connection()) as connection, closing(connection.cursor()) as c:
            for _,currency,key in self.links():
                c.execute('SELECT transaction_count FROM statement_imports WHERE statement_hash=?',(key,))
                self.assertEqual(c.fetchone()[0],115 if currency=='EUR' else 7)
                c.execute('SELECT COUNT(*) FROM classified_transactions WHERE statement_hash=? AND currency=?',(key,currency))
                self.assertEqual(c.fetchone()[0],115 if currency=='EUR' else 7)
        after=self.snapshot();self.assertEqual(before,after)
        for table,old in self.historical.items():
            self.assertEqual([r for r in after[table] if r[0] in {v[0] for v in old}],old)
        self.assertEqual(len(after['account_list']),1)

    def test_browser_print_and_excel_both_wallet_rows(self):
        self.imported();before=self.snapshot();rows,result=self.rows()
        printed=report.print_document(*result)
        browser=report.screen_document(printed,result[0])
        for text in (printed,browser):
            self.assertIn('10,492.67',text);self.assertIn('524.31',text)
            self.assertEqual(len(re.findall(r'<td[^>]*>'+authority.IBAN+r'</td>',text)),2)
        from openpyxl import load_workbook
        book=load_workbook(BytesIO(report.workbook_bytes(*result)))
        sheet=book['Latest Import Balances'];header=[c.value for c in sheet[1]]
        exported=[r for r in sheet.iter_rows(min_row=2,values_only=True) if r[0]==authority.IBAN]
        self.assertEqual(len(exported),2)
        self.assertEqual({r[header.index('Currency')] for r in exported},{'EUR','USD'})
        self.assertEqual(before,self.snapshot())

    def test_missing_eur_fx_preserves_not_available_and_guard(self):
        self.imported()
        with closing(db.get_connection()) as connection, closing(connection.cursor()) as c:
            c.execute("DELETE FROM rates WHERE rate_type='EUR/USD'");connection.commit()
        before=self.snapshot();rows,result=self.rows()
        self.assertEqual(rows['EUR']['Closing balance converted to USD'],'NOT AVAILABLE')
        self.assertEqual(rows['USD']['Closing balance converted to USD'],D('524.31'))
        self.assertEqual(totals(result[0])['Deposits'],D('524.31'))
        self.assertIsNone(grand_total(result[0]));self.assertEqual(before,self.snapshot())

    def test_invalid_or_missing_evidence_fails_closed(self):
        self.imported();valid=self.links();self.assertEqual(len(valid),2)
        key=next(k for _,currency,k in valid if currency=='USD')
        probes=[('statement_balances','notes','{}'),('statement_balances','source',''),
                ('statement_balances','closing_balance','524.32'),('statement_balances','currency','EUR'),
                ('statement_balances','account_name','Other'),('statement_imports','transaction_count',6),
                ('classified_transactions','amount','-1'),('classified_transactions','currency','EUR'),
                ('classified_transactions','original_description','Changed source description')]
        for table,field,value in probes:
            with self.subTest(table=table,field=field),closing(db.get_connection()) as connection,closing(connection.cursor()) as c:
                c.execute('SELECT id,'+field+' FROM '+table+' WHERE statement_hash=? ORDER BY id',(key,));record_id,old=c.fetchone()
                c.execute('UPDATE '+table+' SET '+field+'=? WHERE id=?',(value,record_id));connection.commit()
                before=self.snapshot()
                self.assertEqual(self.links(),[])
                rows,_=self.rows();self.assertFalse('EUR' in rows and 'USD' in rows)
                self.assertEqual(before,self.snapshot())
                c.execute('UPDATE '+table+' SET '+field+'=? WHERE id=?',(old,record_id));connection.commit()
        self.assertEqual(self.links(),valid)

    def test_conflicting_setup_does_not_create_wallets(self):
        self.imported()
        with closing(db.get_connection()) as connection,closing(connection.cursor()) as c:
            c.execute("INSERT INTO account_list(account_name,bank,account_number,currency) VALUES('Conflicting owner','Revolut',?,'')",(authority.IBAN,));connection.commit()
        before=self.snapshot();self.assertEqual(self.links(),[])
        rows,_=self.rows();self.assertNotIn('USD',rows);self.assertEqual(before,self.snapshot())

    def test_no_wallet_inference_before_committed_october(self):
        before=self.snapshot();self.assertEqual(self.links(),[])
        rows,_=self.rows();self.assertNotIn('USD',rows);self.assertEqual(before,self.snapshot())

    def test_report_is_readonly_and_schema_unchanged(self):
        self.imported();before=self.snapshot();original=db.get_connection
        def readonly():
            connection=original()
            with closing(connection.cursor()) as c:
                c.execute('SET TRANSACTION READ ONLY' if db.USING_POSTGRES else 'PRAGMA query_only=ON')
            return connection
        def schema():
            with closing(original()) as connection,closing(connection.cursor()) as c:
                c.execute("SELECT table_name,column_name,data_type FROM information_schema.columns WHERE table_schema='public' ORDER BY table_name,ordinal_position" if db.USING_POSTGRES else "SELECT type,name,sql FROM sqlite_master ORDER BY type,name")
                return c.fetchall()
        structure=schema()
        with patch.object(db,'get_connection',side_effect=readonly):
            rows,_=self.rows();self.assertEqual(set(rows),{'EUR','USD'})
        self.assertEqual(before,self.snapshot());self.assertEqual(structure,schema())

if __name__=='__main__':unittest.main()
