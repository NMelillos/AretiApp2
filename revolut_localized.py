"""Russian Revolut Business structure; reuse the reconciled business row reader."""
import re
from datetime import datetime
from decimal import Decimal

MONTHS={'января':1,'февраля':2,'марта':3,'апреля':4,'мая':5,'июня':6,'июля':7,'августа':8,'сентября':9,'октября':10,'ноября':11,'декабря':12,
        'янв.':1,'февр.':2,'мар.':3,'апр.':4,'май':5,'июн.':6,'июл.':7,'авг.':8,'сент.':9,'окт.':10,'нояб.':11,'дек.':12}
LABELS={'Начальный баланс':'Opening balance','Прибыль':'Money in','Расходы':'Money out','Конечный остаток':'Closing balance'}

def recognized(text):
    return 'Revolut Bank UAB' in text and 'Выписка со счета' in text and 'Дата (UTC) Описание Расходы Прибыль Баланс' in text

def parse(pages):
    from parsing import RevolutBusinessParseError, _parse_revolut_business_pdf_text, _frame_from_pdf_rows
    def fail():raise RevolutBusinessParseError('Russian Revolut Business source failed structural/reconciliation validation.')
    text='\n'.join(pages)
    if not recognized(text):fail()
    # Printed page sequence is required; footer noise never becomes a transaction.
    for i,page in enumerate(pages,1):
        if not re.search(rf'(?m)^{i}/{len(pages)}\s*$',page):fail()
    ibans=set(re.findall(r'(?m)^IBAN (LT[\d ]+)\s*$',text))
    if len(ibans)!=1:fail()
    iban=re.sub(r'\s','',ibans.pop())
    if not re.fullmatch(r'LT\d{18}',iban) or int(iban[4:]+'2129'+iban[2:4])%97!=1:fail()
    currency=re.findall(r'(?m)^.*Валюта ([A-Z]{3})\s*$',text)
    if len(currency)!=1 or currency[0] not in ('EUR','GBP','USD'):fail()
    symbol={'EUR':'€','GBP':'£','USD':'$'}[currency[0]]
    period=re.findall(r'Операции от (\d{1,2}) ([а-я]+) (\d{4}) г\. перед (\d{1,2}) ([а-я]+) (\d{4}) г\.',text)
    if len(period)!=1:fail()
    def day(d,m,y):
        try:return datetime(int(y),MONTHS[m],int(d)).date()
        except (KeyError,ValueError):fail()
    d,m,y,d2,m2,y2=period[0];start,end=day(d,m,y),day(d2,m2,y2)
    if start>end:fail()
    normalized=['Revolut Bank'];summary={}
    for label,english in LABELS.items():
        matches=re.findall(r'^'+re.escape(label)+r' (.+)$',text,re.MULTILINE)
        if len(matches)!=1:fail()
        token=matches[0];money=re.fullmatch(r'(-\s*)?'+re.escape(symbol)+r'([\d ,]+\.\d{2})',token)
        if not money:fail()
        summary[english]=Decimal(money[2].replace(' ','').replace(',',''))*(-1 if money[1] else 1)
        normalized.append(english+' '+token)
    normalized.extend(['Transactions from '+start.isoformat()+' to '+end.isoformat(),'Date (UTC) Description Money out Money in Balance'])
    transactions=[];active=False
    for page in pages:
        for raw in page.splitlines():
            line=re.sub(r'\s+',' ',raw).strip()
            if line=='Дата (UTC) Описание Расходы Прибыль Баланс':active=True;continue
            if line.startswith(('Типы операций','Сообщить об утере')):break
            if not active:continue
            row=re.match(r'^(\d{1,2}) ([а-я.]+) (\d{4}) (.+)$',line)
            if row:
                when=day(*row.group(1,2,3))
                if not start<=when<=end:fail()
                transactions.append(when.strftime('%d %b %Y')+' '+row[4])
            elif line:
                if not transactions:fail()
                # Continuation text is preserved before the source amount columns.
                prior=transactions[-1];amount=re.search(re.escape(symbol)+r'[\d ,]+\.\d{2}',prior)
                if amount is None:fail()
                transactions[-1]=prior[:amount.start()].rstrip()+' | '+line+' '+prior[amount.start():]
    normalized.extend(transactions);normalized.append('Transaction types')
    rows=_parse_revolut_business_pdf_text('\n'.join(normalized))
    frame=_frame_from_pdf_rows(rows)
    balance=dict(bank='Revolut',account_number=iban,currency=currency[0],period_start=start.isoformat(),period_end=end.isoformat(),
                 opening_balance=summary['Opening balance'],money_in=summary['Money in'],money_out=abs(summary['Money out']),closing_balance=summary['Closing balance'],
                 source='Russian Revolut Business',reconciliation_convention='deposit',source_movements=[(r[0],r[2]) for r in rows],transaction_count=len(rows))
    if balance['opening_balance']+balance['money_in']-balance['money_out']!=balance['closing_balance']:fail()
    frame.attrs['statement_balance']=balance
    frame.attrs['parse_diagnostics']={'completed_rows':len(frame)}
    return frame


def validate_preview(frame,balance,account):
    from parsing import RevolutBusinessParseError
    def fail():raise RevolutBusinessParseError('Russian Revolut preview differs from validated source/account.')
    compact=lambda s:re.sub(r'[\s.-]','',str(s)).upper()
    if (compact(account.get('account_number'))!=balance['account_number'] or account.get('currency')!=balance['currency']
            or compact(account.get('bank')) not in ('REVOLUT','REVOLUTBANK')):fail()
    actual=[(str(r['Date']),Decimal(str(r['Amount']))) for r in frame.to_dict('records')]
    if actual!=balance['source_movements'] or len(actual)!=balance['transaction_count']:fail()
    if any(compact(r.get('account_number'))!=balance['account_number'] or r.get('currency')!=balance['currency'] for r in frame.to_dict('records')):fail()
    credits=sum((a for _,a in actual if a>0),Decimal(0));debits=-sum((a for _,a in actual if a<0),Decimal(0))
    if credits!=balance['money_in'] or debits!=balance['money_out'] or balance['opening_balance']+credits-debits!=balance['closing_balance']:fail()
