from io import BytesIO

import pandas as pd


REPORTLAB_AVAILABLE = False


def build_sample_expenses_report(transactions, categories):
    output = BytesIO()
    tx = transactions.copy()
    cat = categories.copy()

    if tx.empty:
        base = cat[["category"]].drop_duplicates().copy() if not cat.empty else pd.DataFrame({"category": []})
        base["Total expenses for all months ever"] = 0.0
        base["Monthly average"] = 0.0
        base["% of category from total"] = 0.0
        with pd.ExcelWriter(output, engine="openpyxl") as writer:
            base.to_excel(writer, index=False, sheet_name="Sample expenses report")
        return output.getvalue()

    tx["txn_date"] = pd.to_datetime(tx["txn_date"], errors="coerce")
    tx["report_amount"] = pd.to_numeric(tx.get("amount_usd", tx["amount"]), errors="coerce").fillna(0)
    tx = tx.dropna(subset=["txn_date"])
    expenses = tx[tx["report_amount"] < 0].copy()
    expenses = expenses[
        expenses["category"].fillna("").astype(str).str.strip().str.casefold() != "own funds"
    ].copy()
    expenses["expense_usd"] = expenses["report_amount"].abs()
    expenses["month"] = expenses["txn_date"].dt.to_period("M")

    end_month = (
        expenses["month"].max()
        if not expenses.empty
        else pd.Timestamp.now().to_period("M")
    )
    months = pd.period_range(
        end=end_month,
        periods=12,
        freq="M",
    )
    month_keys = [str(month) for month in months]
    month_labels = {str(month): month.to_timestamp().strftime("%b-%y") for month in months}

    category_order = (
        cat["category"].dropna().astype(str).drop_duplicates().tolist()
        if not cat.empty and "category" in cat.columns
        else sorted(expenses["category"].dropna().astype(str).unique().tolist())
    )
    category_order = [category for category in category_order if category.strip().casefold() != "own funds"]

    monthly = expenses.pivot_table(
        index="category",
        columns="month",
        values="expense_usd",
        aggfunc="sum",
        fill_value=0,
    )
    monthly.columns = monthly.columns.astype(str)
    for month in month_keys:
        if month not in monthly.columns:
            monthly[month] = 0.0
    monthly = monthly[month_keys]

    totals = expenses.groupby("category")["expense_usd"].sum()
    grand_total = float(totals.sum()) if not totals.empty else 0.0
    active_months = int((monthly.sum(axis=0) > 0).sum()) if not monthly.empty else 0
    average_denominator = max(active_months, 1)

    report = pd.DataFrame({"Category": category_order})
    report["Total expenses for all months ever"] = report["Category"].map(totals).fillna(0).round(2)
    report["Monthly average"] = (
        report["Total expenses for all months ever"] / average_denominator
    ).round(2)
    report["% of category from total"] = (
        report["Total expenses for all months ever"] / grand_total
    ).fillna(0).round(4) if grand_total else 0.0

    month_frame = monthly.reindex(category_order).fillna(0).reset_index(drop=True).round(2)
    report = pd.concat([report, month_frame], axis=1)
    report = report.sort_values("Total expenses for all months ever", ascending=False).reset_index(drop=True)
    report = report.rename(columns=month_labels)

    total_row = {"Category": "TOTAL"}
    total_row["Total expenses for all months ever"] = round(grand_total, 2)
    total_row["Monthly average"] = round(grand_total / average_denominator, 2)
    total_row["% of category from total"] = 1.0 if grand_total else 0.0
    for month in month_keys:
        label = month_labels[month]
        total_row[label] = round(float(report[label].sum()), 2)
    report = pd.concat([report, pd.DataFrame([total_row])], ignore_index=True)

    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        report.to_excel(writer, index=False, sheet_name="Sample expenses report")
        tx.to_excel(writer, index=False, sheet_name="Reviewed transactions")
    return output.getvalue()


def build_excel_report(context):
    output = BytesIO()
    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        context.get("transactions", pd.DataFrame()).to_excel(writer, index=False, sheet_name="Transactions")
    return output.getvalue()


def build_csv_report(context):
    return context.get("transactions", pd.DataFrame()).to_csv(index=False).encode("utf-8")


def build_pdf_report(context):
    raise ValueError("PDF export is not enabled in this version.")
