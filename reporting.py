# =========================
# FILE: reporting.py
# =========================
from io import BytesIO

import pandas as pd
from openpyxl.styles import Alignment, Border, Font, Side

from utils import format_currency

try:
    from reportlab.lib import colors
    from reportlab.lib.enums import TA_CENTER, TA_LEFT
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import mm
    from reportlab.platypus import (
        SimpleDocTemplate,
        Paragraph,
        Spacer,
        Table,
        TableStyle,
        PageBreak,
    )
    REPORTLAB_AVAILABLE = True
except Exception:
    REPORTLAB_AVAILABLE = False


def build_export_frames(context: dict):
    period_label = context["period_label"]

    transactions_export = context["report_df"].copy()
    transactions_export["period"] = period_label
    transactions_export["month"] = transactions_export["txn_date"].dt.to_period("M").astype(str)

    summary_export = context["summary"].copy()
    if not summary_export.empty:
        summary_export["period"] = period_label
        summary_export["month"] = "ALL"

    monthly_export = context["monthly_total"].copy()
    if not monthly_export.empty:
        monthly_export["period"] = period_label

    recurring_export = context["recurring_df"].copy()
    if not recurring_export.empty:
        recurring_export["period"] = period_label
        recurring_export["month"] = "ALL"

    seasonal_export = context["seasonal_df"].copy()
    if not seasonal_export.empty:
        seasonal_export["period"] = period_label
        seasonal_export["month"] = seasonal_export["month_name"]

    kpi_data = [
        ["Total Income", context["kpis"]["total_income"]],
        ["Total Expenses", context["kpis"]["total_expenses"]],
        ["Net Result", context["kpis"]["net_result"]],
        ["Burn Rate", context["kpis"]["burn_rate"]],
        ["Savings Rate %", context["kpis"]["savings_rate"]],
        ["Average Monthly Income", context["kpis"]["avg_monthly_income"]],
        ["Average Monthly Expenses", context["kpis"]["avg_monthly_expenses"]],
        ["Months Covered", context["kpis"]["months_covered"]],
    ]
    kpi_export = pd.DataFrame(kpi_data, columns=["kpi", "value"])
    kpi_export["period"] = period_label
    kpi_export["month"] = context["month_coverage"]

    return {
        "transactions": transactions_export,
        "summary": summary_export,
        "monthly": monthly_export,
        "recurring": recurring_export,
        "seasonal": seasonal_export,
        "kpis": kpi_export,
    }


def _with_export_amount(df: pd.DataFrame) -> pd.DataFrame:
    work = df.copy()
    fallback_amount = pd.to_numeric(work.get("amount", pd.Series(dtype="float64")), errors="coerce")
    if "usd_amount" in work.columns:
        usd_amount = pd.to_numeric(work["usd_amount"], errors="coerce")
        work["export_amount"] = usd_amount.fillna(fallback_amount)
    else:
        work["export_amount"] = fallback_amount
    return work


