"""Anonymised CNB statement contract and isolated E: persistence checks."""
import ast
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import hashlib
from io import BytesIO
import os
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
from unittest.mock import patch

import pandas as pd
import parsing
from cnb_import import CNBParseError, is_cnb

BASE = "1fd69b7fdf8d42636d8da339acf89805af43a122"


def without_cnb_db(actual):
    from _qa_safra_uat import without_uat_db
    actual = without_uat_db(actual)
    source = actual.decode("utf-8")
    baseline = subprocess.check_output(["git", "show", BASE + ":db.py"]).decode().replace("\r\n", "\n")
    find = lambda s: next(n for n in ast.parse(s).body if isinstance(n, ast.FunctionDef) and n.name == "apply_account_and_rates")
    new, old = find(source), find(baseline)
    assert hashlib.sha256(ast.dump(new).encode()).hexdigest() == APPROVED_MAPPING
    lines = source.splitlines(keepends=True)
    lines[new.lineno-1:new.end_lineno] = baseline.splitlines(keepends=True)[old.lineno-1:old.end_lineno]
    compatible = "".join(lines)
    assert compatible == baseline
    return compatible.encode()


APPROVED_MAPPING = "a406dd3f04ef9c015db57e67e038080aa4ee5559c7e596f14d00a2eb720d36bf"

TEXT = """Page 1 (2)
Account #: 987654321
This statement: August 31, 2026 Contact us:
Last statement: July 31, 2026
City National Bank
Synthetic Customer
BUSINESS CHECKING ACCOUNT
Account Summary Account Activity
Account number 987654321 Beginning balance (7/31/2026) $0.00
Minimum balance $0.00
Average balance $50.00 Credits Deposits (1) + 1,000.00
Avg. collected balance $50.00 Electronic cr (0) + 0.00
Other credits(0) + 0.00
Total credits +$1,000.00
Debits Checks paid (0) - 0.00
Electronic db (2) - 750.00
Other debits (0) - 0.00
Total debits - $750.00
Ending balance (8/31/2026) $250.00
DEPOSITS
Date Description Reference Credits
8-20 E-Deposit 00000001 1,000.00
ELECTRONIC DEBITS
Date Description Debits
8-21 Synthetic debit reference ALPHA 600.00
8-28 Synthetic debit reference BETA 150.00
DAILY BALANCES
Date Amount Date Amount Date Amount Date Amount
8-01 .00 8-20 1,000.00 8-21 400.00 8-28 250.00
Thank you for banking with City National Bank
"""


def parse(text=TEXT, alias="0987654321", name="synthetic.pdf"):
    document = SimpleNamespace(pages=[SimpleNamespace(extract_text=lambda: text),
                                     SimpleNamespace(extract_text=lambda: "Synthetic legal notice")],
                               metadata={"Keywords": alias})
    with patch.object(parsing.pdfplumber, "open") as pdf:
        pdf.return_value.__enter__.return_value = document
        stream = BytesIO(b"synthetic-only")
        stream.name = name
        return parsing.parse_pdf(stream)


def rejects(call):
    try:
        call()
    except CNBParseError:
        return
    raise AssertionError("Unsafe CNB input accepted")


