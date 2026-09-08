"""Synthetic Safra regression; optional confidential evidence stays outside Git."""
import os
import ast
import hashlib
import re
import subprocess
import tempfile
from contextlib import closing
from decimal import Decimal
from pathlib import Path
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import patch

os.environ.pop("DATABASE_URL", None)
os.environ.pop("POSTGRES_URL", None)

import parsing
import pandas as pd

TEXT = """Bank J. Safra Sarasin AG
Example Account Holder
Account number QA-A
Account statement in USD 01.01.2026 to 30.06.2026
Date Ref. no. Transaction Value date Debit Credit Balance in USD
31.12.2025 Balance carried forward in your favour 1 000,00
12.03.2026 900001 Example fee 31.03.2026 100,00 900,00
11.06.2026 900002 Example receipt 30.06.2026 200,00 1 100,00
30.06.2026 Balance in your favour 1 100,00
"""


class Document:
    def __init__(self, text):
        self.pages = [SimpleNamespace(extract_text=lambda: text)]

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass


def parse(text, name="synthetic.pdf"):
    stream = BytesIO(b"synthetic")
    stream.name = name
    with patch.object(parsing.pdfplumber, "open", return_value=Document(text)), \
            patch.object(parsing, "convert_from_bytes", None):
        return parsing.parse_pdf(stream)


