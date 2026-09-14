"""Local synthetic PDF-to-Pending timing; no production telemetry or time target."""
import ast
from contextlib import closing
from io import BytesIO
import json
import os
from pathlib import Path
import tempfile
from time import perf_counter
from unittest.mock import patch


def synthetic_pdf(pages):
    objects = [b"<< /Type /Catalog /Pages 2 0 R >>", b"",
               b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"]
    kids = []
    for page in pages:
        page_id = len(objects) + 1
        kids.append(f"{page_id} 0 R")
        objects.append((f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 842] "
                        f"/Resources << /Font << /F1 3 0 R >> >> /Contents {page_id+1} 0 R >>").encode())
        lines = [line.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)") for line in page.splitlines()]
        stream = ("BT /F1 8 Tf 12 TL 30 800 Td " + " T* ".join(f"({line}) Tj" for line in lines) + " ET").encode("ascii")
        objects.append(f"<< /Length {len(stream)} >>\nstream\n".encode() + stream + b"\nendstream")
    objects[1] = f"<< /Type /Pages /Count {len(pages)} /Kids [{' '.join(kids)}] >>".encode()
    output = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for i, obj in enumerate(objects, 1):
        offsets.append(len(output))
        output.extend(f"{i} 0 obj\n".encode() + obj + b"\nendobj\n")
    xref = len(output)
    output.extend(f"xref\n0 {len(offsets)}\n0000000000 65535 f \n".encode())
    output.extend("".join(f"{offset:010d} 00000 n \n" for offset in offsets[1:]).encode())
    output.extend(f"trailer\n<< /Size {len(offsets)} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode())
    return bytes(output)


def main():
    import db
    import classification
    import parsing
    import safra_history
    import streamlit as st
    from streamlit.testing.v1 import AppTest
    from safra_page_qa import fixture
    from _qa_safra_uat import labelled_accounts
    assert not db.USING_POSTGRES and Path(os.environ["TEMP"]).drive.upper() == "E:"
    pdf_bytes = synthetic_pdf(fixture())
    source = Path("app.py").read_text(encoding="utf-8")
    names = {"editable_pending_table", "_scoped_editor_key", "_editor_row_signature",
             "_with_category_pair_column", "_category_pair_label", "_category_pair_options",
             "get_pending_transactions"}
    selected = [n for n in ast.parse(source).body if isinstance(n, ast.FunctionDef) and n.name in names]
    harness = ('import pandas as pd\nimport streamlit as st\nimport hashlib\nimport db\n'
               'from db import get_pending_transactions as _db_get_pending_transactions\n'
               'get_categories=db.get_categories\n_CATEGORY_PAIR_COLUMN="category_subcategory"\n'
               '_NO_SUBCATEGORY_LABEL="No subcategory"\n_DB_CACHE_TTL_SECONDS=90\n')
    harness += ast.unparse(ast.Module(body=selected, type_ignores=[]))
    harness += ('\npending=get_pending_transactions()\nst.metric("Pending",len(pending))\n'
                'if not pending.empty:\n'
                '    with st.form("synthetic_review"):\n'
                '        editable_pending_table(pending,db.get_categories(),db.get_subcategories(),"synthetic",defer_changes=True)\n'
                '        st.form_submit_button("Apply reviewed transaction edits")\n')
    for trial in range(3):
        with tempfile.TemporaryDirectory(dir=os.environ["TEMP"]) as root, patch.object(db, "DB_PATH", str(Path(root)/"visibility.sqlite")):
            db.init_db()
            db.add_category("Synthetic costs", "Service", "Operations")
            st.cache_data.clear()
            app = AppTest.from_string(harness).run(timeout=30)
            assert not app.exception and app.metric[0].value == "0"
            times = {}
            def timed(name, fn, *args):
                start = perf_counter()
                result = fn(*args)
                times[name] = times.get(name, 0) + (perf_counter()-start)*1000
                return result
            original_parse = parsing._parse_safra_pdf_text
            original_balance = safra_history.section_balances
            start = perf_counter()
            with patch.object(parsing, "_parse_safra_pdf_text", side_effect=lambda *a: timed("transaction_parse_inclusive_ms", original_parse, *a)), patch.object(safra_history, "section_balances", side_effect=lambda *a: timed("section_reconciliation_ms", original_balance, *a)):
                parsed = timed("pdf_parse_and_validation_total_ms", parsing.parse_pdf, BytesIO(pdf_bytes))
            accounts = labelled_accounts(parsed)
            accounts["account_name"] = [f"Synthetic holder {trial}-{i}" for i in range(4)]
            with closing(db.get_connection()) as conn, conn:
                conn.executemany("INSERT INTO account_list (account_name,bank,account_number,currency,rate_type) VALUES (?,?,?,?,?)",
                                 list(accounts[["account_name", "bank", "account_number", "currency", "rate_type"]].itertuples(index=False, name=None)))
            mapped = timed("account_mapping_and_rates_ms", db.apply_account_and_rates, parsed, accounts.iloc[0].to_dict())
            assert mapped.account_name.eq(f"Synthetic holder {trial}-1").all()
            assert mapped.currency.eq("USD").all() and len(mapped) == 6
            classified = timed("classification_ms", classification.classify_transactions, mapped, db.get_memory())
            classified.attrs.update(mapped.attrs)
            real_connection = db.get_connection
            class Connection:
                def __init__(self):
                    self.raw = real_connection()
                def commit(self):
                    result = timed("commit_call_ms", self.raw.commit)
                    times["commit_complete_since_received_ms"] = (perf_counter()-start)*1000
                    return result
                def __getattr__(self, name):
                    return getattr(self.raw, name)
            with patch.object(db, "get_connection", side_effect=Connection):
                result = timed("database_save_total_ms", db.save_pending_transactions, classified, "synthetic.pdf", f"visibility-{trial}")
            assert result[0] == 6
            assert timed("post_import_backfill_ms", db.backfill_missing_usd_amounts) == 0
            timed("cache_invalidation_ms", st.cache_data.clear)
            pending = timed("pending_query_ms", db.get_pending_transactions)
            assert len(pending) == 6 and round(pending.amount.sum(), 2) == -900
            app = timed("streamlit_rerun_and_table_ms", app.run)
            assert not app.exception and app.metric[0].value == "6"
            assert len(app.dataframe) == 1 and len(app.dataframe[0].value) == 6
            times["visible_since_received_ms"] = (perf_counter()-start)*1000
            with closing(db.get_connection()) as conn:
                assert conn.execute("SELECT COUNT(*) FROM statement_balances").fetchone()[0] == 4
                counts = conn.execute("SELECT b.currency, i.transaction_count FROM statement_imports i JOIN statement_balances b ON b.statement_hash=i.statement_hash ORDER BY b.currency").fetchall()
                assert dict(counts) == {"CHF": 0, "EUR": 0, "GBP": 0, "USD": 6}
            assert db.save_pending_transactions(classified, "renamed.pdf", f"visibility-{trial}")[:2] == (0, True)
            assert len(db.get_pending_transactions()) == 6
            print("TIMING " + json.dumps({"trial": trial+1, **{k: round(v, 3) for k,v in times.items()}}, sort_keys=True))
    print("PASS: real synthetic PDF extraction, four independent account names/pages, empty balances, commit/cache/query/Streamlit table visibility, renamed duplicate zero; no performance target or production-cause claim")


if __name__ == "__main__":
    main()
