"""Synthetic Excel imports only; all connections restricted to a temporary SQLite DB."""
import ast
import os
from io import BytesIO
from pathlib import Path
import sqlite3
import tempfile
from contextlib import closing

os.environ.pop("DATABASE_URL", None)
os.environ.pop("POSTGRES_URL", None)
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"

import pandas as pd


def workbook(rows):
    buffer = BytesIO()
    pd.DataFrame(rows).to_excel(buffer, index=False, header=False)
    buffer.seek(0)
    return buffer


def main():
    with tempfile.TemporaryDirectory(prefix="areti-chf-qa-") as directory:
        target = str(Path(directory) / "rates.sqlite")
        os.environ["ARETI_DB_PATH"] = target
        import db
        assert not db.USING_POSTGRES and db.DB_PATH == target
        def connect():
            assert db.DB_PATH == target and not db.USING_POSTGRES
            return sqlite3.connect(target)
        db.get_connection = connect
        db.init_db()
        print(f"DATABASE: disposable SQLite {target}; production URL fallbacks removed")
        rows = [["Currency", pd.Timestamp("2026-08-01")],
                ["USD/USD", 1], ["EUR/USD", 1.1], ["GBP/USD", 1.3],
                ["ILS/USD", 0.27], ["KZT/USD", 0.002], ["RUB/USD", 0.012], ["CHF/USD", 1.25]]
        count = db.replace_rates_from_excel(workbook(rows))
        with closing(connect()) as conn:
            stored = pd.read_sql_query("SELECT rate_month, rate_type, rate_value FROM rates ORDER BY rate_type", conn)
        print(f"IMPORT: reported={count}; persisted={len(stored)}; types={stored.rate_type.tolist()}")
        assert count == len(stored) == 7
        assert db.get_latest_rate("CHF/USD", "2026-08-18") == 1.25
        assert db.get_latest_rate("USD/USD", "2026-08-18") == 1
        assert set(stored.rate_month) == {"2026-08-01"}
        # Execute the actual Setup cached reader without importing the application.
        import streamlit as st
        tree = ast.parse(Path("app.py").read_text(encoding="utf-8"))
        nodes = [n for n in tree.body if isinstance(n, ast.FunctionDef)
                 and n.name in {"get_rates", "missing_account_rate_types"}]
        env = {"st": st, "pd": pd, "_DB_CACHE_TTL_SECONDS": 90, "_db_get_rates": db.get_rates}
        exec(compile(ast.Module(body=nodes, type_ignores=[]), "app.py", "exec"), env)
        accounts = pd.DataFrame([{"currency": "CHF"}])
        for _ in range(2):
            data = env["get_rates"]()
            assert "CHF/USD" in set(data.rate_type)
            assert env["missing_account_rate_types"](accounts, data) == []
        print("PASS: Setup source immediate and simulated rerun; fresh connection committed CHF")
        previous = db.get_rates().sort_values("rate_type").reset_index(drop=True)
        assert db.replace_rates_from_excel(workbook(rows)) == 7
        pd.testing.assert_frame_equal(previous, db.get_rates().sort_values("rate_type").reset_index(drop=True))
        rows[-1][1] = 1.3
        assert db.replace_rates_from_excel(workbook(rows)) == 7
        env["get_rates"].clear()
        updated = env["get_rates"]()
        assert updated.loc[updated.rate_type.eq("CHF/USD"), "rate_value"].iloc[0] == 1.3
        pd.testing.assert_frame_equal(previous[previous.rate_type.ne("CHF/USD")].reset_index(drop=True),
                                      updated[updated.rate_type.ne("CHF/USD")].sort_values("rate_type").reset_index(drop=True))
        for label in (" chf / usd ", "CH\u200bF/USD", "CHF\u00a0/USD"):
            assert db._normalize_rate_type(label) == "CHF/USD"
        assert db._rate_from_label_value("USD/CHF", 0.8) == ("CHF/USD", 1.25)
        assert db.replace_rates_from_excel(workbook(rows + [["CAD/USD", "not-a-number"]])) == 7
        assert len(db.get_rates()) == 7
        dated = [["Currency", pd.Timestamp("2026-07-01"), pd.Timestamp("2026-08-01")], ["CHF/USD", 1.2, 1.3]]
        assert db.replace_rates_from_excel(workbook(dated)) == 2
        assert db.get_latest_rate("CHF/USD", "2026-07-31") == 1.2
        assert db.get_latest_rate("CHF/USD", "2026-08-01") == 1.3
        # Restore seven currencies before the single downstream account check.
        db.replace_rates_from_excel(workbook(rows))
        result = db.apply_account_and_rates(pd.DataFrame([{"Amount": 10, "Date": "2026-08-18"}]),
                                            {"currency": "CHF", "rate_type": "CHF/USD", "account_name": "Synthetic CHF"})
        assert result.amount_usd.iloc[0] == 13
        assert len(db.get_rates()) == 7
        print("PASS: idempotent reimport, CHF update, other six rates unchanged, normalization, invalid numeric rejection, effective dates, downstream CHF")
        print("RATES_CHF_PERSISTENCE_QA_COMPLETE")


if __name__ == "__main__":
    main()
