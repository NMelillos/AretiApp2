"""Transaction-consistent persisted Safra section evidence, synthetic by default."""
import ast
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from decimal import Decimal
import hashlib
from io import BytesIO
import json
import os
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
from unittest.mock import patch

import parsing

BASE = "6669a65c7b744255013b80cb171c2f21dd2a7957"
APPROVED = {
    "app.py": "42c469d2e4448246fd48690ab64ce78abc1ba4f8146e8f28cca00fc2b93bd7a2",
    "db.py": "3dbd9c242fdf6299be24927f56dc84af64be4e66d5e4402f197c5052f8089011",
}


def without_history(name, source):
    source = source.replace("\r\n", "\n")
    assert hashlib.sha256(source.encode()).hexdigest() == APPROVED[name], name
    return subprocess.check_output(["git", "show", BASE + ":" + name]).decode("utf-8").replace("\r\n", "\n")


def persisted(rows, document_hash, label):
    import db
    from _qa_safra_uat import labelled_accounts
    accounts = labelled_accounts(rows)
    assert not db.USING_POSTGRES and Path(os.environ["TEMP"]).drive.upper() == "E:"
    with tempfile.TemporaryDirectory(dir=os.environ["TEMP"]) as root:
        with patch.object(db, "DB_PATH", str(Path(root) / "sections.sqlite")), patch.object(db, "get_accounts", return_value=accounts):
            db.init_db()
            data = db.apply_account_and_rates(rows, accounts.iloc[0].to_dict())
            from safra_history import preview_sections, preview_transactions
            pages = preview_sections(data, accounts.iloc[::-1])
            assert len(pages) == len(rows.attrs["safra_sections"])
            for section in rows.attrs["safra_sections"]:
                page = pages[pages.Page.eq(section["source_page"])].iloc[0]
                assert page["IBAN"] == section["source_iban"]
                assert page["Account number"] == section["source_account_number"]
                assert page["Who made the expense"] == "Synthetic " + section["statement_currency"]
                assert page["Currency"] == section["statement_currency"]
                assert page["Opening balance"] == section["opening_balance"]
                assert page["Closing balance"] == section["closing_balance"]
            transactions = preview_transactions(data)
            assert len(transactions) == len(rows)
            if len(rows):
                assert transactions.Page.eq(2).all()
                assert transactions["Who made the expense"].eq("Synthetic USD").all()
                assert transactions.Currency.eq("USD").all()
                assert transactions.IBAN.eq(rows.attrs["safra_sections"][1]["source_iban"]).all()
            with closing(db.get_connection()) as conn:
                for table in ("statement_imports", "statement_balances", "classified_transactions"):
                    assert conn.execute("SELECT COUNT(*) FROM " + table).fetchone()[0] == 0
            real_save = db.save_statement_balance
            calls = []

            def fail_last(*args, **kwargs):
                calls.append(1)
                if len(calls) == len(rows.attrs["safra_sections"]):
                    raise ValueError("Synthetic final-section write failure")
                return real_save(*args, **kwargs)

            with patch.object(db, "save_statement_balance", side_effect=fail_last):
                try:
                    db.save_pending_transactions(data, "authorized-local-test.pdf", document_hash)
                except ValueError:
                    pass
                else:
                    raise AssertionError("Injected write failure was not raised")
            with closing(db.get_connection()) as conn:
                for table in ("statement_imports", "statement_balances", "classified_transactions"):
                    assert conn.execute("SELECT COUNT(*) FROM " + table).fetchone()[0] == 0
            with ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(lambda _: db.save_pending_transactions(data, "authorized-local-test.pdf", document_hash), range(2)))
            assert sorted(result[0] for result in results) == [0, len(rows)]
            assert sum(result[1] for result in results) == 1
            assert db.statement_already_imported(document_hash)
            assert db.statement_balance_exists(document_hash)
            assert db.save_pending_transactions(data, "renamed.pdf", document_hash)[:2] == (0, True)
            assert db.save_pending_transactions(data, "reencoded.pdf", "different-content-hash")[:2] == (0, True)
            real_connection = db.get_connection

            def cursor_only_connection():
                connection = real_connection()
                return SimpleNamespace(cursor=connection.cursor, commit=connection.commit,
                                       rollback=connection.rollback, close=connection.close)

            with patch.object(db, "get_connection", side_effect=cursor_only_connection):
                assert db.save_pending_transactions(data, "driver-contract.pdf", document_hash)[:2] == (0, True)
            if not data.empty:
                revised = data.copy(deep=True)
                revised.loc[revised.index[0], "Description"] += " revised"
                try:
                    db.save_pending_transactions(revised, "partial-overlap.pdf", "revised-hash")
                except ValueError:
                    pass
                else:
                    raise AssertionError("Partially overlapping document silently accepted")
            history = db.get_import_history()
            assert len(history) == len(rows.attrs["safra_sections"])
            with closing(db.get_connection()) as conn:
                saved = conn.execute("SELECT currency, account_name, account_number, period_start, period_end, opening_balance, closing_balance, money_in, money_out, notes, statement_hash FROM statement_balances ORDER BY currency").fetchall()
                assert conn.execute("SELECT COUNT(*) FROM statement_imports").fetchone()[0] == len(saved)
                assert conn.execute("SELECT COUNT(*) FROM classified_transactions").fetchone()[0] == len(rows)
                for record in saved:
                    currency, name, iban, start, end, opening, closing_balance, credits, debits, notes, key = record
                    section, = [s for s in rows.attrs["safra_sections"] if s["statement_currency"] == currency]
                    assert iban == section["source_iban"] and name == "Synthetic " + currency
                    assert json.loads(notes)["account_number"] == section["source_account_number"]
                    assert (start, end) == (section["period_start"], section["period_end"])
                    assert [Decimal(str(v)) for v in (opening, closing_balance, credits, debits)] == [Decimal(section[k]) for k in ("opening_balance", "closing_balance", "money_in", "money_out")]
                    count, signed = conn.execute("SELECT COUNT(*), COALESCE(SUM(amount), 0) FROM classified_transactions WHERE statement_hash = ?", (key,)).fetchone()
                    assert count == section["transaction_count"]
                    assert round(signed, 2) == round(credits - debits, 2)
                    entry = history[history.statement_hash.eq(key)].iloc[0]
                    assert entry.currency == currency and entry.transaction_count == count
                    assert entry.opening_balance == opening and entry.closing_balance == closing_balance
                    assert entry.period_start == start and entry.period_end == end
                    print(f"{label}: {currency} account ending {section['source_account_number'][-4:]} | {start} to {end} | opening {opening:.2f} | closing {closing_balance:.2f} | debits {debits:.2f} | transactions {count} | full IBAN persisted")
    print("PASS: fresh-connection history/balances/transaction reads; atomic rollback; concurrent, renamed and reencoded duplicate prevention")