def build_sample_expenses_report_frame(saved_df: pd.DataFrame) -> pd.DataFrame:
    if saved_df is None or saved_df.empty:
        return pd.DataFrame(columns=["Category", "Total expenses for all months ever", "% of category from total"])

    work = _with_export_amount(saved_df)
    work["txn_date"] = pd.to_datetime(work.get("txn_date"), errors="coerce")
    work = work.dropna(subset=["txn_date"]).copy()
    if work.empty:
        return pd.DataFrame(columns=["Category", "Total expenses for all months ever", "% of category from total"])

    work["category"] = work.get("category", "").fillna("").astype(str).str.strip()
    work.loc[work["category"] == "", "category"] = "Uncategorized"

    expenses = work[work["export_amount"] < 0].copy()
    expenses = expenses[expenses["category"].str.casefold() != "own funds"]
    if expenses.empty:
        return pd.DataFrame(columns=["Category", "Total expenses for all months ever", "% of category from total"])

    expenses["expense_value"] = expenses["export_amount"].abs()
    expenses["month_period"] = expenses["txn_date"].dt.to_period("M")

    category_totals = (
        expenses.groupby("category", dropna=False)["expense_value"]
        .sum()
        .reset_index(name="Total expenses for all months ever")
        .sort_values("Total expenses for all months ever", ascending=False)
    )

    grand_total = float(category_totals["Total expenses for all months ever"].sum())
    if grand_total > 0:
        category_totals["% of category from total"] = (
            category_totals["Total expenses for all months ever"] / grand_total
        )
    else:
        category_totals["% of category from total"] = 0.0

    last_12_months = sorted(expenses["month_period"].dropna().unique())[-12:]
    month_totals = (
        expenses[expenses["month_period"].isin(last_12_months)]
        .groupby(["category", "month_period"], dropna=False)["expense_value"]
        .sum()
        .unstack(fill_value=0.0)
        if last_12_months else pd.DataFrame(index=category_totals["category"])
    )

    month_totals = month_totals.reindex(index=category_totals["category"], fill_value=0.0)
    for month_period in last_12_months:
        if month_period not in month_totals.columns:
            month_totals[month_period] = 0.0

    month_totals = month_totals.reindex(columns=last_12_months, fill_value=0.0)
    month_totals.columns = [month.strftime("%b-%y") for month in month_totals.columns]

    return category_totals.rename(columns={"category": "Category"}).merge(
        month_totals.reset_index().rename(columns={"index": "Category", "category": "Category"}),
        on="Category",
        how="left",
    )


def _write_sample_expenses_sheet(writer, saved_df: pd.DataFrame):
    sheet_name = "Sample Expenses Report"
    report_df = build_sample_expenses_report_frame(saved_df)
    report_df = report_df.fillna(0)

    workbook = writer.book
    worksheet = workbook.create_sheet(title=sheet_name)
    writer.sheets[sheet_name] = worksheet

    month_columns = report_df.columns[3:].tolist()
    header_row_excel = 3
    data_start_row_excel = 4
    total_row_excel = data_start_row_excel + len(report_df)
    last_column_excel = len(report_df.columns)

    worksheet.cell(row=1, column=1, value="sample expenses report")
    worksheet.cell(row=2, column=4, value="Month (the last 12 months should be shown)")

    if last_column_excel >= 4:
        worksheet.merge_cells(start_row=2, start_column=4, end_row=2, end_column=last_column_excel)

    worksheet.cell(row=header_row_excel, column=2, value="Total expenses for all months ever")
    worksheet.cell(row=header_row_excel, column=3, value="% of category from total")
    for offset, month_column in enumerate(month_columns, start=4):
        worksheet.cell(row=header_row_excel, column=offset, value=month_column)

    for row_offset, (_, report_row) in enumerate(report_df.iterrows(), start=data_start_row_excel):
        worksheet.cell(row=row_offset, column=1, value=report_row["Category"])
        worksheet.cell(row=row_offset, column=2, value=float(report_row["Total expenses for all months ever"]))
        worksheet.cell(row=row_offset, column=3, value=float(report_row["% of category from total"]))
        for offset, month_column in enumerate(month_columns, start=4):
            worksheet.cell(row=row_offset, column=offset, value=float(report_row[month_column]))

    grand_total = float(report_df["Total expenses for all months ever"].sum()) if not report_df.empty else 0.0
    worksheet.cell(row=total_row_excel, column=2, value=grand_total)
    worksheet.cell(row=total_row_excel, column=3, value=1 if grand_total else 0)

    for offset, month_column in enumerate(month_columns, start=4):
        month_total = float(report_df[month_column].sum()) if not report_df.empty else 0.0
        worksheet.cell(row=total_row_excel, column=offset, value=month_total)

    worksheet.cell(
        row=total_row_excel + 3,
        column=1,
        value="Above report should include every expense except 'own funds'.",
    )
    worksheet.cell(
        row=total_row_excel + 5,
        column=1,
        value="Sorting should always be expense with highest total amount first.",
    )

    header_font = Font(bold=True)
    title_font = Font(bold=True, size=12)
    center_alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
    left_alignment = Alignment(horizontal="left", vertical="center", wrap_text=True)
    thin_border = Border(
        left=Side(style="thin", color="000000"),
        right=Side(style="thin", color="000000"),
        top=Side(style="thin", color="000000"),
        bottom=Side(style="thin", color="000000"),
    )

    worksheet.cell(row=1, column=1).font = title_font
    worksheet.cell(row=2, column=4).font = header_font
    worksheet.cell(row=2, column=4).alignment = center_alignment
    worksheet.cell(row=total_row_excel, column=2).font = header_font
    worksheet.cell(row=total_row_excel, column=3).font = header_font

    for column_index in range(2, last_column_excel + 1):
        header_cell = worksheet.cell(row=header_row_excel, column=column_index)
        header_cell.font = header_font
        header_cell.alignment = center_alignment
        header_cell.border = thin_border

    for row_index in range(data_start_row_excel, total_row_excel + 1):
        for column_index in range(1, last_column_excel + 1):
            cell = worksheet.cell(row=row_index, column=column_index)
            cell.border = thin_border
            if column_index == 1:
                cell.alignment = left_alignment
            else:
                cell.alignment = center_alignment

    for row_index in range(data_start_row_excel, total_row_excel):
        worksheet.cell(row=row_index, column=2).number_format = '#,##0.00'
        worksheet.cell(row=row_index, column=3).number_format = '0%'

    worksheet.cell(row=total_row_excel, column=2).number_format = '#,##0.00'
    worksheet.cell(row=total_row_excel, column=3).number_format = '0%'
    for offset in range(4, last_column_excel + 1):
        worksheet.cell(row=total_row_excel, column=offset).number_format = '#,##0.00'
        for row_index in range(data_start_row_excel, total_row_excel):
            worksheet.cell(row=row_index, column=offset).number_format = '#,##0.00'

    column_widths = {
        1: 28,
        2: 24,
        3: 18,
    }
    for column_index in range(4, last_column_excel + 1):
        column_widths[column_index] = 14

    for column_index, width in column_widths.items():
        worksheet.column_dimensions[worksheet.cell(row=1, column=column_index).column_letter].width = width

    worksheet.row_dimensions[1].height = 22
    worksheet.row_dimensions[2].height = 22
    worksheet.row_dimensions[3].height = 34


