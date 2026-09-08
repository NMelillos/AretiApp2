"""Anonymised structural fixture; no client evidence or production connections."""
import ast
import os
from pathlib import Path
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import patch
import subprocess
import tempfile

os.environ.pop("DATABASE_URL", None)
os.environ.pop("POSTGRES_URL", None)

import pandas as pd
import parsing

BASE = "aaf5535b36a6e1d092a34cefe86c57ddf52de0d1"
TEXT = """Revolut Bank
Example Trading Limited
Balance summary
Opening balance \u20ac1 000.00
Money in \u20ac200.00
Money out - \u20ac100.00
Closing balance \u20ac1 100.00
Transactions from July 1, 2026 to July 31, 2026
Date (UTC) Description Money out Money in Balance
23 Jul 2026 Example purchase \u20ac100.00 \u20ac1 100.00
01 Jul 2026 Example receipt \u20ac200.00 \u20ac1 200.00
Transaction types
Total \u20ac300.00
"""


class Document:
    def __init__(self, text):
        self.pages = [SimpleNamespace(extract_text=lambda: text)]
    def __enter__(self):
        return self
    def __exit__(self, *args):
        pass


def parse(text, name):
    stream = BytesIO(b"synthetic placeholder")
    stream.name = name
    with patch.object(parsing.pdfplumber, "open", return_value=Document(text)), \
            patch.object(parsing, "pytesseract", None), patch.object(parsing, "convert_from_bytes", None):
        return parsing.parse_pdf(stream)


