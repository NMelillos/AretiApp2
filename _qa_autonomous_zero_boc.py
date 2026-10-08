"""Actual synthetic PDF through both summary and bank-column proof; local only."""
from contextlib import closing
from decimal import Decimal
import db
from boc_summary_balances import prepare,record
from _qa_boc_package import fixture
from _qa_post_import_message import CompletionTests
def zero_source():
    page,text=fixture()
    commands=[]
    def show(value,x,top):
        value=value.replace('\\','\\\\').replace('(','\\(').replace(')','\\)')
        commands.append(f'BT /F1 9 Tf 1 0 0 1 {x} {792-top} Tm ({value}) Tj ET')
    for i,line in enumerate(text.splitlines()):show(line,40,40+i*20)
    for word in page.extract_words():show(word['text'],word['x0'],word['top'])
    stream='\n'.join(commands).encode('ascii')
    objects=[b'<< /Type /Catalog /Pages 2 0 R >>',b'<< /Type /Pages /Kids [3 0 R] /Count 1 >>',b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>',b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>',b'<< /Length '+str(len(stream)).encode()+b' >>\nstream\n'+stream+b'\nendstream']
    pdf=b'%PDF-1.4\n';offsets=[0]
    for i,obj in enumerate(objects,1):offsets.append(len(pdf));pdf+=str(i).encode()+b' 0 obj\n'+obj+b'\nendobj\n'
    xref=len(pdf);pdf+=b'xref\n0 6\n0000000000 65535 f \n'+b''.join(f'{n:010} 00000 n \n'.encode() for n in offsets[1:])
    return pdf+f'trailer\n<< /Size 6 /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n'.encode()
class ZeroSourceTests(CompletionTests):
    def test_real_synthetic_zero_pdf_source_proof_commit_and_repeat(self):
        content=zero_source();frame,balance=prepare(content)
        self.assertTrue(frame.empty);self.assertEqual(balance['opening_balance'],Decimal('100.00'));self.assertEqual(balance['closing_balance'],Decimal('100.00'))
        self.assertEqual(balance['money_in'],Decimal(0));self.assertEqual(balance['money_out'],Decimal(0))
        with closing(db.get_connection()) as conn:
            conn.execute('INSERT INTO account_list(account_name,bank,account_number,currency,rate_type) VALUES(?,?,?,?,?)',('Synthetic zero','Bank of Cyprus',balance['account_number'],'EUR','EUR/USD'));conn.commit()
        before=self.unchanged if hasattr(self,'unchanged') else self.counts()
        self.assertEqual(record(db,content,'synthetic-zero.pdf','Synthetic operator'),'recorded');self.assertEqual(self.counts(),(0,1,1))
        saved=self.snapshot();self.assertEqual(record(db,content,'synthetic-zero.pdf','Synthetic operator'),'already recorded');self.assertEqual(self.snapshot(),saved)