def main():
    from safra_page_qa import fixture
    from unittest.mock import Mock
    source = Path("app.py").read_text(encoding="utf-8")
    preview, = [n for n in ast.walk(ast.parse(source)) if isinstance(n, ast.Try)
                and len(n.finalbody) == 1 and ast.unparse(n.finalbody[0]) == "progress_slot.empty()"]
    for pages in (fixture(), [fixture()[0]]):
        rows = parsing._parse_safra_pages(pages)

        def classify(frame, memory):
            result = frame.copy()
            result.attrs.clear()
            return result

        slot = Mock()
        env = dict(parse_statement=lambda *args: rows.copy(), apply_account_and_rates=lambda frame, account: frame,
                   flag_duplicates=lambda frame: frame, classify_statement_rows=classify, get_memory=lambda: None,
                   progress_slot=slot, uploaded_statement=SimpleNamespace(name="synthetic.pdf"), file_bytes=b"synthetic", selected_account={})
        exec(compile(ast.Module(body=[preview], type_ignores=[]), "preview", "exec"), env)
        assert env["classified"].attrs == rows.attrs
        assert env["selected_account"] == {} and env["balance_info"] == {}
        if rows.empty:
            assert "match_type" in env["classified"]
        slot.empty.assert_called_once()
    persisted(parsing._parse_safra_pages(fixture()), "synthetic-history", "SYNTHETIC")
    persisted(parsing._parse_safra_pages([fixture()[0]]), "synthetic-zero-only", "ZERO-ONLY")
    source = os.environ.get("SAFRA_HISTORY_EVIDENCE")
    if source:
        raw = Path(source).read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        assert digest == "84e9ba121280ba4b33195653ce27a5dcbb8873624c6f625810b2790681915ca1"
        rows = parsing.parse_pdf(BytesIO(raw))
        assert len(rows) == 6 and rows.source_page.eq(2).all()
        assert rows.Amount.tolist().count(-656.05) == 3 and rows.Amount.tolist().count(-639.25) == 3
        persisted(rows, digest, "AUTHORIZED")
    print("SAFRA HISTORY PASS")


if __name__ == "__main__":
    main()