def build_excel_report(context: dict, saved_df: pd.DataFrame | None = None) -> bytes:
    exports = build_export_frames(context)
    output = BytesIO()

    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        exports["transactions"].to_excel(writer, index=False, sheet_name="Transactions")

        _write_sample_expenses_sheet(
            writer,
            saved_df if saved_df is not None else context["report_df"],
        )

        if not exports["summary"].empty:
            exports["summary"].to_excel(writer, index=False, sheet_name="Category Summary")

        if not exports["monthly"].empty:
            exports["monthly"].to_excel(writer, index=False, sheet_name="Monthly Summary")

        if not exports["recurring"].empty:
            exports["recurring"].to_excel(writer, index=False, sheet_name="Recurring")

        if not exports["seasonal"].empty:
            exports["seasonal"].to_excel(writer, index=False, sheet_name="Seasonality")

        exports["kpis"].to_excel(writer, index=False, sheet_name="KPIs")

    return output.getvalue()


def build_csv_report(context: dict) -> bytes:
    exports = build_export_frames(context)
    return exports["transactions"].to_csv(index=False).encode("utf-8")


def build_access_backup_frame(saved_df: pd.DataFrame) -> pd.DataFrame:
    export_columns = [
        "txn_date",
        "original_description",
        "normalized_description",
        "amount",
        "currency",
        "usd_amount",
        "beneficiary",
        "transaction_type",
        "category",
        "subcategory",
        "match_type",
        "confidence",
        "reviewed",
        "account_name",
        "account_number",
        "bank",
        "source_occurrence",
        "created_at",
    ]
    rename_map = {
        "txn_date": "transaction_date",
        "original_description": "description",
    }

    work = saved_df.copy()
    for column in export_columns:
        if column not in work.columns:
            work[column] = ""

    backup_df = work[export_columns].rename(columns=rename_map)
    if "transaction_date" in backup_df.columns:
        backup_df["transaction_date"] = pd.to_datetime(
            backup_df["transaction_date"], errors="coerce"
        ).dt.strftime("%Y-%m-%d")
        backup_df["transaction_date"] = backup_df["transaction_date"].fillna("")

    return backup_df


