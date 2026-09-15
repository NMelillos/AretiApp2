"""Canonical Safra history identity without rewriting stored display fields."""
from contextlib import closing
import os
from pathlib import Path
import subprocess
import tempfile
from unittest.mock import patch

OLD = "                   COALESCE(ft.account_number, sb.account_number, '') AS account_number,"
NEW = """                   CASE WHEN sb.source LIKE 'Safra document %'
                        THEN sb.account_number
                        ELSE COALESCE(ft.account_number, sb.account_number, '')
                   END AS account_number,"""


def without_history_identity(source):
    source = source.replace(b"\r\n", b"\n")
    text = source.decode()
    start = text.index("def get_import_history():")
    end = text.index("\ndef get_import_transaction_audit():", start)
    section = text[start:end]
    if NEW in section:
        assert section.count(NEW) == 1
        section = section.replace(NEW, OLD, 1)
    prior = subprocess.check_output(["git", "show", "dc5d9c83c3e527808072107791ba1f22828bb02f:db.py"]).decode().replace("\r\n", "\n")
    a = prior.index("def get_import_history():")
    b = prior.index("\ndef get_import_transaction_audit():", a)
    assert section == prior[a:b], "History changed beyond the exact Safra identity projection"
    return (text[:start] + section + text[end:]).encode()


def main():
    import db
    import parsing
    from safra_page_qa import fixture
    from _qa_safra_uat import labelled_accounts
    assert not db.USING_POSTGRES and Path(os.environ["TEMP"]).drive.upper() == "E:"
    rows = parsing._parse_safra_pages(fixture())
    accounts = labelled_accounts(rows)
    expected = {s["statement_currency"]: s["source_iban"] for s in rows.attrs["safra_sections"]}
    with tempfile.TemporaryDirectory(dir=os.environ["TEMP"]) as root:
        with patch.object(db, "DB_PATH", str(Path(root) / "identity.sqlite")), patch.object(db, "get_accounts", return_value=accounts):
            db.init_db()
            mapped = db.apply_account_and_rates(rows, {})
            assert db.get_import_history().empty
            assert db.save_pending_transactions(mapped, "synthetic.pdf", "synthetic-identity") == (6, False, 0)
            with closing(db.get_connection()) as conn:
                before = conn.execute("SELECT * FROM classified_transactions ORDER BY id").fetchall()
                assert conn.execute("SELECT DISTINCT account_number FROM classified_transactions").fetchall() == [(accounts.iloc[1].account_number,)]
            history = db.get_import_history()
            assert len(history) == 4 and history.transaction_count.sum() == 6
            assert dict(zip(history.currency, history.account_number)) == expected, "Safra history mixes Setup labels with canonical IBANs"
            assert history[history.currency.ne("USD")].transaction_count.eq(0).all()
            assert history[history.currency.eq("USD")].money_out.iloc[0] == 900
            assert db.save_pending_transactions(mapped, "renamed.pdf", "synthetic-identity")[:2] == (0, True)
            with closing(db.get_connection()) as conn:
                assert before == conn.execute("SELECT * FROM classified_transactions ORDER BY id").fetchall()
    without_history_identity(Path("db.py").read_bytes())
    print("PASS: canonical four-account history, unchanged stored Setup labels, zero-activity sections and duplicate safety")


if __name__ == "__main__":
    main()