def main():
    rows = parse(TEXT)
    assert rows.Amount.tolist() == [-100, 200]
    assert rows.Date.tolist() == ["2026-03-12", "2026-06-11"]
    assert rows.statement_currency.tolist() == ["USD", "USD"]
    assert rows.Description.tolist() == [
        "900001 Example fee | Value date: 31.03.2026",
        "900002 Example receipt | Value date: 30.06.2026",
    ]
    for owner in ("Another Entity", "An Independent Person"):
        for name in ("changed.pdf", "arbitrary.pdf"):
            pd.testing.assert_frame_equal(rows, parse(TEXT.replace("Example Account Holder", owner).replace("QA-A", "QA-B"), name))
    for currency in ("EUR", "GBP", "CHF"):
        assert parse(TEXT.replace("USD", currency)).statement_currency.eq(currency).all()
    for space in ("  ", "\t", "\u00a0"):
        pd.testing.assert_frame_equal(rows, parse(TEXT.replace(" ", space)))
    pd.testing.assert_frame_equal(rows, parse(TEXT.replace("Bank J. Safra Sarasin AG", "BankJ.SafraSarasinAG")))
    empty = """Bank J. Safra Sarasin AG
Account statement in EUR 01.01.2026 to 30.06.2026
Date Ref. no. Transaction Value date Debit Credit Balance in EUR
No bookings were carried out during the period stated.
30.06.2026 Balance in your favour 50,00
"""
    combined = empty + TEXT + empty.replace("EUR", "CHF") + empty.replace("EUR", "GBP")
    pd.testing.assert_frame_equal(rows, parse(combined))
    assert parse(empty).empty
    continuation = TEXT.replace("11.06.2026 900002", "Account statement in USD 01.01.2026 to 30.06.2026\nDate Ref. no. Transaction Value date Debit Credit Balance in USD\n11.06.2026 900002")
    pd.testing.assert_frame_equal(rows, parse(continuation))
    negative = TEXT.replace("in your favour", "in our favour")
    negative = negative.replace("100,00 900,00", "100,00 -1 100,00").replace("200,00 1 100,00", "200,00 -900,00")
    negative = negative.replace("favour 1 100,00", "favour 900,00")
    assert parse(negative).Amount.tolist() == [-100, 200]
    prefix = TEXT.split("12.03.2026 900001")[0]
    bookings = [
        "12.03.2026 900001 Fee 31.03.2026 100,00 900,00",
        "13.03.2026 900002 Receipt 31.03.2026 100,00 1 000,00",
        "14.03.2026 900003 Fee 31.03.2026 100,00 900,00",
    ]
    ending = "\n30.06.2026 Balance in your favour 900,00\n"
    assert parse(prefix + "\n".join(bookings) + ending).Amount.tolist() == [-100, 100, -100]
    malformed = [
        "\n".join(TEXT.splitlines()[:3]) + "\n" + " ".join(TEXT.splitlines()[4:]) + "\n" + TEXT.replace("USD", "EUR"),
        TEXT + " ".join(TEXT.splitlines()[4:]),
        TEXT.replace("Account statement in USD 01.01.2026 to 30.06.2026\n", "") + TEXT.replace("USD", "EUR"),
        prefix + " ".join(bookings[:2]) + " Date Ref. no. Transaction Value date Debit Credit Balance in USD\n" + bookings[2] + ending,
        TEXT.replace("Bank J. Safra", "Bank J.Safra").replace("12.03.2026", "12/03/2026").replace("100,00 900,00", "100.00 900.00"),
        TEXT.replace("Bank J. Safra", "Bank J.  Safra").replace("12.03.2026", "12/03/2026").replace("100,00 900,00", "100.00 900.00"),
        prefix + " ".join(bookings) + ending,
        TEXT.replace("100,00 900,00", "+100,00 900,00"),
        TEXT + TEXT,
        TEXT.replace("12.03.2026 900001", "bad-date 900001"),
        TEXT.replace("Balance in USD", "Balance in USD EUR"),
        TEXT.replace("Account statement in USD 01.01.2026 to 30.06.2026\n", ""),
        TEXT.replace("Debit Credit", "Unknown"),
        TEXT.replace("Balance in USD", "Balance in EUR"),
        TEXT.replace("100,00 900,00", "USD 100,00 900,00"),
        TEXT.replace("100,00 900,00", "100,00 10,00 900,00"),
        TEXT.replace("100,00 900,00", "100,00 901,00"),
        TEXT.replace("favour 1 100,00", "favour 1 101,00"),
        TEXT.replace("12.03.2026", "32.03.2026"),
        TEXT.replace("31.03.2026", "31.02.2026"),
        TEXT.replace("12.03.2026", "12.03.2027"),
        TEXT.replace("Example fee", "Example fee EUR"),
        TEXT.replace("Account statement in USD", "Account statement in ???"),
        TEXT.replace("100,00 900,00", "-100,00 900,00"),
    ]
    for broken in malformed:
        with patch.object(parsing, "_parse_generic_pdf_text") as generic, \
                patch.object(parsing, "convert_from_bytes") as ocr:
            try:
                with patch.object(parsing.pdfplumber, "open", return_value=Document(broken)):
                    parsing.parse_pdf(BytesIO(b"synthetic"))
            except parsing.SafraParseError:
                pass
            else:
                raise AssertionError("Malformed Safra must fail closed")
            generic.assert_not_called()
            ocr.assert_not_called()
    for unrelated in ("An ordinary invoice", TEXT.replace("Bank J. Safra Sarasin AG", "Another Bank"),
                      "Bank J. Safra Sarasin AG\nAn invoice with no bank table",
                      TEXT.replace("Bank J. Safra Sarasin AG", "Payment to Bank J. Safra Sarasin AG")):
        with patch.object(parsing, "_parse_safra_pdf_text", wraps=parsing._parse_safra_pdf_text) as spy:
            try:
                parse(unrelated)
            except ValueError:
                pass
            finally:
                spy.assert_not_called()
    baseline = subprocess.check_output(["git", "show", "f7a0f9f13faed18734eb726a35a7069b41b46156:parsing.py"], text=True)
    functions = lambda source: {n.name: ast.dump(n) for n in ast.parse(source).body if isinstance(n, ast.FunctionDef)}
    old, new = functions(baseline), functions(Path("parsing.py").read_text(encoding="utf-8"))
    assert {name for name in old if old[name] != new[name]} == {"parse_pdf"}
    for name in ("app.py", "db.py", "auth.py", "reporting.py"):
        assert Path(name).read_bytes().replace(b"\r\n", b"\n") == subprocess.check_output([
            "git", "show", "f7a0f9f13faed18734eb726a35a7069b41b46156:" + name]).replace(b"\r\n", b"\n")
    persistence(rows)
    evidence = os.environ.get("SAFRA_EVIDENCE_PDF")
    if evidence:
        exact_pdf(evidence)
    print("SAFRA_TARGETED_PASS")


