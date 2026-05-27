from io import BytesIO
import re
import textwrap

import pandas as pd
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side


REPORT_GROUP_COLUMN = "report_group"
UNASSIGNED_GROUP = "Unassigned reporting group"


def _ordered_unique(values):
    seen = set()
    ordered = []
    for value in values:
        text = str(value).strip()
        key = text.casefold()
        if not text or key in seen:
            continue
        seen.add(key)
        ordered.append(text)
    return ordered


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


def _category_group_map(categories):
    if categories.empty or "category" not in categories.columns:
        return {}
    mapping = {}
    for _, row in categories.iterrows():
        category = str(row.get("category", "")).strip()
        if not category:
            continue
        group = str(row.get(REPORT_GROUP_COLUMN, "")).strip() if REPORT_GROUP_COLUMN in categories.columns else ""
        mapping.setdefault(category.casefold(), group)
    return mapping


def _month_context(expenses):
    end_month = expenses["month"].max() if not expenses.empty else pd.Timestamp.now().to_period("M")
    months = pd.period_range(end=end_month, periods=12, freq="M")
    labels = {month: month.to_timestamp().strftime("%Y-%m") for month in months}
    return months, labels


def _prepare_report_data(transactions, categories, report_group=None):
    tx = transactions.copy()
    for column in ["txn_date", "amount", "amount_usd", "category", "subcategory"]:
        if column not in tx.columns:
            tx[column] = ""

    if tx.empty:
        tx["report_amount"] = []
        return tx, tx.copy(), *(_month_context(tx))

    tx["txn_date"] = pd.to_datetime(tx["txn_date"], errors="coerce")
    tx["category"] = tx["category"].fillna("").astype(str).str.strip()
    tx["subcategory"] = tx["subcategory"].fillna("").astype(str).str.strip()
    tx["report_amount"] = pd.to_numeric(tx["amount_usd"], errors="coerce")
    tx["report_amount"] = tx["report_amount"].fillna(pd.to_numeric(tx["amount"], errors="coerce")).fillna(0)
    tx = tx.dropna(subset=["txn_date"]).copy()

    group_map = _category_group_map(categories)
    tx["report_group"] = tx["category"].map(lambda value: group_map.get(str(value).casefold(), ""))
    tx["report_group"] = tx["report_group"].replace("", UNASSIGNED_GROUP)

    expenses = tx[tx["report_amount"] < 0].copy()
    expenses = expenses[
        expenses["category"].str.strip().str.casefold() != "own funds"
    ].copy()
    expenses["expense_usd"] = expenses["report_amount"].abs()
    expenses["month"] = expenses["txn_date"].dt.to_period("M")

    if report_group:
        expenses = expenses[expenses["report_group"] == report_group].copy()

    months, labels = _month_context(expenses)
    return tx, expenses, months, labels


def _month_totals(frame, months):
    if frame.empty:
        return {month: 0.0 for month in months}
    monthly = frame.groupby("month")["expense_usd"].sum()
    return {month: round(float(monthly.get(month, 0.0)), 2) for month in months}


def _group_order(categories, expenses, report_group=None):
    if report_group:
        return [report_group]
    setup_groups = get_report_groups(categories)
    data_groups = sorted(expenses["report_group"].dropna().astype(str).unique().tolist()) if not expenses.empty else []
    return _ordered_unique(setup_groups + data_groups)


def _build_sections(transactions, categories, report_group=None):
    prepared_tx, expenses, months, month_labels = _prepare_report_data(transactions, categories, report_group)
    groups = _group_order(categories, expenses, report_group)
    all_total = float(expenses["expense_usd"].sum()) if not expenses.empty else 0.0
    active_months = int((expenses.groupby("month")["expense_usd"].sum() > 0).sum()) if not expenses.empty else 0
    average_denominator = max(active_months, 1)

    sections = []
    summary_rows = []

    for group in groups:
        group_expenses = expenses[expenses["report_group"] == group].copy()
        if group_expenses.empty:
            continue

        group_total = float(group_expenses["expense_usd"].sum())
        category_totals = (
            group_expenses.groupby("category")["expense_usd"]
            .sum()
            .sort_values(ascending=False)
        )
        rows = []
        for category, category_total in category_totals.items():
            category_expenses = group_expenses[group_expenses["category"] == category].copy()
            subcategory_totals = (
                category_expenses.groupby("subcategory", dropna=False)["expense_usd"]
                .sum()
                .sort_values(ascending=False)
            )
            if subcategory_totals.empty:
                subcategory_totals = pd.Series({"": category_total})

            first = True
            for subcategory, _ in subcategory_totals.items():
                sub_expenses = category_expenses[category_expenses["subcategory"] == subcategory]
                rows.append({
                    "report_group": group,
                    "category": category if first else "",
                    "category_total": round(float(category_total), 2) if first else None,
                    "category_percent": float(category_total / all_total) if first and all_total else None,
                    "category_average": round(float(category_total) / average_denominator, 2) if first else None,
                    "subcategory": subcategory,
                    "months": _month_totals(sub_expenses, months),
                })
                first = False

        sections.append({
            "group": group,
            "rows": rows,
            "total": round(group_total, 2),
            "percent": float(group_total / all_total) if all_total else 0.0,
            "average": round(group_total / average_denominator, 2),
            "months": _month_totals(group_expenses, months),
        })
        summary_rows.append({
            "report_group": group,
            "total": round(group_total, 2),
            "percent": float(group_total / all_total) if all_total else 0.0,
            "average": round(group_total / average_denominator, 2),
        })

    return prepared_tx, sections, summary_rows, months, month_labels


