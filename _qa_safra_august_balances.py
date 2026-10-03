"""Exact August regression with external evidence; geometry fixtures are synthetic."""
import ast
from contextlib import closing
from decimal import Decimal
from io import BytesIO
import os
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
from unittest.mock import patch

import parsing
from safra_layout import booking_text


def rejects(call):
    try:
        call()
    except parsing.SafraParseError:
        return
    raise AssertionError('Unknown Safra content accepted')


def geometry():
    from _qa_safra_import import TEXT
    booking = TEXT.splitlines()[6]
    tail = 'Synthetic address continuation - Distribution'
    raw = TEXT.replace(booking, booking + '\n' + tail)
    def word(text, x, top, width=10):
        return dict(text=text, x0=x, x1=x+width, top=top, bottom=top+8.5)
    header = [word('Transaction', 230, 10), word('Value', 550, 10),
              word('Debit', 640, 10), word('Credit', 720, 10)]
    tokens = booking.split()
    row = [word(token, 120+i*10, 50) for i, token in enumerate(tokens)]
    tail_row = [word(token, 230+i*25, 60) for i, token in enumerate(tail.split())]
    page = SimpleNamespace(extract_words=lambda: header + row + tail_row)
    normalized = booking_text(page, raw)
    assert tail in parsing._parse_safra_pdf_text(normalized)[0][1]
    rejects(lambda: parsing._parse_safra_pdf_text(raw))
    for bad in ('Unknown line', '12.03.2026 malformed booking', 'Unknown 1,00'):
        rejects(lambda bad=bad: parsing._parse_safra_pdf_text(TEXT.replace(booking, booking+'\n'+bad)))
    for shift, top in ((-110, 60), (330, 60), (0, 90)):
        bad_words = [dict(w, x0=w['x0']+shift, x1=w['x1']+shift,
                          top=top, bottom=top+8.5) for w in tail_row]
        bad_page = SimpleNamespace(extract_words=lambda: header + row + bad_words)
        rejects(lambda: parsing._parse_safra_pdf_text(booking_text(bad_page, raw)))
    assert booking_text(SimpleNamespace(), raw) == raw
    # Tail financial/date tokens cannot acquire description status from layout.
    for financial in ('12.03.2026 unknown', 'Unknown 1,00'):
        altered = raw.replace(tail, financial)
        financial_words = [word(t, 230+i*40, 60) for i,t in enumerate(financial.split())]
        fake = SimpleNamespace(extract_words=lambda: header + row + financial_words)
        rejects(lambda: parsing._parse_safra_pdf_text(booking_text(fake, altered)))
    print('PASS: geometry-backed continuation and unknown-content rejection')


def evidence(path):
    with parsing.pdfplumber.open(path) as document:
        pages = [page.extract_text() for page in document.pages]
    assert len(pages) == 6
    prior = subprocess.check_output(['git', 'show',
        'dfa218f944bb7204df179b9a7e4ce0f31d5d8c74:parsing.py'])
    node = next(n for n in ast.parse(prior).body if isinstance(n, ast.FunctionDef)
                and n.name == '_parse_safra_pdf_text')
    namespace = dict(parsing.__dict__)
    exec(compile(ast.Module(body=[node], type_ignores=[]), 'production-baseline', 'exec'), namespace)
    try:
        namespace['_parse_safra_pdf_text'](pages[0])
    except parsing.SafraParseError as exc:
        assert str(exc) == 'Safra statement validation failed: unrecognized content within bookings'
    else:
        raise AssertionError('Old failure not reproduced')
    assert sum('No bookings were carried out during the period stated.' in p for p in pages) == 5
    for page in pages[1:]:
        assert namespace['_parse_safra_pdf_text'](page) == []
    with open(path, 'rb') as stream:
        result = parsing.parse_pdf(stream)
    assert len(result) == 1 and result.Amount.iloc[0] == Decimal('-592186.14')
    assert isinstance(result.Amount.iloc[0], Decimal)
    sections = result.attrs['safra_sections']
    assert len(sections) == 6 and len({s['source_iban'] for s in sections}) == 6
    closing_values = ('1151053.95','0.00','24945.84','24334.64','0.00','12927.55')
    currencies = ('USD','EUR','GBP','USD','EUR','GBP')
    for i, section in enumerate(sections):
        assert section['source_account_number'].endswith(str(4000+i))
        assert section['statement_currency'] == currencies[i]
        assert Decimal(section['closing_balance']) == Decimal(closing_values[i])
        assert Decimal(section['opening_balance']) == Decimal('1743240.09' if i == 0 else closing_values[i])
        assert section['transaction_count'] == (1 if i == 0 else 0)
        assert section['period_start'] == '2026-08-01' and section['period_end'] == '2026-08-31'
        assert Decimal(section['money_out']) == Decimal('592186.14' if i == 0 else '0')
    assert 'Distribution' in result.Description.iloc[0]
    import db
    from _qa_safra_uat import labelled_accounts
    assert not db.USING_POSTGRES
    with tempfile.TemporaryDirectory(dir=os.environ['TEMP']) as root:
        accounts = labelled_accounts(result)
        with patch.object(db, 'DB_PATH', str(Path(root)/'august.sqlite')), \
                patch.object(db, 'get_accounts', return_value=accounts):
            db.init_db()
            frame = db.apply_account_and_rates(result, accounts.iloc[0].to_dict())
            assert db.save_pending_transactions(frame, 'external.pdf', 'external-document') == (1, False, 0)
            assert db.save_pending_transactions(frame, 'external.pdf', 'external-document')[1]
            with closing(db.get_connection()) as connection:
                assert connection.execute('SELECT COUNT(*) FROM statement_balances').fetchone()[0] == 6
                assert connection.execute('SELECT COUNT(*) FROM statement_imports').fetchone()[0] == 6
                assert connection.execute('SELECT COUNT(*) FROM classified_transactions').fetchone()[0] == 1
    print('PASS: original failure; six sections, five empty; exact balances; isolated persistence/duplicates')


def main():
    geometry()
    from safra_balances_qa import without_safra_balances
    for name in ('app.py', 'parsing.py'):
        without_safra_balances(name, Path(name).read_bytes())
    path = os.environ.get('SAFRA_AUGUST_EVIDENCE_PDF')
    if path:
        evidence(path)
    else:
        print('SKIP: external August PDF; set SAFRA_AUGUST_EVIDENCE_PDF')


if __name__ == '__main__':
    main()
