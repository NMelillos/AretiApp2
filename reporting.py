from io import BytesIO
import re
import textwrap

import pandas as pd


REPORT_GROUP_COLUMN = "report_group"


def get_report_groups(categories):
    if categories.empty or REPORT_GROUP_COLUMN not in categories.columns:
        return []
    groups = (
        categories[REPORT_GROUP_COLUMN]
        .fillna("")
        .astype(str)
        .str.strip()
        .drop_duplicates()
        .tolist()
    )
    return [group for group in groups if group and not group.lower().startswith("0-")]


def _report_categories(categories, report_group=None):
    if categories.empty:
        return categories
    cat = categories.copy()
    if report_group and REPORT_GROUP_COLUMN in cat.columns:
        cat = cat[cat[REPORT_GROUP_COLUMN].fillna("").astype(str).str.strip() == report_group].copy()
    return cat


def _build_report_frame(transactions, categories, report_group=None):
    tx = transactions.copy()
    cat = _report_categories(categories.copy(), report_group)

    category_order = (
        cat["category"].dropna().astype(str).drop_duplicates().tolist()
        if not cat.empty and "category" in cat.columns
        else []
    )

    if tx.empty:
        base = pd.DataFrame({"Category": category_order})
        base["Total expenses for all months ever"] = 0.0
        base["Monthly average"] = 0.0
        base["% of category from total"] = 0.0
        return base, tx

    tx["txn_date"] = pd.to_datetime(tx["txn_date"], errors="coerce")
    tx["report_amount"] = pd.to_numeric(tx.get("amount_usd", tx["amount"]), errors="coerce")
    tx["report_amount"] = tx["report_amount"].fillna(pd.to_numeric(tx["amount"], errors="coerce")).fillna(0)
    tx = tx.dropna(subset=["txn_date"])

    if report_group and category_order:
        tx = tx[tx["category"].fillna("").astype(str).isin(category_order)].copy()

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
    months = pd.period_range(end=end_month, periods=12, freq="M")
    month_keys = [str(month) for month in months]
    month_labels = {str(month): month.to_timestamp().strftime("%b-%y") for month in months}

    if not category_order:
        category_order = sorted(expenses["category"].dropna().astype(str).unique().tolist())
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
    return report, tx


def build_sample_expenses_report(transactions, categories, report_group=None):
    output = BytesIO()
    report, tx = _build_report_frame(transactions, categories, report_group)

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


def _pdf_safe(text):
    return str(text).replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def _pdf_lines_for_report(report, title):
    cols = report.columns.tolist()
    narrow_cols = cols[:4] + cols[4:16]
    lines = [title, ""]
    lines.append(" | ".join(narrow_cols))
    lines.append("-" * 170)
    for _, row in report.iterrows():
        values = []
        for col in narrow_cols:
            value = row.get(col, "")
            if isinstance(value, float):
                value = f"{value:,.2f}"
            values.append(str(value))
        line = " | ".join(values)
        lines.extend(textwrap.wrap(line, width=170) or [""])
    return lines


def _minimal_pdf(lines):
    width, height = 842, 595
    lines_per_page = 44
    pages = [lines[i:i + lines_per_page] for i in range(0, len(lines), lines_per_page)] or [[]]
    objects = []

    def add(obj):
        objects.append(obj)
        return len(objects)

    catalog_id = add("<< /Type /Catalog /Pages 2 0 R >>")
    pages_id = add("")
    page_ids = []
    font_id = add("<< /Type /Font /Subtype /Type1 /BaseFont /Courier >>")

    for page_lines in pages:
        content = ["BT", "/F1 7 Tf", "36 560 Td", "10 TL"]
        for line in page_lines:
            content.append(f"({_pdf_safe(line[:190])}) Tj")
            content.append("T*")
        content.append("ET")
        stream = "\n".join(content)
        content_id = add(f"<< /Length {len(stream.encode('latin-1', errors='ignore'))} >>\nstream\n{stream}\nendstream")
        page_id = add(
            f"<< /Type /Page /Parent {pages_id} 0 R /MediaBox [0 0 {width} {height}] "
            f"/Resources << /Font << /F1 {font_id} 0 R >> >> /Contents {content_id} 0 R >>"
        )
        page_ids.append(page_id)

    objects[pages_id - 1] = (
        f"<< /Type /Pages /Kids [{' '.join(f'{page_id} 0 R' for page_id in page_ids)}] "
        f"/Count {len(page_ids)} >>"
    )

    output = BytesIO()
    output.write(b"%PDF-1.4\n")
    offsets = [0]
    for idx, obj in enumerate(objects, start=1):
        offsets.append(output.tell())
        output.write(f"{idx} 0 obj\n{obj}\nendobj\n".encode("latin-1", errors="ignore"))
    xref = output.tell()
    output.write(f"xref\n0 {len(objects) + 1}\n".encode("ascii"))
    output.write(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        output.write(f"{offset:010d} 00000 n \n".encode("ascii"))
    output.write(
        f"trailer\n<< /Size {len(objects) + 1} /Root {catalog_id} 0 R >>\n"
        f"startxref\n{xref}\n%%EOF".encode("ascii")
    )
    return output.getvalue()


def safe_filename(value):
    return re.sub(r"[^A-Za-z0-9._-]+", "_", str(value)).strip("_") or "report"


def build_pdf_report(transactions, categories, report_group=None):
    title = "Sample expenses report" if not report_group else f"Sample expenses report - {report_group}"
    report, _ = _build_report_frame(transactions, categories, report_group)
    return _minimal_pdf(_pdf_lines_for_report(report, title))