def _build_report_frame(transactions, categories, report_group=None):
    prepared_tx, sections, _, months, month_labels = _build_sections(transactions, categories, report_group)
    rows = []
    for section in sections:
        for row in section["rows"]:
            if not row["category"]:
                continue
            out = {
                "Report group": section["group"],
                "Category": row["category"],
                "Total expenses for all months ever": row["category_total"] or 0.0,
                "% of category from total": row["category_percent"] or 0.0,
                "Average monthly": row["category_average"] or 0.0,
                "Subcategory": row["subcategory"],
            }
            for month in months:
                out[month_labels[month]] = row["months"].get(month, 0.0)
            rows.append(out)
        total_row = {
            "Report group": section["group"],
            "Category": f"total of {section['group']} ONLY",
            "Total expenses for all months ever": section["total"],
            "% of category from total": section["percent"],
            "Average monthly": section["average"],
            "Subcategory": "",
        }
        for month in months:
            total_row[month_labels[month]] = section["months"].get(month, 0.0)
        rows.append(total_row)
    return pd.DataFrame(rows), prepared_tx


def _style_report_sheet(ws, max_month_col, header_rows, section_rows, total_rows, summary_marker_rows):
    header_fill = PatternFill("solid", fgColor="E8EEF6")
    section_fill = PatternFill("solid", fgColor="DDEDE9")
    total_fill = PatternFill("solid", fgColor="FFF4CC")
    summary_fill = PatternFill("solid", fgColor="EAF2FF")
    thin = Side(style="thin", color="D7DEE8")
    border = Border(left=thin, right=thin, top=thin, bottom=thin)

    widths = {
        "A": 24,
        "B": 34,
        "C": 24,
        "D": 18,
        "E": 18,
        "F": 4,
        "G": 34,
    }
    for column, width in widths.items():
        ws.column_dimensions[column].width = width
    for col in range(8, max_month_col + 1):
        ws.column_dimensions[chr(64 + col)].width = 13

    for row in ws.iter_rows():
        for cell in row:
            cell.alignment = Alignment(vertical="top", wrap_text=True)
            cell.border = border
            if cell.row in header_rows:
                cell.fill = header_fill
                cell.font = Font(bold=True)
            if cell.row in section_rows:
                cell.fill = section_fill
                cell.font = Font(bold=True)
            if cell.row in total_rows:
                cell.fill = total_fill
                cell.font = Font(bold=True)
            if cell.row in summary_marker_rows:
                cell.fill = summary_fill
                cell.font = Font(bold=True)
            if cell.column in {3, 5} or cell.column >= 8:
                cell.number_format = '#,##0.00'
            if cell.column == 4:
                cell.number_format = '0.00%'

    ws.freeze_panes = "A4"


