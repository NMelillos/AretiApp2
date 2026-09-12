"""Safra Setup label matching and guaranteed preview cleanup, synthetic only."""
import ast
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import hashlib
import os
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pandas as pd
import parsing

BASE = "5ee9b76c36ce4b311f92ae8350895dcae6a728bc"


def baseline(name):
    return subprocess.check_output(["git", "show", BASE + ":" + name]).decode("utf-8").replace("\r\n", "\n")


def without_uat_app(source):
    from _qa_safra_history import without_history
    source = without_history("app.py", source)
    original = baseline("app.py")
    lines = original.splitlines(keepends=True)
    start, = [i for i, line in enumerate(lines) if line == "            parsed = parse_statement(file_bytes, uploaded_statement.name)\n"]
    end, = [i for i, line in enumerate(lines) if line == "            progress_slot.empty()\n"]
    old = "".join(lines[start:end + 1])
    new = "            try:\n" + "".join("    " + line for line in lines[start:end])
    new += "            finally:\n    " + lines[end]
    assert source.count(new) == 1
    restored = source.replace(new, old, 1)
    assert restored == original, "App changed outside exact preview cleanup"
    return restored


def without_uat_db(actual):
    from _qa_safra_history import without_history
    actual = without_history("db.py", actual.decode("utf-8")).encode("utf-8")
    source = actual.decode("utf-8")
    original = baseline("db.py")
    find = lambda text: next(n for n in ast.parse(text).body if isinstance(n, ast.FunctionDef) and n.name == "_safra_page_accounts")
    new, old = find(source), find(original)
    assert hashlib.sha256(ast.dump(new).encode()).hexdigest() == "09efa05a8dd770858c8685902bdd0de1317397955587111ae4255ed72ab96f13"
    lines = source.splitlines(keepends=True)
    lines[new.lineno - 1:new.end_lineno] = original.splitlines(keepends=True)[old.lineno - 1:old.end_lineno]
    restored = "".join(lines)
    assert restored == original, "DB changed outside exact Safra matching"
    return restored.encode()


def labelled_accounts(rows):
    from safra_page_qa import setup_accounts
    accounts = setup_accounts(rows)
    accounts["account_number"] = [
        "Current account " + s["statement_currency"] + " / IBAN " +
        " ".join(s["source_iban"][i:i+4] for i in range(0, len(s["source_iban"]), 4))
        for s in rows.attrs["safra_sections"]
    ]
    return accounts


def cleanup_check():
    source = Path("app.py").read_text(encoding="utf-8")
    without_uat_app(source)
    candidates = [n for n in ast.walk(ast.parse(source)) if isinstance(n, ast.Try)
                  and len(n.finalbody) == 1 and ast.unparse(n.finalbody[0]) == "progress_slot.empty()"]
    assert len(candidates) == 1
    code = compile(ast.Module(body=candidates, type_ignores=[]), "preview-cleanup", "exec")
    steps = ("parse_statement", "apply_account_and_rates", "flag_duplicates", "classify_statement_rows")
    for failure in (None, *steps):
        slot = Mock()
        env = {name: Mock(return_value=pd.DataFrame()) for name in steps}
        if failure:
            env[failure].side_effect = ValueError("synthetic failure")
        env.update(progress_slot=slot, file_bytes=b"synthetic", uploaded_statement=SimpleNamespace(name="synthetic.pdf"),
                   selected_account={}, get_memory=lambda: pd.DataFrame())
        env.update(parse_statement_balance=lambda *args: {}, accounts=pd.DataFrame(),
                   account_options=lambda *args: (["Synthetic"], {"Synthetic": {}}),
                   guess_account_index=lambda *args: 0, st=Mock(), re=__import__("re"),
                   is_amex_cardholder_statement=lambda *args: False)
        env["st"].selectbox.return_value = "Synthetic"
        try:
            exec(code, env)
        except ValueError:
            assert failure is not None
        else:
            assert failure is None
        slot.empty.assert_called_once_with()
    print("PASS: processing indicator cleared on success and every preview-stage failure")