def main():
    rows = parse()
    assert is_cnb(TEXT)
    for text in ("City National Bank\nInvoice", TEXT.replace("City National Bank", "Another Bank"),
                 TEXT.replace("City National Bank", "Payment to City National Bank")):
        assert not is_cnb(text)
    assert rows.Date.tolist() == ["2026-08-20", "2026-08-21", "2026-08-28"]
    assert rows.Amount.tolist() == [1000, -600, -150]
    assert rows.Description.tolist() == ["E-Deposit 00000001", "Synthetic debit reference ALPHA", "Synthetic debit reference BETA"]
    assert rows.statement_currency.eq("USD").all()
    assert rows.source_account_number.eq("987654321").all()
    assert rows.Amount.sum() == 250
    pd.testing.assert_frame_equal(rows, parse(name="renamed.pdf"))
    assert parse(TEXT.replace("2026", "2027")).Date.str.startswith("2027").all()
    assert "category" not in rows and "suggested_category" not in rows
    broken = [TEXT.replace("$250.00", "$251.00"), TEXT.replace("db (2)", "db (3)"),
              TEXT.replace("ALPHA 600.00", "ALPHA -600.00"), TEXT.replace("00000001 1,000.00", "00000001 -1,000.00"),
              TEXT.replace("8-21", "7-21"), TEXT.replace("987654321 Beginning", "987654322 Beginning"),
              TEXT.replace("Date Description Debits", "Unknown Columns"),
              TEXT.replace("DAILY BALANCES", "8-29 Unconsumed row 1.00\nDAILY BALANCES"),
              TEXT.replace("DEPOSITS\n", "8-19 Outside section 2.00\nDEPOSITS\n"),
              TEXT.replace("Total debits -", "Total debits +"),
              TEXT.replace("Total debits - $750.00", "Total debits - $750.00\nTotal debits + $750.00"),
              TEXT.replace("Account #: 987654321", "Account #: 987654321\nAccount #: invalid")]
    for text in broken:
        with patch.object(parsing, "_parse_generic_pdf_text") as generic, patch.object(parsing, "convert_from_bytes") as ocr:
            rejects(lambda: parse(text))
            generic.assert_not_called()
            ocr.assert_not_called()
    rejects(lambda: parse(alias="00987654321"))
    rejects(lambda: parse(alias="0987654322"))
    import db
    assert not db.USING_POSTGRES and Path(os.environ["TEMP"]).drive.upper() == "E:"
    accounts = pd.DataFrame([dict(bank="City National Bank", account_name="Synthetic CNB", account_number="987654321", currency="USD", rate_type="USD/USD")])
    with tempfile.TemporaryDirectory(dir=os.environ["TEMP"]) as root:
        with patch.object(db, "DB_PATH", str(Path(root) / "qa.sqlite")), patch.object(db, "get_accounts", return_value=accounts):
            db.init_db()
            for invalid in (accounts.iloc[:0], pd.concat([accounts, accounts]), accounts.assign(account_number="7654321")):
                with patch.object(db, "get_accounts", return_value=invalid):
                    rejects(lambda: db.apply_account_and_rates(rows, {}))
            data = db.apply_account_and_rates(rows, {"account_number": "UNSAFE DEFAULT"})
            assert data.account_number.eq("987654321").all()
            assert data.amount_usd.tolist() == [1000, -600, -150]
            with patch.object(db, "get_accounts", return_value=accounts.assign(account_number="0987654321")):
                assert db.apply_account_and_rates(rows, {}).account_number.eq("0987654321").all()
            with closing(db.get_connection()) as conn:
                assert conn.execute("SELECT COUNT(*) FROM classified_transactions").fetchone()[0] == 0
                assert conn.execute("SELECT COUNT(*) FROM statement_imports").fetchone()[0] == 0
            with ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(lambda _: db.save_pending_transactions(data, "synthetic.pdf", "cnb-synthetic"), range(2)))
            assert sorted(r[0] for r in results) == [0, 3]
            assert db.save_pending_transactions(data, "renamed.pdf", "cnb-synthetic")[0] == 0
            assert db.save_pending_transactions(data, "retry.pdf", "cnb-new-hash")[0] == 0
            with closing(db.get_connection()) as conn:
                stored = conn.execute("SELECT txn_date, original_description, amount, currency, status, reviewed FROM classified_transactions ORDER BY id").fetchall()
            assert len(stored) == 3
            for item, row in zip(stored, rows.itertuples()):
                assert item == (row.Date, row.Description, row.Amount, "USD", "pending", 0)
    without_cnb_db(Path("db.py").read_bytes().replace(b"\r\n", b"\n"))
    print("CNB PASS: detection, 14 malformed cases, dates, descriptions, signs, totals, mapping, preview isolation, three pending rows, concurrency and retries")


if __name__ == "__main__":
    main()
