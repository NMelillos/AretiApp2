"""Read-only report compatibility for explicitly labelled ex-Comerica accounts."""
from contextlib import closing
from decimal import Decimal
import unittest
from _qa_post_import_message import CompletionTests
from latest_import_balances import snapshot
from latest_balances_compact import compact_model,compact_html
import db

class MatchTests(CompletionTests):
    def seed(self,setup_bank='Fifth Third (ex-Comerica)',currency='USD',name='Synthetic migrated'):
        with closing(db.get_connection()) as c:
            schema=c.execute("SELECT sql FROM sqlite_master WHERE name='statement_balances'").fetchone()[0]
            c.execute('DROP TABLE statement_balances')
            c.execute(schema.replace('opening_balance REAL','opening_balance TEXT').replace('closing_balance REAL','closing_balance TEXT'))
            c.execute('INSERT INTO account_list(account_name,bank,account_number,currency,rate_type) VALUES(?,?,?,?,?)',(name,setup_bank,'1892774128',currency,'USD/USD'))
            c.execute("INSERT INTO statement_imports(statement_hash,imported_at,transaction_count) VALUES('synthetic-migration','2026-10-09T10:00:00+00:00',1)")
            c.execute("INSERT INTO statement_balances(statement_hash,account_name,bank,account_number,currency,period_start,period_end,opening_balance,money_in,money_out,closing_balance,source) VALUES('synthetic-migration','Synthetic migrated','Comerica','1892774128','USD','2026-09-01','2026-09-30','100.10',0,'10.05','90.05','Fifth Third labelled sections')")
            c.execute("INSERT INTO classified_transactions(statement_hash,account_name,bank,account_number,currency,original_description,amount) VALUES('synthetic-migration','Synthetic migrated','Comerica','1892774128','USD','Synthetic debit',-10.05)")
            c.commit()

    def test_migrated_bank_matches_exact_existing_balances_without_writes(self):
        self.seed();before=self.snapshot()
        stored=db.get_statement_balances().iloc[0]
        rows,total,warnings=snapshot(db);r=next(r for r in rows if r['Account number']=='1892774128')
        self.assertEqual(r['Status'],'IMPORTED');self.assertEqual(r['Bank'],'Fifth Third (ex-Comerica)')
        for field,col in [('Opening balance','opening_balance'),('Closing balance','closing_balance'),('Credits / money in','money_in'),('Debits / money out','money_out')]:
            self.assertEqual(r[field],Decimal(str(stored[col])))
        self.assertEqual(r['Closing balance converted to USD'],Decimal('90.05'))
        self.assertEqual(total,Decimal('90.05'));self.assertEqual(compact_model(rows)[1],total)
        self.assertIn('90.05',compact_html(rows));self.assertEqual(self.snapshot(),before)

    def test_existing_comerica_unchanged(self):
        self.seed('Comerica');before=self.snapshot();rows,total,_=snapshot(db)
        r=next(r for r in rows if r['Account number']=='1892774128')
        self.assertEqual(r['Verification'],'SOURCE RECONCILIATION NOT VERIFIED')
        self.assertEqual(total,Decimal('90.05'));self.assertEqual(self.snapshot(),before)

    def test_currency_and_account_ownership_guards_preserved(self):
        self.seed(currency='EUR');rows,total,_=snapshot(db)
        r=next(r for r in rows if r['Account number']=='1892774128')
        self.assertEqual(r['Status'],'NO IMPORT');self.assertEqual(total,Decimal(0))
        with closing(db.get_connection()) as c:
            c.execute("UPDATE account_list SET currency='USD',account_name='Different owner' WHERE account_number='1892774128'");c.commit()
        rows,total,_=snapshot(db);r=next(r for r in rows if r['Account number']=='1892774128')
        self.assertEqual(r['Verification'],'SETUP/SOURCE LABEL MISMATCH');self.assertEqual(total,Decimal(0))

    def test_unrelated_bank_and_wrong_account_never_match(self):
        self.seed('Fifth Third Bank')
        rows,total,_=snapshot(db)
        self.assertEqual(next(r for r in rows if r['Account number']=='1892774128')['Status'],'NO IMPORT')
        with closing(db.get_connection()) as c:
            c.execute("UPDATE account_list SET bank='Fifth Third (ex-Comerica)',account_number='1892779999' WHERE account_number='1892774128'");c.commit()
        rows,total,_=snapshot(db)
        self.assertEqual(next(r for r in rows if r['Account number']=='1892779999')['Status'],'NO IMPORT')
        self.assertEqual(total,Decimal(0))

    def test_fx_calculation_and_latest_selection_unchanged(self):
        self.seed()
        with closing(db.get_connection()) as c:
            c.execute("UPDATE account_list SET currency='EUR',rate_type='EUR/USD' WHERE account_number='1892774128'")
            c.execute("UPDATE statement_balances SET currency='EUR'")
            c.execute("UPDATE classified_transactions SET currency='EUR'")
            c.execute("INSERT INTO rates(rate_month,rate_type,rate_value) VALUES('2026-09-01','EUR/USD',2)")
            c.execute("INSERT INTO statement_imports(statement_hash,imported_at,transaction_count) VALUES('older','2026-10-08T10:00:00+00:00',0)")
            c.execute("INSERT INTO statement_balances(statement_hash,account_name,bank,account_number,currency,period_start,period_end,opening_balance,closing_balance) VALUES('older','Synthetic migrated','Comerica','1892774128','EUR','2026-09-01','2026-09-30','20','20')")
            c.commit()
        before=self.snapshot();rows,total,_=snapshot(db)
        r=next(r for r in rows if r['Account number']=='1892774128')
        self.assertEqual(r['Closing balance'],Decimal('90.05'))
        self.assertEqual(r['Closing balance converted to USD'],db._usd_from_amount(Decimal('90.05'),Decimal(2)))
        self.assertEqual(compact_model(rows)[1],total)
        self.assertEqual(self.snapshot(),before)

    def test_duplicate_setup_alias_is_one_ambiguous_identity(self):
        self.seed()
        with closing(db.get_connection()) as c:
            c.execute("INSERT INTO account_list(account_name,bank,account_number,currency,rate_type) VALUES('Synthetic migrated','Comerica','1892774128','USD','USD/USD')");c.commit()
        before=self.snapshot();rows,total,_=snapshot(db)
        found=[r for r in rows if r['Account number']=='1892774128']
        self.assertEqual(len(found),1);self.assertEqual(found[0]['Verification'],'AMBIGUOUS SETUP IDENTITY')
        self.assertEqual(total,Decimal(0));self.assertEqual(self.snapshot(),before)

if __name__=='__main__':
    suite=unittest.TestSuite(MatchTests(n) for n in ('test_migrated_bank_matches_exact_existing_balances_without_writes','test_existing_comerica_unchanged','test_currency_and_account_ownership_guards_preserved','test_duplicate_setup_alias_is_one_ambiguous_identity','test_unrelated_bank_and_wrong_account_never_match','test_fx_calculation_and_latest_selection_unchanged'))
    result=unittest.TextTestRunner(verbosity=2).run(suite)
    raise SystemExit(not result.wasSuccessful())