def _write_report_sheet(ws, title, sections, summary_rows, months, month_labels):
    header_rows = set()
    section_rows = set()
    total_rows = set()
    summary_marker_rows = set()

    month_headers = [month_labels[month] for month in months]
    headers = [
        "Report group",
        "Expense category",
        "Total expenses for all months ever",
        "% of category from total",
        "Average monthly",
        "",
        "Subcategory",
        *month_headers,
    ]
    max_col = len(headers)

    row_num = 1
    ws.cell(row_num, 1, title)
    ws.merge_cells(start_row=row_num, start_column=1, end_row=row_num, end_column=max_col)
    ws.cell(row_num, 1).font = Font(bold=True, size=14)
    ws.cell(row_num, 1).alignment = Alignment(vertical="top", wrap_text=True)
    section_rows.add(row_num)
    row_num += 2

    if not sections:
        ws.cell(row_num, 1, "No reviewed expenses match the selected report filters.")
        return

    for section in sections:
        ws.cell(row_num, 1, section["group"])
        ws.merge_cells(start_row=row_num, start_column=1, end_row=row_num, end_column=max_col)
        section_rows.add(row_num)
        row_num += 1

        for col_num, header in enumerate(headers, 1):
            ws.cell(row_num, col_num, header)
        header_rows.add(row_num)
        row_num += 1

        for item in section["rows"]:
            values = [
                item["report_group"],
                item["category"],
                item["category_total"],
                item["category_percent"],
                item["category_average"],
                "",
                item["subcategory"],
                *[item["months"].get(month, 0.0) for month in months],
            ]
            for col_num, value in enumerate(values, 1):
                ws.cell(row_num, col_num, value)
            row_num += 1

        total_values = [
            section["group"],
            f"total of {section['group']} ONLY",
            section["total"],
            section["percent"],
            section["average"],
            "",
            "total",
            *[section["months"].get(month, 0.0) for month in months],
        ]
        for col_num, value in enumerate(total_values, 1):
            ws.cell(row_num, col_num, value)
        total_rows.add(row_num)
        row_num += 3

    ws.cell(row_num, 1, "Summary - total of all categories")
    ws.merge_cells(start_row=row_num, start_column=1, end_row=row_num, end_column=5)
    summary_marker_rows.add(row_num)
    row_num += 1
    summary_headers = ["Report group", "Total", "% of category from total", "Average monthly"]
    for col_num, header in enumerate(summary_headers, 1):
        ws.cell(row_num, col_num, header)
    header_rows.add(row_num)
    row_num += 1
    for item in summary_rows:
        values = [item["report_group"], item["total"], item["percent"], item["average"]]
        for col_num, value in enumerate(values, 1):
            ws.cell(row_num, col_num, value)
        row_num += 1

    _style_report_sheet(ws, max_col, header_rows, section_rows, total_rows, summary_marker_rows)


def build_sample_expenses_report(transactions, categories, report_group=None):
    output = BytesIO()
    prepared_tx, sections, summary_rows, months, month_labels = _build_sections(transactions, categories, report_group)
    title = "Sample expenses report" if not report_group else f"Sample expenses report - {report_group}"

    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        prepared_tx.to_excel(writer, index=False, sheet_name="Reviewed transactions")
        ws = writer.book.create_sheet("Sample expenses report", 0)
        _write_report_sheet(ws, title, sections, summary_rows, months, month_labels)
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


def _format_money(value):
    if value in (None, ""):
        return ""
    try:
        return f"{float(value):,.2f}"
    except Exception:
        return str(value)


def _format_percent(value):
    if value in (None, ""):
        return ""
    try:
        return f"{float(value) * 100:.2f}%"
    except Exception:
        return str(value)


def _pdf_lines_for_sections(sections, summary_rows, title, months, month_labels):
    lines = [
        title,
        "Reports exclude Own funds and use USD equivalent where available.",
        "",
    ]
    if not sections:
        return lines + ["No reviewed expenses match the selected report filters."]

    for section in sections:
        lines.append(section["group"])
        lines.append("Category | Total | % of total | Average monthly | Subcategory")
        lines.append("-" * 120)
        for item in section["rows"]:
            if item["category"]:
                line = (
                    f"{item['category']} | {_format_money(item['category_total'])} | "
                    f"{_format_percent(item['category_percent'])} | {_format_money(item['category_average'])} | "
                    f"{item['subcategory']}"
                )
            else:
                line = f" |  |  |  | {item['subcategory']}"
            lines.extend(textwrap.wrap(line, width=150) or [""])
            month_values = [
                f"{month_labels[month]}: {_format_money(item['months'].get(month, 0.0))}"
                for month in months
                if item["months"].get(month, 0.0)
            ]
            if month_values:
                lines.extend(textwrap.wrap("Months: " + ", ".join(month_values), width=150))
        total_line = (
            f"total of {section['group']} ONLY | {_format_money(section['total'])} | "
            f"{_format_percent(section['percent'])} | {_format_money(section['average'])}"
        )
        lines.extend(["", total_line, ""])

    lines.append("Summary - total of all categories")
    for item in summary_rows:
        lines.append(
            f"{item['report_group']} | {_format_money(item['total'])} | "
            f"{_format_percent(item['percent'])} | {_format_money(item['average'])}"
        )
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
    _, sections, summary_rows, months, month_labels = _build_sections(transactions, categories, report_group)
    return _minimal_pdf(_pdf_lines_for_sections(sections, summary_rows, title, months, month_labels))
