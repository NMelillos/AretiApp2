"""Synthetic page identity and isolated persistence regression for Safra."""
import ast
import hashlib
import os
from pathlib import Path
import subprocess
import tempfile
from contextlib import closing
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

import pandas as pd
import parsing

BASE = "32024814feb1f56ff68a7014bbdb30d6be8ba3e8"


def protected_db(actual):
    baseline = subprocess.check_output(["git", "show", BASE + ":db.py"]).decode("utf-8").replace("\r\n", "\n")
    source = actual.decode("utf-8")
    old = ast.parse(baseline)
    new = ast.parse(source)
    old_functions = {n.name: n for n in old.body if isinstance(n, ast.FunctionDef)}
    new_functions = {n.name: n for n in new.body if isinstance(n, ast.FunctionDef)}
    assert set(new_functions) - set(old_functions) == {"_safra_page_accounts"}
    assert {k for k in old_functions if ast.dump(old_functions[k]) != ast.dump(new_functions[k])} == {"apply_account_and_rates"}
    # Pin the exact reviewed implementation, then restore only its two functions
    # so historical whole-file assertions still protect every other byte.
    for name, expected in APPROVED_DB_FUNCTIONS.items():
        assert hashlib.sha256(ast.dump(new_functions[name]).encode()).hexdigest() == expected
    lines = source.splitlines(keepends=True)
    for name in sorted(APPROVED_DB_FUNCTIONS, key=lambda k: new_functions[k].lineno, reverse=True):
        node = new_functions[name]
        if name in old_functions:
            prior = old_functions[name]
            lines[node.lineno - 1:node.end_lineno] = baseline.splitlines(keepends=True)[prior.lineno - 1:prior.end_lineno]
        else:
            lines[node.lineno - 1:node.end_lineno + 2] = []
    compatible = "".join(lines)
    assert compatible == baseline
    return compatible.encode()


APPROVED_DB_FUNCTIONS = {
    "_safra_page_accounts": "24466c95b1ecbb2bc1e49f0d2e434a2f3b8bfb220c2ca0c900421bdb11988d7a",
    "apply_account_and_rates": "885e8c04a60e776f096b2c2b3a7f09481b2b5aaf09ceef4b55353c2306a971cd",
}


def fixture():
    from _qa_safra_import import fake_iban
    pages = []
    for i, currency in enumerate(("EUR", "USD", "CHF", "GBP")):
        page = (f"Bank J. Safra Sarasin AG\nSynthetic Holder\n"
                f"Account statement in {currency} 01.01.2026 to 30.06.2026\n"
                f"Current account {currency} / IBAN {fake_iban(i + 1)}\n"
                f"Client number Date Ref. no. Transaction Value date Debit Credit Balance in {currency}\n"
                "9.99999.9\nAccount number\n" + f"9.99999.9 {9000 + i}\n")
        if currency == "USD":
            page += "31.12.2025 Balance carried forward in your favour 10 000,00\n"
            balance = 10000
            for j, amount in enumerate((100, 100, 100, 200, 200, 200)):
                balance -= amount
                page += f"12.03.2026 {800000 + j} Synthetic fee {chr(65+j)} 31.03.2026 {amount},00 {balance},00\n"
            page += "30.06.2026 Balance in your favour 9100,00\n"
        else:
            page += "No bookings were carried out during the period stated.\n30.06.2026 Balance in your favour 0,00\n"
        pages.append(page)
    return pages


def setup_accounts(rows):
    return pd.DataFrame([dict(bank="Safra", account_name=f"Synthetic {s['statement_currency']}",
                              account_number=s["source_account_number"], currency=s["statement_currency"],
                              rate_type=f"{s['statement_currency']}/USD") for s in rows.attrs["safra_sections"]])


def must_reject(call):
    try:
        call()
    except ValueError:
        return
    raise AssertionError("Unsafe Safra input accepted")