def main():
    rows = parse(TEXT, "statement.pdf")
    assert rows.Amount.tolist() == [-100, 200]
    assert rows.Date.tolist() == ["2026-07-23", "2026-07-01"]
    assert rows.statement_currency.tolist() == ["EUR", "EUR"]
    assert rows.Description.tolist() == ["Example purchase", "Example receipt"]
    assert len(rows) == 2 and rows.Amount.sum() == 100
    assert parse(TEXT.replace("Example purchase", "Example\u2014purchase"), "unicode.pdf").Description.iloc[0] == "Example\u2014purchase"
    for owner in ("Another Entity Ltd", "Independent Account Holder"):
        for filename in ("different-name.pdf", "account-statement.pdf"):
            pd.testing.assert_frame_equal(rows, parse(TEXT.replace("Example Trading Limited", owner), filename))
    forward = TEXT.replace("23 Jul 2026 Example purchase \u20ac100.00 \u20ac1 100.00\n01 Jul 2026 Example receipt \u20ac200.00 \u20ac1 200.00",
                           "01 Jul 2026 Example receipt \u20ac200.00 \u20ac1 200.00\n23 Jul 2026 Example purchase \u20ac100.00 \u20ac1 100.00")
    assert parse(forward, "forward.pdf").Amount.tolist() == [200, -100]
    for broken in (TEXT.replace("1 100.00", "1 101.00"), TEXT.replace("23 Jul 2026", "32 Jul 2026"),
                   TEXT.replace("01 Jul 2026 Example receipt \u20ac200.00 \u20ac1 200.00\n", "")):
        try:
            parse(broken, "broken.pdf")
        except parsing.RevolutBusinessParseError:
            pass
        else:
            raise AssertionError("Unreconciled input must not fall back to OCR/generic parsing")
    for unrelated in ("Ordinary invoice with no statement rows", TEXT.replace("Revolut Bank", "Unrelated institution"),
                      TEXT.replace("Transactions from ", "Unrelated period "),
                      TEXT.replace("Date (UTC) Description Money out Money in Balance", "Invoice details")):
        with patch.object(parsing, "_parse_revolut_business_pdf_text", wraps=parsing._parse_revolut_business_pdf_text) as business:
            try:
                parse(unrelated, "revolut.pdf")
            except ValueError:
                pass
            finally:
                business.assert_not_called()
    negative = TEXT.replace("Opening balance \u20ac1 000.00", "Opening balance - \u20ac1 000.00")
    negative = negative.replace("\u20ac1 100.00", "- \u20ac900.00").replace("\u20ac1 200.00", "- \u20ac800.00")
    for symbol, currency in (("\u20ac", "EUR"), ("\u00a3", "GBP"), ("$", "USD")):
        for fixture in (TEXT, negative):
            result = parse(fixture.replace("\u20ac", symbol), "valid.pdf")
            assert result.Amount.tolist() == [-100, 200]
            assert result.statement_currency.eq(currency).all()
    for minus in ("-", "\u2212", "\u2010", "\u2011", "\u2012", "\u2013", "\u2014", "\u2015", "\uFE58", "\uFE63", "\uFF0D"):
        for fixture in (negative.replace("-", minus), negative.replace("- \u20ac", "\u20ac" + minus)):
            assert parse(fixture, "negative.pdf").Amount.tolist() == [-100, 200]
    labels = ("Opening balance", "Money in", "Money out", "Closing balance")
    malformed = [TEXT.replace("Opening balance \u20ac", "Opening balance - \u20ac"),
                 TEXT.replace("Closing balance \u20ac", "Closing balance - \u20ac"),
                 TEXT.replace("Money in \u20ac", "Money in - \u20ac"),
                 TEXT.replace("Opening balance \u20ac", "Opening balance - \u20ac-")]
    malformed.append(TEXT.replace("Opening balance \u20ac", "Opening balance - \u20ac").replace(
        "Closing balance \u20ac", "Closing balance - \u20ac"))
    for label in labels:
        original = next(line for line in TEXT.splitlines() if line.startswith(label + " "))
        for replacement in (original.replace("\u20ac", "$"), original.replace("\u20ac", ""),
                            original.replace("\u20ac", "\u20ac$")):
            malformed.append(TEXT.replace(original, replacement))
    for fixture in malformed:
        with patch.object(parsing.pdfplumber, "open", return_value=Document(fixture)), \
                patch.object(parsing, "_parse_generic_pdf_text") as generic, \
                patch.object(parsing, "convert_from_bytes") as ocr:
            try:
                parsing.parse_pdf(BytesIO(b"synthetic"))
            except parsing.RevolutBusinessParseError:
                pass
            else:
                raise AssertionError("Malformed signed/currency summary accepted")
            generic.assert_not_called()
            ocr.assert_not_called()
    print("PASS: signed EUR/GBP/USD balances, minus variants/positions, per-field summary currency validation, no generic/OCR fallback, direct negative-routing spies")
    baseline = subprocess.check_output(["git", "show", f"{BASE}:parsing.py"]).decode("utf-8")
    old = {}
    exec(compile(baseline, "baseline_parsing.py", "exec"), old)
    legacy = """Revolut Bank
EUR Statement
Account (Current Account) \u20ac100.00 \u20ac0.00 \u20ac10.00 \u20ac90.00
Account transactions from July 1, 2026 to July 31, 2026
Jul 1, 2026 Example purchase \u20ac10.00 \u20ac90.00
"""
    assert parsing._parse_revolut_pdf_text(legacy) == old["_parse_revolut_pdf_text"](legacy)
    assert len(parsing._parse_revolut_pdf_text(legacy)) == 1
    old_nodes = {n.name: ast.dump(n) for n in ast.parse(baseline).body if isinstance(n, ast.FunctionDef)}
    new_nodes = {n.name: ast.dump(n) for n in ast.parse(Path("parsing.py").read_text(encoding="utf-8")).body if isinstance(n, ast.FunctionDef)}
    assert {name for name in old_nodes if old_nodes[name] != new_nodes[name]} == {"parse_pdf", "_parse_revolut_pdf_text"}
    for file in ("app.py", "db.py", "auth.py", "reporting.py"):
        assert Path(file).read_bytes().replace(b"\r\n", b"\n") == subprocess.check_output(["git", "show", f"{BASE}:{file}"]).replace(b"\r\n", b"\n")
    with tempfile.TemporaryDirectory(prefix="revolut-qa-") as tmp:
        os.environ["ARETI_DB_PATH"] = str(Path(tmp) / "isolated.sqlite")
        import db
        assert not db.USING_POSTGRES and Path(db.DB_PATH).parent == Path(tmp)
        db.init_db()
        rows["currency"] = "EUR"
        rows["bank"] = "Revolut"
        rows["account_name"] = "Synthetic entity"
        rows["account_number"] = "QA-ONLY"
        assert db.save_pending_transactions(rows, "example.pdf", "synthetic-hash")[0] == 2
        assert db.save_pending_transactions(rows, "renamed.pdf", "synthetic-hash")[:2] == (0, True)
        assert db.save_pending_transactions(rows, "another.pdf", "different-hash")[0] == 0
        assert len(db.get_all_transactions()) == 2
    print("PASS: format/name/filename, dates/signs/currency, both ordering directions, reconciliation, fail-closed, negative routing, legacy format, AST isolation and duplicate protection")
    print("REVOLUT_BUSINESS_QA_COMPLETE")


if __name__ == "__main__":
    main()
