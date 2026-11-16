"""Synthetic coverage for itemized other credits and daily-page boundaries."""
from decimal import Decimal
import unittest
from cnb_import import parse_cnb, CNBParseError
from _qa_cnb_import import TEXT


def pages():
    text = TEXT.replace('Other credits(0) + 0.00', 'Other credits(1) + 25.00')
    text = text.replace('Total credits +$1,000.00', 'Total credits +$1,025.00')
    text = text.replace('Ending balance (8/31/2026) $250.00', 'Ending balance (8/31/2026) $275.00')
    text = text.replace('ELECTRONIC DEBITS\n', 'OTHER CREDITS\nDate Description Reference Credits\n8-20 Transfer Credit SAVINGS 25.00\nELECTRONIC DEBITS\n')
    first, daily = text.split('DAILY BALANCES', 1)
    return [first.rstrip(), 'Synthetic Customer Page 2\nAugust 31, 2026 Account #: 987654321\nDAILY BALANCES' + daily]


class OtherCreditsTests(unittest.TestCase):
    def test_valid_itemized_credit_and_continuation(self):
        result = parse_cnb(pages(), {'Keywords':'0987654321'})
        self.assertEqual(len(result),4)
        self.assertEqual([Decimal(str(v)) for v in result.Amount],list(map(Decimal,['1000','25','-600','-150'])))
        balance=result.attrs['statement_balance']
        self.assertEqual(balance['money_in'],Decimal('1025'))
        self.assertEqual(balance['opening_balance']+balance['money_in']-balance['money_out'],balance['closing_balance'])

    def test_missing_mismatched_or_duplicate_credit_rejected(self):
        for old,new in [('8-20 Transfer Credit SAVINGS 25.00',''),('SAVINGS 25.00','SAVINGS 24.00'),('Other credits(1)','Other credits(2)'),('OTHER CREDITS\n','OTHER CREDITS\nOTHER CREDITS\n')]:
            with self.subTest(old=old),self.assertRaises(CNBParseError):
                parse_cnb([p.replace(old,new) for p in pages()],{})

    def test_wrong_continuation_identity_or_period_rejected(self):
        for old,new in [('Account #: 987654321','Account #: 987654322'),('August 31, 2026','August 30, 2026')]:
            source=pages();source[1]=source[1].replace(old,new)
            with self.subTest(old=old),self.assertRaises(CNBParseError):parse_cnb(source,{})

    def test_unitemized_other_debits_still_rejected(self):
        with self.assertRaises(CNBParseError):
            parse_cnb([p.replace('Other debits (0) - 0.00','Other debits (1) - 1.00') for p in pages()],{})


if __name__=='__main__':unittest.main(verbosity=2)
