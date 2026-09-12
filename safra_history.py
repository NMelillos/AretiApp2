"""Validated Safra account-section history, including sections without bookings."""
from decimal import Decimal
from datetime import datetime
import hashlib
import json
import re


def preview_sections(frame, accounts):
    import pandas as pd
    from db import _safra_page_accounts
    mapped = _safra_page_accounts(frame, accounts)
    return pd.DataFrame([{
        "Page": section["source_page"],
        "Who made the expense": mapped[section["source_page"]]["account_name"],
        "Bank": mapped[section["source_page"]]["bank"],
        "Account number": section["source_account_number"],
        "IBAN": section["source_iban"],
        "Currency": section["statement_currency"],
        "Exchange type": mapped[section["source_page"]].get("rate_type", ""),
        "Period start": section["period_start"], "Period end": section["period_end"],
        "Opening balance": section["opening_balance"], "Closing balance": section["closing_balance"],
        "Money in": section["money_in"], "Money out": section["money_out"],
        "Transactions": section["transaction_count"],
    } for section in frame.attrs["safra_sections"]])


def preview_transactions(frame):
    columns = {
        "source_page": "Page", "Date": "Date", "Description": "Description", "Amount": "Amount",
        "account_name": "Who made the expense", "bank": "Bank",
        "source_account_number": "Account number", "source_iban": "IBAN", "currency": "Currency",
        "rate_type": "Exchange type", "amount_usd": "USD amount",
        "suggested_category": "suggested_category", "suggested_subcategory": "suggested_subcategory",
        "match_type": "match_type", "confidence": "confidence", "dup_flag": "dup_flag",
        "duplicate_reason": "duplicate_reason",
    }
    return frame[[key for key in columns if key in frame]].rename(columns=columns)


def section_balances(section, frame, text):
    lines = [re.sub(r"\s+", " ", line).strip() for line in text.splitlines()]
    dates = re.search(r"Account statement in [A-Z]{3} (\d{2}\.\d{2}\.\d{4}) to (\d{2}\.\d{2}\.\d{4})", "\n".join(lines))
    period = [datetime.strptime(value, "%d.%m.%Y").date().isoformat() for value in dates.groups()]
    balances = {}
    for line in lines:
        match = re.fullmatch(r"\d{2}\.\d{2}\.\d{4} Balance( carried forward)?( in your favour| in our favour)? ([+-]?[\d ]+,\d{2})", line)
        if match:
            carried, side, value = match.groups()
            value = Decimal(value.replace(" ", "").replace(",", "."))
            if side == " in our favour":
                value = -value
            balances["opening_balance" if carried else "closing_balance"] = value
    closing = balances["closing_balance"]
    if frame.empty and "opening_balance" not in balances:
        # The validated no-bookings declaration establishes unchanged balances.
        balances["opening_balance"] = closing
    credits = sum((Decimal(str(v)) for v in frame.Amount if v > 0), Decimal(0))
    debits = -sum((Decimal(str(v)) for v in frame.Amount if v < 0), Decimal(0))
    if balances["opening_balance"] + credits - debits != closing:
        raise ValueError("Safra section balance reconciliation failed.")
    section.update(period_start=period[0], period_end=period[1], transaction_count=len(frame),
                   money_in=str(credits), money_out=str(debits),
                   **{key: str(value) for key, value in balances.items()})


def save_sections(db, frame, statement_name, document_hash):
    sections = frame.attrs.get("safra_sections")
    if not sections:
        raise ValueError("Missing Safra section evidence.")
    accounts = db._safra_page_accounts(frame, db.get_accounts())
    prepared = []
    for section in sections:
        rows = frame[frame.source_page.eq(section["source_page"])].copy()
        rows.attrs.clear()
        if len(rows) != section["transaction_count"]:
            raise ValueError("Safra section transaction population changed.")
        credits = sum((Decimal(str(v)) for v in rows.Amount if v > 0), Decimal(0))
        debits = -sum((Decimal(str(v)) for v in rows.Amount if v < 0), Decimal(0))
        if (credits != Decimal(section["money_in"]) or debits != Decimal(section["money_out"])
                or Decimal(section["opening_balance"]) + credits - debits != Decimal(section["closing_balance"])):
            raise ValueError("Safra section reconciliation changed before saving.")
        identity = [section["source_iban"], section["statement_currency"], section["period_start"],
                    section["period_end"], section["opening_balance"], section["closing_balance"],
                    sorted((str(r.Date), str(r.Description), str(Decimal(str(r.Amount)).quantize(Decimal("0.01"))))
                           for r in rows.itertuples())]
        key = hashlib.sha256(json.dumps(identity, ensure_ascii=True).encode()).hexdigest()
        balance = {key: section[key] for key in ("period_start", "period_end", "opening_balance", "closing_balance", "money_in", "money_out")}
        balance.update(currency=section["statement_currency"], source="Safra document " + document_hash,
                       notes=json.dumps({"source_page": section["source_page"], "account_number": section["source_account_number"]}))
        account = dict(accounts[section["source_page"]], account_number=section["source_iban"])
        prepared.append((key, rows, balance, account))
    if len({item[0] for item in prepared}) != len(prepared):
        raise ValueError("Repeated Safra account section.")
    conn = db.get_connection()
    cur = conn.cursor()
    inserted = 0
    saved_sections = 0
    try:
        for key, rows, balance, account in prepared:
            count, duplicate, skipped = db.save_pending_transactions(rows, statement_name, key, _connection=conn)
            if duplicate:
                existing = sum(cur.execute("SELECT COUNT(*) FROM statement_imports WHERE statement_hash = ?", (item[0],)).fetchone()[0] for item in prepared)
                if saved_sections or existing != len(prepared):
                    raise ValueError("Safra document partially overlaps Import History; no changes saved.")
                conn.rollback()
                return 0, True, 0
            if skipped or count != len(rows):
                raise ValueError("Safra section overlaps existing transactions; no partial import saved.")
            db.save_statement_balance(key, statement_name, balance, account, _connection=conn)
            inserted += count
            saved_sections += 1
        conn.commit()
        return inserted, False, 0
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
        conn.close()
