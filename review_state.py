"""Equivalent SQL and frame predicates for active transaction review counters."""
import pandas as pd

TRUE_FLAGS = ("1", "1.0", "true", "yes", "y", "reviewed", "checked")


def _text(value):
    return "" if value is None or pd.isna(value) else str(value).strip().casefold()


def display_rows(rows):
    """Present legacy review states using the existing counter definition; no writes."""
    out = rows.copy()
    status = out.get("status", pd.Series("", index=out.index)).map(_text)
    flag = out.get("reviewed", pd.Series("", index=out.index)).map(_text).isin(TRUE_FLAGS)
    review_state = status.isin(["", "pending", "reviewed"])
    effective = status.eq("reviewed") | flag
    out["reviewed"] = flag.where(~review_state, effective)
    out["status"] = status.where(~review_state, effective.map({True: "reviewed", False: "pending"}))
    return out


def edit_values(values, before):
    """Resolve the control actually changed, retaining non-review statuses."""
    out = dict(values)
    if not {"status", "reviewed"}.intersection(out):
        return out
    prior = display_rows(pd.DataFrame([before])).iloc[0]
    status = _text(out.get("status", prior["status"]))
    flag = _text(out.get("reviewed", prior["reviewed"])) in TRUE_FLAGS
    if status not in ("", "pending", "reviewed"):
        out.update(status=status, reviewed=flag)
        return out
    status_changed = "status" in out and status != _text(before.get("status"))
    flag_changed = "reviewed" in out and flag != (_text(before.get("reviewed")) in TRUE_FLAGS)
    if status_changed and flag_changed and (status == "reviewed") != flag:
        raise ValueError("Status and Reviewed changes disagree. Choose one review state.")
    reviewed = status == "reviewed" if status_changed else flag if flag_changed else bool(prior["reviewed"])
    out.update(status="reviewed" if reviewed else "pending", reviewed=reviewed)
    return out


def edited_rows(rows, baseline):
    prior = baseline.set_index("id")
    return pd.DataFrame([edit_values(row.to_dict(), prior.loc[row["id"]].to_dict())
                         for _, row in rows.iterrows()], columns=rows.columns, index=rows.index)


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
