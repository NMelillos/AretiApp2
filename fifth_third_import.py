"""Labelled Fifth Third commercial deposit statements; strict source accounting."""
from datetime import datetime
from decimal import Decimal
import re

class FifthThirdParseError(ValueError):pass

def recognized(text):
    return 'FTCSTMT002' in text and 'Banking Center: Fifth Third Center' in text and 'Account Summary -' in text

def parse(pages):
    from parsing import _frame_from_pdf_rows
    def fail():raise FifthThirdParseError('Fifth Third source structure/counts/totals failed validation; no rows imported.')
    text='\n'.join(pages)
    if not recognized(text):fail()
    for i,page in enumerate(pages,1):
        if not re.search(rf'(?m)Page {i} of {len(pages)}\s*$',page):fail()
    if any('Thispageintentionallyleftblank.' not in re.sub(r'\s+','',page) for page in pages[1:]):fail()
    text=pages[0]
    def unique(pattern):
        found=re.findall(pattern,text,re.MULTILINE)
        if len(found)!=1:fail()
        return found[0]
    account=unique(r'^Account Number: (\d+)\s*$')
    if unique(r'^Account Summary - (\d+)\s*$')!=account:fail()
    d1,d2=unique(r'^Statement Period Date: (\d{1,2}/\d{1,2}/\d{4}) - (\d{1,2}/\d{1,2}/\d{4})\s*$')
    try:start,end=[datetime.strptime(d,'%m/%d/%Y').date() for d in (d1,d2)]
    except ValueError:fail()
    if start>end or (start.year,start.month)!=(end.year,end.month):fail()
    money=r'(?:\d{1,3}(?:,\d{3})+|\d+)\.\d{2}'
    def amount(s):return Decimal(s.replace(',',''))
    od,op=unique(rf'^(\d{{2}}/\d{{2}}) Beginning Balance \$({money})\b.*$')
    cd,cl=unique(rf'^(\d{{2}}/\d{{2}}) Ending Balance \$({money})\s*$')
    if od!=start.strftime('%m/%d') or cd!=end.strftime('%m/%d'):fail()
    opening,closing=amount(op),amount(cl)
    sections={'Checks':-1,'Withdrawals /Debits':-1,'Deposits /Credits':1}
    expected={}
    for label in ('Checks','Withdrawals / Debits','Deposits / Credits'):
        found=re.findall(rf'^(?:(\d+) )?{re.escape(label)}(?: \$\(?({money})\)?)?(?: (?:Interest Earned|Annual Percentage|Number of Days).*)?$',text,re.MULTILINE)
        if len(found)!=1:fail()
        count,total=found[0]
        if not count and not total:count,total='0','0.00'
        if not count or not total:fail()
        expected[label.replace(' / ',' /')]=(int(count),amount(total))
    rows=[];observed={k:[] for k in sections};section=None
    for raw in text.splitlines():
        line=re.sub(r'\s+',' ',raw).strip()
        header=re.fullmatch(rf'(Checks|Withdrawals /Debits|Deposits /Credits) (\d+) (?:checks|items|item) totaling \$({money})',line)
        if header:
            section=header[1]
            if observed[section] or expected[section]!=(int(header[2]),amount(header[3])):fail()
            continue
        if line.startswith('DailyBalanceSummary'):section=None;continue
        if section is None:continue
        if line.startswith(('Date Amount Description','Number DatePaid Amount','*Indicatesgapinchecksequence')):continue
        if section=='Checks':
            chunks=re.findall(rf'(\d+) [is] (\d{{2}}/\d{{2}}) ({money})(?: |$)',line)
            if not chunks or ' '.join(f'{number} i {date} {value}' for number,date,value in chunks)!=line.replace(' s ',' i '):fail()
            for number,date,value in chunks:observed[section].append((date,'Check '+number,amount(value)))
        else:
            row=re.fullmatch(rf'(\d{{2}}/\d{{2}}) ({money}) (.+)',line)
            if row:observed[section].append((row[1],row[3],amount(row[2])))
            elif observed[section] and line:
                date,description,value=observed[section][-1]
                observed[section][-1]=(date,description+' | '+line,value)
            elif line:fail()
    credits=debits=Decimal(0)
    for label,direction in sections.items():
        values=observed[label]
        if (len(values),sum((v[2] for v in values),Decimal(0)))!=expected[label]:fail()
        for date,description,value in values:
            try:when=datetime.strptime(f'{date}/{start.year}','%m/%d/%Y').date()
            except ValueError:fail()
            if not start<=when<=end:fail()
            rows.append([when.isoformat(),description,direction*value,'USD','Fifth Third labelled sections'])
        total=expected[label][1]
        if direction==1:credits+=total
        else:debits+=total
    if opening+credits-debits!=closing:fail()
    frame=_frame_from_pdf_rows(rows)
    frame.attrs['statement_balance']=dict(bank='Fifth Third Bank',account_number=account,currency='USD',period_start=start.isoformat(),period_end=end.isoformat(),
        opening_balance=opening,money_in=credits,money_out=debits,closing_balance=closing,source='Fifth Third labelled sections',
        source_movements=[(r[0],r[2]) for r in rows],transaction_count=len(rows),reconciliation_convention='deposit')
    return frame

def validate_preview(frame,balance,account):
    # The source is Fifth Third after the acquisition; retain the existing
    # Comerica Setup identity when its exact account number and USD match.
    # This does not rename accounts or rewrite historical bank identities.
    def fail():raise FifthThirdParseError('Fifth Third preview differs from validated source/account.')
    if (str(account.get('account_number','')).strip()!=balance['account_number'] or account.get('currency')!='USD'
            or re.sub(r'[^A-Z0-9]','',str(account.get('bank','')).upper()) not in ('FIFTHTHIRD','FIFTHTHIRDBANK','COMERICA','COMERICABANK')):fail()
    actual=[(str(r['Date']),Decimal(str(r['Amount']))) for r in frame.to_dict('records')]
    if actual!=balance['source_movements'] or len(actual)!=balance['transaction_count']:fail()
    if any(str(r.get('account_number'))!=balance['account_number'] or r.get('currency')!='USD' for r in frame.to_dict('records')):fail()
    credits=sum((a for _,a in actual if a>0),Decimal(0))
    debits=-sum((a for _,a in actual if a<0),Decimal(0))
    if credits!=balance['money_in'] or debits!=balance['money_out'] or balance['opening_balance']+credits-debits!=balance['closing_balance']:fail()
