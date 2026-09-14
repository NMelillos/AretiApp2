"""Equivalent SQL and frame predicates for active transaction review counters."""
import pandas as pd

TRUE_FLAGS = ("1", "1.0", "true", "yes", "y", "reviewed", "checked")


def sql_predicates():
    status = "LOWER(TRIM(COALESCE(CAST(status AS TEXT), '')))"
    flag = "LOWER(TRIM(COALESCE(CAST(reviewed AS TEXT), '')))"
    truth = ", ".join("'" + value + "'" for value in TRUE_FLAGS)
    reviewed = f"({status} = 'reviewed' OR {flag} IN ({truth}))"
    pending = f"({status} IN ('', 'pending') AND NOT {reviewed})"
    return pending, reviewed


def counts(active_rows):
    status = active_rows.get("status", pd.Series("", index=active_rows.index)).fillna("").astype(str).str.strip().str.casefold()
    flag = active_rows.get("reviewed", pd.Series("", index=active_rows.index)).fillna("").astype(str).str.strip().str.casefold()
    reviewed = status.eq("reviewed") | flag.isin(TRUE_FLAGS)
    pending = status.isin(["", "pending"]) & ~reviewed
    return {"pending": int(pending.sum()), "reviewed": int(reviewed.sum())}