def build_access_backup_csv(saved_df: pd.DataFrame) -> bytes:
    return build_access_backup_frame(saved_df).to_csv(index=False).encode("utf-8")


def build_access_backup_excel(saved_df: pd.DataFrame) -> bytes:
    output = BytesIO()
    backup_df = build_access_backup_frame(saved_df)

    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        backup_df.to_excel(writer, index=False, sheet_name="Transactions")

    return output.getvalue()


def build_memory_backup_frame(memory_df: pd.DataFrame) -> pd.DataFrame:
    if memory_df is None or memory_df.empty:
        return pd.DataFrame(
            columns=[
                "id",
                "normalized_description",
                "beneficiary",
                "transaction_type",
                "category",
                "first_seen",
                "last_seen",
                "times_seen",
            ]
        )

    backup_df = memory_df.copy()
    for column in ["first_seen", "last_seen"]:
        if column in backup_df.columns:
            backup_df[column] = pd.to_datetime(backup_df[column], errors="coerce").dt.strftime("%Y-%m-%d %H:%M:%S")
            backup_df[column] = backup_df[column].fillna("")

    return backup_df


def build_memory_backup_csv(memory_df: pd.DataFrame) -> bytes:
    return build_memory_backup_frame(memory_df).to_csv(index=False).encode("utf-8")


def build_memory_backup_excel(memory_df: pd.DataFrame) -> bytes:
    output = BytesIO()
    backup_df = build_memory_backup_frame(memory_df)

    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        backup_df.to_excel(writer, index=False, sheet_name="Memory")

    return output.getvalue()


def _pdf_table_from_df(df: pd.DataFrame, max_rows=20):
    if df.empty:
        return [["No data available"]]

    clipped = df.head(max_rows).copy()
    data = [clipped.columns.tolist()]
    for _, row in clipped.iterrows():
        data.append([str(x) for x in row.tolist()])
    return data


