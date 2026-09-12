"""Anonymised structural fixture; no client evidence or production connections."""
import ast
import hashlib
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


def _app_without_authorized_income_charity_edits(source):
    production = subprocess.check_output([
        "git", "show", "cb8b62a56007d49297fac3fdd408a307bee74377:app.py",
    ]).decode("utf-8").replace("\r\n", "\n")
    source = source.replace("\r\n", "\n")
    approved = {
        "_save_income_charity_edits": "f9d8c825c9f2b665b13d3a39d553ddd50188237aa96fbe74ea3f2f348cf940e2",
        "_render_income_charity_editor": "c9bbd66ac2b579c5746e18d97801c9f64647587e8159543142c39c4f8b745f1e",
        "_render_income_charity_transactions": "9c129839e87c49ca55a08468d3ed800aadab2da39b6e1d9744e7e3c981ec1106",
        "_render_income_charity_section": "ae196ba3518e54f4b735a08635ef47cbd78315f376d961f1ee375cd7ab8484f3",
        "render_executive_report": "be1e0f475797e6933c41832a494744502f4d48699ede46cd24f1df8a55d6bbf2",
    }
    def functions(text):
        nodes = [n for n in ast.parse(text).body if isinstance(n, ast.FunctionDef)]
        assert len({n.name for n in nodes}) == len(nodes), "Duplicate application function"
        return {n.name: n for n in nodes}
    old, new = functions(production), functions(source)
    added = {"_save_income_charity_edits", "_render_income_charity_editor"}
    changed = approved.keys() - added
    assert new.keys() - old.keys() == added and not old.keys() - new.keys()
    assert {name for name in old if ast.dump(old[name]) != ast.dump(new[name])} == changed
    lines, original = source.splitlines(keepends=True), production.splitlines(keepends=True)
    for name, expected_hash in approved.items():
        node = new[name]
        body = "".join(lines[node.lineno - 1:node.end_lineno])
        assert hashlib.sha256(body.encode("utf-8")).hexdigest() == expected_hash, name
    insertion_start = new["_save_income_charity_edits"].lineno - 1
    insertion_end = new["_render_income_charity_transactions"].lineno - 1
    inserted = "".join(
        "".join(lines[new[name].lineno - 1:new[name].end_lineno]) + "\n\n"
        for name in ("_save_income_charity_edits", "_render_income_charity_editor")
    )
    assert "".join(lines[insertion_start:insertion_end]) == inserted, "Unexpected code in insertion span"
    # Undo only the hash-pinned patch for legacy comparisons; protect all other
    # functions, top-level code, comments and whitespace against production.
    replacements = [(new[name].lineno - 1, new[name].end_lineno,
                     original[old[name].lineno - 1:old[name].end_lineno]) for name in changed]
    replacements.append((insertion_start, insertion_end, []))
    for start, end, replacement in sorted(replacements, reverse=True):
        lines[start:end] = replacement
    restored = "".join(lines)
    assert restored == production, "Application change outside the exact authorized patch"
    return restored


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
        actual = Path(file).read_bytes().replace(b"\r\n", b"\n")
        if file == "db.py":
            from safra_page_qa import protected_db
            actual = protected_db(actual)
        if file == "app.py":
            actual = _app_without_authorized_income_charity_edits(actual.decode("utf-8")).encode("utf-8")
        assert actual == subprocess.check_output(["git", "show", f"{BASE}:{file}"]).replace(b"\r\n", b"\n"), file
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
