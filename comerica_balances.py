"""Validated balances for Comerica's electronic-withdrawals-only summary."""
from datetime import datetime
from decimal import Decimal
import re


class ComericaBalanceError(ValueError):
    pass


def withdrawals_only_balance(panel, text, rows, metadata):
    money = r'-?\$\d[\d,]*\.\d{2}'
    date = r'[A-Za-z]+\s*\d{1,2},\s*\d{4}'
    summary = re.search(
        rf'Account\s*summary\s+Beginning\s*balance\s+on\s*(?P<start>{date})\s+'
        rf'(?P<opening>{money})\s+Less\s*withdrawals\s+'
        rf'Electronic\s*\(EFT\)\s*withdrawals\s+(?P<out>{money})\s+'
        rf'Ending\s*balance\s+(?:on\s*(?P<end_before>{date})\s+(?P<close_after>{money})'
        rf'|(?P<close_before>{money})\s+on\s*(?P<end_after>{date}))', panel, re.I)
    if summary is None:
        return None  # Other layouts retain their existing balance path.
    number = lambda value: Decimal(value.replace('$', '').replace(',', ''))
    iso_date = lambda value: datetime.strptime(re.sub(r'([A-Za-z]+)\s*(\d)', r'\1 \2', value).replace(',', ' '), '%B %d %Y').date().isoformat()
    opening = number(summary['opening'])
    closing = number(summary['close_after'] or summary['close_before'])
    out = number(summary['out'])
    start, end = iso_date(summary['start']), iso_date(summary['end_before'] or summary['end_after'])
    identities = set(re.findall(r'Account\s*number\s*(\d{6,})', text, re.I))
    counts = re.findall(r'Total\s*Number\s*of\s*Electronic\s*Withdrawals:\s*(\d+)', text, re.I)
    totals = re.findall(rf'Total\s*Electronic\s*Withdrawals:\s*({money})', text, re.I)
    amount = sum((row[2] for row in rows), Decimal(0))
    if (identities != {metadata['account_number']} or metadata['currency'] != 'USD'
            or (start, end) != (metadata['period_start'], metadata['period_end']) or start > end
            or len(counts) != 1 or int(counts[0]) != len(rows) or not rows
            or len(totals) != 1 or number(totals[0]) != out or out >= 0
            or any(row[2] >= 0 or not start <= row[0] <= end for row in rows)
            or amount != out or opening + out != closing):
        raise ComericaBalanceError('Comerica withdrawal-only identity/count/total or balance reconciliation failed.')
    # The complete summary contains only EFT withdrawals. Its source count,
    # source total and existing parsed rows account for every balance movement.
    return dict(metadata, opening_balance=opening, money_out=out,
                money_in=Decimal(0), closing_balance=closing,
                source='parsed validated withdrawals-only summary')
