"""Source-validated Comerica checks-only, check-number-first statements."""
from datetime import datetime
from decimal import Decimal
import re


class ComericaChecksError(ValueError):
    pass


def parse_checks_only(pdf):
    text = '\n'.join(page.extract_text() or '' for page in pdf.pages)
    money = r'-?\$?\d[\d,]*\.\d{2}'
    rows = re.findall(rf'(?m)^([#*@]*\d+)\s+({money})\s+((?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\s*\d{{1,2}})\s+(\d+)\s*$', text)
    if not rows or not re.search(r'Checks\s*paid\s*this\s*statement\s*period', text, re.I):
        return None
    # Restrict the added path to the left account-summary panel. This excludes
    # right-column addresses/contact numbers interleaved by whole-page extraction.
    page = pdf.pages[0]
    panel = page.crop((0, 0, page.width * .60, page.height)).extract_text() or ''
    date = r'([A-Za-z]+)\s*(\d{1,2}),\s*(\d{4})'
    summary = re.search(
        rf'Beginning\s*balance\s*(?:on\s*{date}\s*)?({money})\s+'
        rf'Less\s*withdrawals\s+Checks\s+({money})\s+'
        rf'Ending\s*balance\s*(?:on\s*{date}\s*)?({money})',
        panel, re.I,
    )
    if not summary or not all(summary[i].lstrip('-').startswith('$') for i in (4, 9)):
        return None  # Other Comerica layouts continue through their existing path.
    period = re.findall(rf'{date}\s*to\s*{date}', text, re.I)
    identities = set(re.findall(r'Account\s*number\s*(\d{6,})', text, re.I))
    if not period or len(set(period)) != 1 or len(identities) != 1:
        raise ComericaChecksError('Comerica check statement identity/period is ambiguous.')
    parse_date = lambda parts: datetime.strptime(' '.join(parts), '%B %d %Y').date().isoformat()
    start, end = parse_date(period[0][:3]), parse_date(period[0][3:])
    if start > end:
        raise ComericaChecksError('Comerica check statement period is invalid.')
    number = lambda value: Decimal(value.replace('$', '').replace(',', ''))
    opening, summary_checks, closing = map(number, (summary[4], summary[5], summary[9]))
    count = re.search(r'Total\s*number\s*of\s*checks\s*paid\s*this\s*statement\s*period:\s*(\d+)', text, re.I)
    total = re.search(rf'Total\s*checks\s*paid\s*this\s*statement\s*period:\s*({money})', text, re.I)
    parsed = []
    for check, amount, paid, reference in rows:
        month, day = re.fullmatch(r'([A-Za-z]+)\s*(\d+)', paid).groups()
        paid_date = datetime.strptime(f'{month} {day} {start[:4]}', '%b %d %Y').date().isoformat()
        if not start <= paid_date <= end:
            raise ComericaChecksError('Comerica check date is outside the statement period.')
        parsed.append([paid_date, f'Check {check} / Bank reference {reference}', -abs(number(amount)), 'USD'])
    amounts = sum((row[2] for row in parsed), Decimal(0))
    if (not count or int(count[1]) != len(parsed) or not total
            or amounts != -abs(number(total[1])) or amounts != -abs(summary_checks)
            or opening + amounts != closing):
        raise ComericaChecksError('Comerica check count/total or balance reconciliation failed; no rows imported.')
    # This exact summary has only checks under "Less withdrawals"; all movement
    # is accounted for by the source check total and reconciles to the source close.
    return parsed, dict(bank='Comerica', account_number=identities.pop(), currency='USD',
                        period_start=start, period_end=end, opening_balance=opening,
                        money_out=amounts, money_in=Decimal(0), closing_balance=closing,
                        source='parsed checks-only summary')
