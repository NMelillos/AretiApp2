"""Synthetic exact-IBAN mapping and unchanged Setup currency/exchange fields."""
import ast
from contextlib import closing
import os
from pathlib import Path
import subprocess
import tempfile
from unittest.mock import patch

import pandas as pd
import parsing
from safra_page_qa import fixture, setup_accounts, must_reject
from _qa_safra_import import fake_iban


def main():
    import db
    assert not db.USING_POSTGRES and Path(os.environ["TEMP"]).drive.upper() == "E:"
    base = "efd2bddfe12eb3eb88faa04d25751e3fe437efa0"
    for name in ("app.py", "reporting.py", "cnb_import.py", "safra_history.py", "parsing.py"):
        actual = Path(name).read_bytes().replace(b"\r\n", b"\n")
        if name == 'parsing.py':
            from _qa_safra_lifecycle import without_booking_currency
            actual = without_booking_currency(actual)
        if name in ('app.py', 'reporting.py'):
            from _qa_income_groups import compatible
            actual = compatible(name, actual)
        assert actual == subprocess.check_output(["git", "show", base + ":" + name]).replace(b"\r\n", b"\n")
    nodes = lambda source: {n.name: ast.dump(n) for n in ast.parse(source).body if isinstance(n, (ast.FunctionDef, ast.ClassDef))}
    old = nodes(subprocess.check_output(["git", "show", base + ":db.py"]))
    from _qa_pending_save_normalization import without_save_normalization
    new = nodes(without_save_normalization("db.py", Path("db.py").read_bytes()))
    assert old.keys() == new.keys()
    assert {k for k in old if old[k] != new[k]} == {"_safra_page_accounts"}
    rows = parsing._parse_safra_pages(fixture())
    accounts = setup_accounts(rows)
    extra = accounts.iloc[[1]].copy()
    extra["account_number"] = fake_iban(99)
    accounts = pd.concat([extra, accounts], ignore_index=True)
    with tempfile.TemporaryDirectory(dir=os.environ["TEMP"]) as root:
        with patch.object(db, "DB_PATH", str(Path(root) / "synthetic.sqlite")):
            db.init_db()
            workbook = accounts.rename(columns={"account_name": "Account Name", "account_number": "Account Number",
                                               "bank": "Bank", "currency": "Currency", "rate_type": "Rate Type"})
            with patch.object(db, "_read_excel", return_value=workbook):
                db.replace_accounts_from_excel(None)
            saved = db.get_accounts()
            assert len(saved) == 5
            usd = saved[saved.currency.eq("USD")]
            assert len(usd) == 2 and usd.account_number.nunique() == 2
            assert usd.rate_type.eq("USD/USD").all()
            for section in rows.attrs["safra_sections"]:
                record = saved[saved.account_number.eq(section["source_iban"])].iloc[0]
                assert record.currency == section["statement_currency"]
                assert record.rate_type == section["statement_currency"] + "/USD"
            for ordered in (saved, saved.iloc[::-1]):
                mapped = db._safra_page_accounts(rows, ordered)
                assert mapped[2]["account_number"] == rows.attrs["safra_sections"][1]["source_iban"]
            numeric = setup_accounts(rows)
            numeric["account_number"] = [s["source_account_number"] for s in rows.attrs["safra_sections"]]
            must_reject(lambda: db._safra_page_accounts(rows, numeric))
            must_reject(lambda: db._safra_page_accounts(rows, saved[saved.account_number.ne(rows.attrs["safra_sections"][1]["source_iban"])]))
            data = db.apply_account_and_rates(rows, saved[saved.currency.eq("EUR")].iloc[0].to_dict())
            assert data.currency.eq("USD").all() and data.rate_type.eq("USD/USD").all()
            assert data.account_number.eq(rows.attrs["safra_sections"][1]["source_iban"]).all()
            with closing(db.get_connection()) as conn:
                assert conn.execute("SELECT COUNT(*) FROM classified_transactions").fetchone()[0] == 0
    print("PASS: Setup retains Currency/Rate Type, same-currency IBANs remain separate, exact page mapping, numeric-only/wrong IBAN rejected; previews write no transactions")


if __name__ == "__main__":
    main()
