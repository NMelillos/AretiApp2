# =========================
# FILE: reporting.py
# =========================
from io import BytesIO

import pandas as pd

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


def build_excel_report(context: dict) -> bytes:
    exports = build_export_frames(context)
    output = BytesIO()

    with pd.ExcelWriter(output, engine="openpyxl") as writer:
        exports["transactions"].to_excel(writer, index=False, sheet_name="Transactions")

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
    tx_pdf_df = context["report_df"][[
        "txn_date", "month", "original_description", "amount", "category", "match_type"
    ]].copy()
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