def persistence(rows):
    import db
    assert not db.USING_POSTGRES
    root = os.environ.get("TEMP", "")
    assert Path(root).drive.upper() == "E:"
    with tempfile.TemporaryDirectory(prefix="safra-qa-", dir=root) as tmp:
        assert Path(tmp).drive.upper() == "E:"
        db.DB_PATH = str(Path(tmp) / "isolated.sqlite")
        assert Path(db.DB_PATH).drive.upper() == "E:"
        db.init_db()
        data = db.apply_account_and_rates(rows, {
            "bank": "Safra", "account_name": "Synthetic account",
            "account_number": "QA-ONLY", "currency": "EUR",
        })
        assert data.currency.tolist() == rows.statement_currency.tolist()
        assert data.Amount.tolist() == rows.Amount.tolist()
        assert data.account_name.eq("Synthetic account").all()
        assert db.save_pending_transactions(data, "synthetic.pdf", "synthetic-hash")[0] == len(rows)
        assert db.save_pending_transactions(data, "renamed.pdf", "synthetic-hash")[:2] == (0, True)
        assert db.save_pending_transactions(data, "different.pdf", "different-hash")[0] == 0
        with closing(db.get_connection()) as connection:
            stored = connection.execute("SELECT txn_date, original_description, amount, currency FROM classified_transactions ORDER BY id").fetchall()
        assert len(stored) == len(rows)
        for actual, expected in zip(stored, rows.itertuples()):
            assert actual == (expected.Date, expected.Description, expected.Amount, expected.statement_currency)
    print("PASS: isolated E: SQLite persistence and duplicate reimport")


def exact_pdf(path):
    raw = Path(path).read_bytes()
    assert len(raw) == 174504
    assert hashlib.sha256(raw).hexdigest() == "84e9ba121280ba4b33195653ce27a5dcbb8873624c6f625810b2790681915ca1"
    with parsing.pdfplumber.open(BytesIO(raw)) as document:
        assert len(document.pages) == 4
        texts = [p.extract_text() for p in document.pages]
    result = parsing.parse_pdf(BytesIO(raw))
    assert len(result) == 6 and result.statement_currency.eq("USD").all()
    # Independently read each booking's two date tokens and its reference from
    # external evidence; never embed confidential text or identifiers in fixtures.
    expected = []
    for line in texts[1].splitlines():
        tokens = line.split()
        if len(tokens) > 4 and re.fullmatch(r"\d{2}\.\d{2}\.\d{4}", tokens[0]) and tokens[1].isdigit():
            value_index = next(i for i in range(2, len(tokens)) if re.fullmatch(r"\d{2}\.\d{2}\.\d{4}", tokens[i]))
            expected.append((tokens[0], " ".join(tokens[1:value_index]), tokens[value_index], Decimal(tokens[value_index+1].replace(",", "."))))
    assert len(expected) == 6
    from datetime import datetime
    for row, (when, description, value_date, magnitude) in zip(result.itertuples(), expected):
        assert row.Date == datetime.strptime(when, "%d.%m.%Y").strftime("%Y-%m-%d")
        assert row.Description == description + " | Value date: " + value_date
        assert Decimal(str(row.Amount)) == -magnitude
    amounts = [Decimal(str(v)) for v in result.Amount]
    assert amounts.count(Decimal("-656.05")) == 3
    assert amounts.count(Decimal("-639.25")) == 3
    assert sum(amounts) == Decimal("-3885.90")
    assert Decimal("62238.85") + sum(amounts) == Decimal("58352.95")
    for i in (0, 2, 3):
        assert "No bookings were carried out" in texts[i]
    persistence(result)
    print("PASS: external PDF, six rows, references/descriptions/booking/value dates, USD 62238.85 - 3885.90 = 58352.95")


if __name__ == "__main__":
    main()