def build_pdf_report(context: dict) -> bytes:
    if not REPORTLAB_AVAILABLE:
        raise ValueError("PDF reporting requires reportlab to be installed.")

    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=A4,
        leftMargin=15 * mm,
        rightMargin=15 * mm,
        topMargin=15 * mm,
        bottomMargin=15 * mm,
    )

    styles = getSampleStyleSheet()
    title_style = ParagraphStyle(
        "TitleStyle",
        parent=styles["Heading1"],
        fontSize=18,
        alignment=TA_CENTER,
        textColor=colors.HexColor("#16324F"),
        spaceAfter=8,
    )
    subtitle_style = ParagraphStyle(
        "SubtitleStyle",
        parent=styles["Normal"],
        fontSize=9,
        alignment=TA_CENTER,
        textColor=colors.HexColor("#4A5568"),
        spaceAfter=10,
    )
    heading_style = ParagraphStyle(
        "HeadingStyle",
        parent=styles["Heading2"],
        fontSize=12,
        textColor=colors.HexColor("#16324F"),
        spaceBefore=8,
        spaceAfter=6,
        alignment=TA_LEFT,
    )
    normal_style = styles["Normal"]

    story = []

    story.append(Paragraph("Executive Financial Report", title_style))
    story.append(Paragraph(
        f"Generated: {pd.Timestamp.now().strftime('%Y-%m-%d %H:%M:%S')}<br/>"
        f"Period: {context['period_label']}<br/>"
        f"Months in report: {context['month_coverage']}",
        subtitle_style
    ))
    story.append(Spacer(1, 6))

    kpi_table_data = [
        ["KPI", "Value"],
        ["Total Income", format_currency(context["kpis"]["total_income"])],
        ["Total Expenses", format_currency(context["kpis"]["total_expenses"])],
        ["Net Result", format_currency(context["kpis"]["net_result"])],
        ["Burn Rate", format_currency(context["kpis"]["burn_rate"])],
        ["Savings Rate %", f"{context['kpis']['savings_rate']:.2f}%"],
        ["Average Monthly Income", format_currency(context["kpis"]["avg_monthly_income"])],
        ["Average Monthly Expenses", format_currency(context["kpis"]["avg_monthly_expenses"])],
        ["Months Covered", str(context["kpis"]["months_covered"])],
    ]
    kpi_table = Table(kpi_table_data, colWidths=[70 * mm, 80 * mm])
    kpi_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#16324F")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("BACKGROUND", (0, 1), (-1, -1), colors.whitesmoke),
        ("ALIGN", (1, 1), (-1, -1), "RIGHT"),
    ]))
    story.append(Paragraph("Executive KPI Summary", heading_style))
    story.append(kpi_table)
    story.append(Spacer(1, 10))

    story.append(Paragraph("Monthly Expense Difference", heading_style))
    monthly_diff_df = context["monthly_total"][["month", "amount", "diff", "change_%", "trend"]].copy()
    monthly_diff_df = monthly_diff_df.fillna("")
    monthly_diff_table = Table(_pdf_table_from_df(monthly_diff_df, max_rows=24))
    monthly_diff_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#2B6CB0")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.grey),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
    ]))
    story.append(monthly_diff_table)
    story.append(Spacer(1, 10))

    story.append(Paragraph("Category Summary", heading_style))
    category_pdf_df = context["summary"].copy()
    if not category_pdf_df.empty:
        category_pdf_df["sum"] = category_pdf_df["sum"].round(2)
        category_pdf_df["mean"] = category_pdf_df["mean"].round(2)
    category_table = Table(_pdf_table_from_df(category_pdf_df, max_rows=20))
    category_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#2F855A")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.grey),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
    ]))
    story.append(category_table)
    story.append(Spacer(1, 10))

    story.append(Paragraph("Recurring Expenses", heading_style))
    recurring_pdf_df = context["recurring_df"][[
        "sample_description",
        "category",
        "occurrences",
        "months_active",
        "avg_amount",
        "last_seen"
    ]].copy() if not context["recurring_df"].empty else pd.DataFrame()
    if not recurring_pdf_df.empty:
        recurring_pdf_df["avg_amount"] = recurring_pdf_df["avg_amount"].round(2)
    recurring_table = Table(_pdf_table_from_df(recurring_pdf_df, max_rows=20))
    recurring_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#805AD5")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.grey),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
    ]))
    story.append(recurring_table)
    story.append(Spacer(1, 10))

    story.append(Paragraph("Seasonal Expense Patterns", heading_style))
    seasonal_pdf_df = context["seasonal_df"][["month_name", "expense_total"]].copy() if not context["seasonal_df"].empty else pd.DataFrame()
    if not seasonal_pdf_df.empty:
        seasonal_pdf_df["expense_total"] = seasonal_pdf_df["expense_total"].round(2)
    seasonal_table = Table(_pdf_table_from_df(seasonal_pdf_df, max_rows=12))
    seasonal_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#D69E2E")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.grey),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
    ]))
    story.append(seasonal_table)
    story.append(Spacer(1, 10))

    story.append(Paragraph("Forecast", heading_style))
    if context["prediction"] is None:
        story.append(Paragraph("Not enough expense history for next-month prediction.", normal_style))
    else:
        story.append(Paragraph(
            f"Predicted next month expenses: <b>{format_currency(context['prediction'])}</b>",
            normal_style
        ))

    story.append(PageBreak())
    story.append(Paragraph("Transactions Detail (Top 40 rows)", heading_style))
    tx_columns = ["txn_date", "month", "original_description", "amount", "category"]
    if "subcategory" in context["report_df"].columns:
        tx_columns.append("subcategory")
    tx_columns.append("match_type")
    tx_pdf_df = context["report_df"][tx_columns].copy()
    tx_pdf_df["txn_date"] = tx_pdf_df["txn_date"].astype(str)
    tx_pdf_df["amount"] = tx_pdf_df["amount"].round(2)
    tx_table = Table(_pdf_table_from_df(tx_pdf_df, max_rows=40), repeatRows=1)
    tx_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1A202C")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("GRID", (0, 0), (-1, -1), 0.3, colors.grey),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 7),
    ]))
    story.append(tx_table)

    doc.build(story)
    return buffer.getvalue()
