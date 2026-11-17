"""Bind already-reconciled English Business summaries to the active source account."""
from datetime import datetime
from decimal import Decimal
import re


def bind_summary(pages, frame, summaries):
    from parsing import RevolutBusinessParseError
    def require(ok):
        if not ok:
            raise RevolutBusinessParseError('Revolut Business source/account summary is ambiguous; no rows imported.')
    active = [s for s in summaries if s['transaction_count']]
    require(len(active) == 1 and len({s['currency'] for s in summaries}) == len(summaries))
    summary = active[0]
    # Empty wallets may not conceal non-zero balances in a single-wallet import.
    require(all(s is summary or all(s[k] == 0 for k in
                ('opening_balance','money_in','money_out','closing_balance')) for s in summaries))
    headers = []
    for i, page in enumerate(pages, 1):
        require('Revolut Bank UAB' in page and re.search(rf'(?m)^{i}/{len(pages)}\s*$', page))
        if 'Balance summary' not in page:
            continue
        header = page.split('Balance summary')[0]
        currencies = re.findall(r'\bCurrency\s+(EUR|USD|GBP)\b', header)
        numbers = {re.sub(r'\s', '', n) for n in re.findall(r'(?m)^IBAN\s+(LT[\d ]+)\s*$', header)}
        owner = re.search(r'(?m)^Generated on[^\n]+\n([^\n]+)', header)
        periods = re.findall(r'Transactions from ([A-Za-z]+ \d{1,2}, \d{4}) to ([A-Za-z]+ \d{1,2}, \d{4})', page)
        require(len(currencies) == 1 and len(numbers) == 1 and owner is not None and len(periods) == 1)
        number = next(iter(numbers))
        require(re.fullmatch(r'LT\d{18}', number) and int(number[4:]+'2129'+number[2:4]) % 97 == 1)
        start, end = [datetime.strptime(v, '%B %d, %Y').date().isoformat() for v in periods[0]]
        require(start <= end)
        headers.append((currencies[0], number, owner[1].strip(), start, end))
    require(len(headers) == len(summaries) and {h[0] for h in headers} == {s['currency'] for s in summaries})
    require(len({h[1:] for h in headers}) == 1)
    currency, number, owner, start, end = next(h for h in headers if h[0] == summary['currency'])
    require(len(frame) == summary['transaction_count'] and frame.statement_currency.eq(currency).all())
    movements = [(str(r.Date), str(r.Description), Decimal(str(r.Amount))) for r in frame.itertuples()]
    require(all(start <= d <= end for d, _, _ in movements))
    require(sum((a for _,_,a in movements if a > 0), Decimal(0)) == summary['money_in'])
    require(-sum((a for _,_,a in movements if a < 0), Decimal(0)) == summary['money_out'])
    evidence = dict(summary, account_number=number, account_name=owner, period_start=start,
                    period_end=end, movements=movements)
    frame.attrs['revolut_source_account'] = evidence
    frame.attrs['statement_balance'] = dict(evidence, bank='Revolut', source='English Revolut Business validated summary')
