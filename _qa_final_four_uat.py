"""Original-source liability corrections and truthful dates; disposable DB only."""
import ast
import json
import os
import re
import unittest
from contextlib import closing
from datetime import datetime, timezone
from decimal import Decimal
from hashlib import sha256
from pathlib import Path
from unittest.mock import patch

import db
import amex_balance_metadata as amex
import flexline_balance_metadata as loc
from duplicate_balance_import import record
from import_history import cyprus_time
from latest_import_balances import snapshot, screen_document, print_document
from latest_balances_compact import compact_html, compact_model
from _qa_uat_followup_20261026 import NataliaTests, FlexlineTests
from _qa_amex_metadata_22oct import MetadataTests

BASE = Path(__file__).resolve().parent.parent/'areti-uat-three-flows-20261026'
OLD = '2026-10-12T13:27:37+00:00'
TODAY = datetime(2026, 10, 26, 19, 0, tzinfo=timezone.utc)


class NataliaDateTests(NataliaTests):
    def dated(self, missing=False):
        path, content, account, balance, digest = self.seed_natalia()
        with closing(db.get_connection()) as c:
            c.execute('UPDATE statement_imports SET imported_at=? WHERE statement_hash=?',(OLD,digest))
            c.execute('UPDATE statement_balances SET imported_at=?,updated_at=? WHERE statement_hash=?',(OLD,OLD,digest))
            if missing:c.execute('UPDATE statement_balances SET closing_balance=NULL WHERE statement_hash=?',(digest,))
            c.commit()
        return path,content,account,balance,digest

    def test_historical_completion_report_link_and_repeat_keep_actual_date(self):
        path, content, account, _, digest = self.dated()
        protected = self.protected()
        with patch('duplicate_balance_import.datetime') as clock:
            clock.now.return_value=TODAY
            self.assertEqual(record(db,content,path.name,'Synthetic reviewer'),'linked')
        row=next(r for r in snapshot(db)[0] if r['Account number']==account['account_number'])
        self.assertEqual(row['Balance Import Date'],cyprus_time(OLD))
        self.assertEqual(row['Transaction Import Date'],cyprus_time(OLD))
        self.assertEqual(row['Statement end date'],'2026-09-30')
        self.assertEqual(protected,self.protected())
        frozen=self.snapshot()
        self.assertEqual(record(db,content,path.name,'Synthetic reviewer'),'already recorded')
        self.assertEqual(frozen,self.snapshot())

    def test_real_completion_uses_real_event_not_transaction_or_link_date(self):
        path,content,account,_,digest=self.dated(missing=True)
        protected=self.protected()
        with patch('duplicate_balance_import.datetime') as clock:
            clock.now.return_value=TODAY
            self.assertEqual(record(db,content,path.name,'Synthetic reviewer'),'recorded')
        row=next(r for r in snapshot(db)[0] if r['Account number']==account['account_number'])
        self.assertEqual(row['Balance Import Date'],cyprus_time(TODAY.isoformat()))
        self.assertEqual(row['Transaction Import Date'],cyprus_time(OLD))
        self.assertEqual(protected,self.protected())
        frozen=self.snapshot()
        self.assertEqual(record(db,content,path.name,'Synthetic reviewer'),'already recorded')
        self.assertEqual(frozen,self.snapshot())


class LocLiabilityTests(FlexlineTests):
    def test_original_and_explicit_negative_source_have_same_negative_liability(self):
        _,content=self.source()
        self.assertEqual(loc.parse(content)['closing_balance'],Decimal('-771915.46'))
        text=loc.first_page(content)
        signed=re.sub(r'(NEW\s*BALANCE\s+)771,915.46',r'\g<1>-771,915.46',text)
        self.assertNotEqual(signed,text)
        with patch.object(loc,'first_page',return_value=signed):
            self.assertEqual(loc.parse(content)['closing_balance'],Decimal('-771915.46'))

    def test_legacy_positive_balance_is_not_rewritten(self):
        path,content=self.source();self.seed()
        loc.record(db,content,path.name,'Synthetic reviewer')
        with closing(db.get_connection()) as c:
            c.execute('UPDATE statement_balances SET closing_balance=771915.46 WHERE statement_hash=?',(sha256(content).hexdigest(),));c.commit()
        frozen=self.snapshot()
        with self.assertRaisesRegex(ValueError,'conflicts'):loc.record(db,content,path.name,'Synthetic reviewer')
        self.assertEqual(frozen,self.snapshot())