def main():
    import db
    from safra_page_qa import fixture
    rows = parsing._parse_safra_pages(fixture())
    accounts = labelled_accounts(rows)
    # Reproduce the deployed failure with no database access, then exercise
    # the corrected resolver on the same Setup representation.
    old_node = next(n for n in ast.parse(baseline("db.py")).body if isinstance(n, ast.FunctionDef) and n.name == "_safra_page_accounts")
    import re
    old_env = {"re": re}
    exec(compile(ast.Module(body=[old_node], type_ignores=[]), "baseline-resolver", "exec"), old_env)
    with patch.object(db, "get_connection", side_effect=AssertionError("Unexpected database access")):
        try:
            old_env["_safra_page_accounts"](rows, accounts)
        except ValueError:
            pass
        else:
            raise AssertionError("Baseline failure not reproduced")
        for variants in (accounts, accounts.iloc[::-1], accounts.assign(account_number=accounts.account_number.str.lower()),
                         accounts.assign(currency=accounts.currency.map(lambda c: " " + c + " "))):
            assert db._safra_page_accounts(rows, variants)[2]["account_name"] == "Synthetic USD"
        for section in rows.attrs["safra_sections"]:
            assert len(rows[rows.source_page.eq(section["source_page"])]) == (6 if section["source_page"] == 2 else 0)
    assert not db.USING_POSTGRES and Path(os.environ["TEMP"]).drive.upper() == "E:"
    with tempfile.TemporaryDirectory(dir=os.environ["TEMP"]) as root:
        with patch.object(db, "DB_PATH", str(Path(root) / "synthetic.sqlite")), patch.object(db, "get_accounts", return_value=accounts):
            db.init_db()
            for invalid in (pd.concat([accounts, accounts.iloc[[1]]]), accounts.iloc[:1],
                            accounts.assign(account_number=accounts.account_number.str[-4:]),
                            accounts.assign(account_number=accounts.account_number.str.replace("Current account USD", "Current account EUR", regex=False))):
                with patch.object(db, "get_accounts", return_value=invalid):
                    try:
                        db.apply_account_and_rates(rows, accounts.iloc[0].to_dict())
                    except ValueError:
                        pass
                    else:
                        raise AssertionError("Ambiguous, partial or mismatched account accepted")
            data = db.apply_account_and_rates(rows, accounts.iloc[0].to_dict())
            assert data.account_number.eq(accounts.iloc[1].account_number).all()
            assert data.currency.eq("USD").all() and len(data) == 6
            with closing(db.get_connection()) as conn:
                assert conn.execute("SELECT COUNT(*) FROM classified_transactions").fetchone()[0] == 0
                assert conn.execute("SELECT COUNT(*) FROM statement_imports").fetchone()[0] == 0
            with ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(lambda _: db.save_pending_transactions(data, "synthetic.pdf", "uat-synthetic"), range(2)))
            assert sorted(result[0] for result in results) == [0, 6]
            assert db.save_pending_transactions(data, "renamed.pdf", "uat-synthetic")[0] == 0
            assert db.save_pending_transactions(data, "retry.pdf", "uat-new-hash")[0] == 0
            with closing(db.get_connection()) as conn:
                saved = conn.execute("SELECT amount, currency, account_number, status FROM classified_transactions ORDER BY id").fetchall()
            assert saved == [(row.Amount, "USD", accounts.iloc[1].account_number, "pending") for row in rows.itertuples()]
    cleanup_check()
    without_uat_db(Path("db.py").read_bytes().replace(b"\r\n", b"\n"))
    print("SAFRA UAT PASS: exact full-IBAN labels, currency, ambiguity rejection, no-write failures/preview, six persistent rows and idempotent retries")


if __name__ == "__main__":
    main()
