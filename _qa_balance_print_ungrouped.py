"""Presentation-only no-grouping checks; synthetic in-memory rows, no SQL."""
from copy import deepcopy
from datetime import datetime
from decimal import Decimal
from html.parser import HTMLParser
import unittest
from latest_import_balances import COLUMNS, NOTE, print_document


class Rows(HTMLParser):
    def __init__(self):
        super().__init__(); self.inside=False; self.count=0
    def handle_starttag(self,tag,attrs):
        if tag=='tbody': self.inside=True
        if self.inside and tag=='tr': self.count+=1
    def handle_endtag(self,tag):
        if tag=='tbody': self.inside=False


class PrintTests(unittest.TestCase):
    def fixture(self):
        row=dict.fromkeys(COLUMNS,'')
        row.update({'Bank':'Synthetic bank','Account name':'Synthetic owner','Account number':'QA-A',
                    'Currency':'USD','Opening balance':Decimal('1000.00'),'Closing balance':Decimal('962.5999755859375'),
                    'Closing balance converted to USD':'NOT AVAILABLE','Statement end date':'2026-09-30',
                    'Transaction Import Date':'2026-07-01 11:00:00 EEST','Status':'IMPORTED',
                    'Verification':'UNVERIFIED STORED PRECISION'})
        return [row,dict(row,**{'Bank':'Other synthetic bank','Account number':'QA-B'})]

    def test_no_group_heading_and_one_data_row_per_account(self):
        rows=self.fixture(); before=deepcopy(rows)
        html=print_document(rows,Decimal(0),['Synthetic precision exclusion'],datetime.fromisoformat('2026-10-19T12:00:00+03:00'))
        parsed=Rows();parsed.feed(html)
        self.assertEqual(parsed.count,len(rows))
        self.assertNotIn('class="group"',html)
        self.assertNotIn('.group th',html)
        self.assertEqual(rows,before)
        for column in COLUMNS:self.assertIn('<th>'+column+'</th>',html)
        for value in ('QA-A','QA-B','962.60',NOTE,'Synthetic precision exclusion','PARTIAL TOTAL USD','color:#000000'):
            self.assertIn(value,html)
        self.assertNotIn('962.5999755859375',html)

    def test_repeat_and_empty_report_preserve_scope_and_totals(self):
        rows=self.fixture(); now=datetime.fromisoformat('2026-10-19T12:00:00+03:00')
        self.assertEqual(print_document(rows,Decimal(0),[],now),print_document(rows,Decimal(0),[],now))
        html=print_document([],Decimal(0),[],now);parsed=Rows();parsed.feed(html)
        self.assertEqual(parsed.count,0);self.assertIn(NOTE,html);self.assertIn('0.00',html)


if __name__=='__main__':unittest.main(verbosity=2)