class AmexLiabilityTests(MetadataTests):
    def test_original_signed_reconciliation_report_and_csv_preservation(self):
        path,pdf,csv,_,account=self.seed();protected=self.protected()
        source,evidence,matched,_=amex.prepare(db,pdf,csv)
        self.assertEqual(matched['id'],account['id'])
        self.assertEqual(source['opening_balance'],Decimal('-1036.93'))
        self.assertEqual(source['money_in'],Decimal('14214.43'))
        self.assertEqual(source['money_out'],Decimal('-16138.07'))
        self.assertEqual(source['closing_balance'],Decimal('-2960.57'))
        self.assertEqual(source['opening_balance']+source['money_in']+source['money_out'],source['closing_balance'])
        self.assertEqual(amex.record(db,pdf,path.name,csv,'Synthetic reviewer'),'recorded')
        row=next(r for r in snapshot(db)[0] if r['Account number']==account['account_number'])
        self.assertEqual(row['Closing balance'],Decimal('-2960.57'))
        self.assertEqual(row['Closing balance converted to USD'],Decimal('-2960.57'))
        from balance_report_sections import totals
        self.assertEqual(totals([row])['Debts'],Decimal('-2960.57'))
        self.assertEqual(self.protected(),protected)
        frozen=self.snapshot()
        self.assertEqual(amex.record(db,pdf,path.name,csv,'Synthetic reviewer'),'already recorded')
        self.assertEqual(frozen,self.snapshot())

    def test_already_signed_source_not_double_negated_and_inconsistent_source_rejects(self):
        _,pdf,_,_,_=self.seed()
        pages=amex.texts(pdf)
        # Same labelled summary, synthetic fully signed liability representation.
        pattern=r'((?:Previous Balance|Plus New Charges|Plus Fees|Plus Interest Charged|Equals New Balance|New Balance) \$)([\d,]+\.\d{2})'
        signed=[re.sub(pattern,r'\g<1>-\2',page) for page in pages]
        with patch.object(amex,'texts',return_value=signed):
            self.assertEqual(amex.parse(pdf)['closing_balance'],Decimal('-2960.57'))
        bad=[page.replace('Equals New Balance $2,960.57','Equals New Balance $2,960.58') for page in pages]
        with patch.object(amex,'texts',return_value=bad):
            with self.assertRaisesRegex(ValueError,'reconcile'):amex.parse(pdf)

    def test_existing_positive_metadata_never_silently_repaired(self):
        path,pdf,csv,_,_=self.seed()
        amex.record(db,pdf,path.name,csv,'Synthetic reviewer')
        with closing(db.get_connection()) as c:
            c.execute('UPDATE statement_balances SET opening_balance=-opening_balance,money_in=-money_in,money_out=-money_out,closing_balance=-closing_balance WHERE statement_hash=?',(sha256(pdf).hexdigest(),));c.commit()
        frozen=self.snapshot()
        with self.assertRaisesRegex(ValueError,'conflicts'):amex.record(db,pdf,path.name,csv,'Synthetic reviewer')
        self.assertEqual(frozen,self.snapshot())


class LayoutProtectionTests(unittest.TestCase):
    def functions(self,file):
        return {n.name:ast.dump(n) for n in ast.parse(file.read_text(encoding='utf-8-sig')).body if isinstance(n,ast.FunctionDef)}

    def test_only_presentation_functions_changed_in_report_modules(self):
        here=Path(__file__).resolve().parent
        for module,allowed in (('latest_import_balances.py',{'screen_document','render'}),('latest_balances_compact.py',{'_section_html'})):
            before,after=self.functions(BASE/module),self.functions(here/module)
            for name in before:
                if name not in allowed:self.assertEqual(before[name],after[name],(module,name))
        self.assertEqual((BASE/'balance_report_sections.py').read_bytes(),(here/'balance_report_sections.py').read_bytes())
        for name in ('prepare','record'):
            self.assertEqual(self.functions(BASE/'duplicate_balance_import.py')[name],self.functions(here/'duplicate_balance_import.py')[name])

    def test_identical_table_cells_headers_sections_and_totals_reduced_padding(self):
        rows=[]
        for bank,number,value in (('BOC','123',Decimal('123.45')),('AMEX','456',Decimal('-2960.57'))):
            rows.append({'Bank':bank,'Account name':'Synthetic account','Account number':number,'Currency':'USD','Closing balance':value,'Closing balance converted to USD':value,'Status':'IMPORTED','Verification':'SOURCE RECONCILIATION NOT VERIFIED','Transaction Import Date':cyprus_time(OLD),'Balance Import Date':cyprus_time(OLD),'Statement end date':'2026-09-30'})
        # Load the pinned baseline presentation module without any application startup.
        import importlib.util
        spec=importlib.util.spec_from_file_location('baseline_compact',BASE/'latest_balances_compact.py')
        old=importlib.util.module_from_spec(spec);spec.loader.exec_module(old)
        frozen=json.dumps(rows,default=str,sort_keys=True)
        when=TODAY
        before,after=old.compact_html(rows,when),compact_html(rows,when)
        # Word boundary excludes <thead>; compare cell content, not CSS attrs.
        cells=lambda html:[text.replace('<br>',' ') for text in re.findall(r'<(?:th|td)\b[^>]*>(.*?)</(?:th|td)>',html)]
        self.assertEqual(cells(before),cells(after))
        for section in ('Deposits','Debts'):
            self.assertEqual(old.compact_model(rows,section),compact_model(rows,section))
        self.assertEqual(frozen,json.dumps(rows,default=str,sort_keys=True))
        self.assertIn('padding:1px 2px',after)
        self.assertIn('max-width:82px',after)
        self.assertIn('white-space:normal',after)
        self.assertIn('Transaction<br>Import Date',after)
        runtime=Path(os.environ['ARETI_ISOLATION_ROOT'])
        for name,html in (('layout-before.html',before),('layout-after.html',after)):
            (runtime/name).write_text('<html><body>'+html+'</body></html>',encoding='utf-8')


if __name__=='__main__':
    classes=(NataliaDateTests,LocLiabilityTests,AmexLiabilityTests,LayoutProtectionTests)
    suite=unittest.TestSuite(cls(n) for cls in classes for n in cls.__dict__ if n.startswith('test_'))
    result=unittest.TextTestRunner(verbosity=2).run(suite)
    Path(os.environ['ARETI_ISOLATION_ROOT'],'final-four-counts.json').write_text(json.dumps(dict(tests=result.testsRun,failures=len(result.failures),errors=len(result.errors),skips=len(result.skipped))))
    raise SystemExit(not result.wasSuccessful())