def run():
    import db
    header_matrix()
    pages = fixture()
    rows = parsing._parse_safra_pages(pages)
    assert len(rows) == 6 and rows.source_page.eq(2).all()
    assert rows.statement_currency.eq("USD").all()
    assert rows.source_account_number.eq("9.99999.9 9001").all()
    assert rows.source_iban.eq(rows.attrs["safra_sections"][1]["source_iban"]).all()
    assert [s["statement_currency"] for s in rows.attrs["safra_sections"]] == ["EUR", "USD", "CHF", "GBP"]
    assert rows.Amount.tolist() == [-100] * 3 + [-200] * 3
    assert 10000 + rows.Amount.sum() == 9100
    assert rows.Description.nunique() == rows.normalized_description.nunique() == 6
    for index in (0, 2, 3):
        assert parsing._parse_safra_pages([pages[index]]).empty
    for altered in (pages[1].replace("Account number", "Client number"),
                    pages[1].replace("Current account USD", "Current account EUR"),
                    pages[1].replace("9100,00", "9101,00")):
        must_reject(lambda: parsing._parse_safra_pages([pages[0], altered, *pages[2:]]))
    accounts = setup_accounts(rows)
    assert not db.USING_POSTGRES
    assert Path(os.environ["TEMP"]).drive.upper() == "E:"
    with tempfile.TemporaryDirectory(dir=os.environ["TEMP"]) as root:
        with patch.object(db, "DB_PATH", str(Path(root) / "synthetic.sqlite")), \
                patch.object(db, "get_accounts", return_value=accounts):
            db.init_db()
            for invalid in (accounts.iloc[:1], pd.concat([accounts, accounts.iloc[[1]]])):
                with patch.object(db, "get_accounts", return_value=invalid):
                    must_reject(lambda: db.apply_account_and_rates(rows, accounts.iloc[0].to_dict()))
            data = db.apply_account_and_rates(rows, accounts.iloc[0].to_dict())
            assert data.account_number.eq("9.99999.9 9001").all()
            assert data.currency.eq("USD").all() and data.amount_usd.tolist() == data.Amount.tolist()
            with closing(db.get_connection()) as connection:
                assert connection.execute("SELECT COUNT(*) FROM classified_transactions").fetchone()[0] == 0
                assert connection.execute("SELECT COUNT(*) FROM statement_imports").fetchone()[0] == 0
            with ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(lambda _: db.save_pending_transactions(data, "synthetic.pdf", "synthetic-only"), range(2)))
            assert sorted(r[0] for r in results) == [0, 6]
            assert db.save_pending_transactions(data, "renamed.pdf", "synthetic-only")[0] == 0
            assert db.save_pending_transactions(data, "another.pdf", "synthetic-new-hash")[0] == 0
            with closing(db.get_connection()) as connection:
                saved = connection.execute("SELECT amount, currency, account_number, status, reviewed, original_description FROM classified_transactions ORDER BY id").fetchall()
            assert len(saved) == 6
            for stored, row in zip(saved, data.itertuples()):
                assert stored == (row.Amount, "USD", "9.99999.9 9001", "pending", 0, row.Description)
    print("PASS: Safra page mapping, zero-booking sections, fail-closed mapping, preview isolation, concurrent/renamed retries and fresh-connection persistence")


def header_matrix():
    from _qa_safra_import import fake_iban
    from io import BytesIO
    from types import SimpleNamespace
    pages = fixture()
    page = pages[1]
    iban_line = f"Current account USD / IBAN {fake_iban(2)}"
    cases = {
        "account conflict": page.replace("Account number\n", "Account number\n9.99999.9 9999\nAccount number\n"),
        "IBAN conflict": page.replace(iban_line, iban_line + f"\nCurrent account USD / IBAN {fake_iban(3)}"),
        "malformed extra IBAN": page.replace(iban_line, iban_line + "\nCurrent account EUR / IBAN INVALID"),
        "title currency": page.replace("statement in USD", "statement in EUR"),
        "current currency": page.replace("Current account USD", "Current account EUR"),
        "column currency": page.replace("Balance in USD", "Balance in EUR"),
        "missing account": page.replace("Account number\n9.99999.9 9001\n", ""),
        "missing IBAN": page.replace(iban_line + "\n", ""),
        "malformed account": page.replace("Account number\n9.99999.9 9001", "Account number\ninvalid"),
        "malformed title": page.replace("statement in USD", "statement in ???"),
        "malformed columns": page.replace("Balance in USD", "Balance in ???"),
        "truncated account": page + "Account number\n",
    }
    documents = {name: [pages[0], broken, *pages[2:]] for name, broken in cases.items()}
    documents["cross-page duplicate"] = [*pages, pages[1]]
    # Fail through the production entry point, without generic/OCR fallback or
    # ever passing a partial frame to persistence.
    for name, texts in documents.items():
        document = SimpleNamespace(pages=[SimpleNamespace(extract_text=lambda t=t: t) for t in texts])
        with patch.object(parsing.pdfplumber, "open") as pdf, \
                patch.object(parsing, "_parse_generic_pdf_text") as generic, \
                patch.object(parsing, "convert_from_bytes") as ocr:
            pdf.return_value.__enter__.return_value = document
            try:
                parsing.parse_pdf(BytesIO(b"synthetic-header-matrix"))
            except parsing.SafraParseError:
                pass
            else:
                raise AssertionError("Safra header matrix accepted: " + name)
            generic.assert_not_called()
            ocr.assert_not_called()
    repeated = page.replace(iban_line, iban_line + "\n" + iban_line)
    repeated = repeated.replace("Account number\n9.99999.9 9001", "Account number\n9.99999.9 9001\nAccount number\n9.99999.9 9001")
    pd.testing.assert_frame_equal(parsing._parse_safra_pages(pages), parsing._parse_safra_pages([pages[0], repeated, *pages[2:]]))
    print(f"PASS: {len(documents)} malformed header/document cases and agreeing repeated headers")
