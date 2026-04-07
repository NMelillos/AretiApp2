# =========================
# FILE: mailer.py
# =========================
import os
import smtplib
import ssl
from email.message import EmailMessage

import streamlit as st

from analytics import build_report_context
from db import log_report_delivery, report_already_sent
from reporting import REPORTLAB_AVAILABLE, build_pdf_report
from utils import format_currency, previous_closed_month_period


def build_email_html(context: dict):
    k = context["kpis"]
    return f"""
    <html>
    <body style="font-family: Arial, sans-serif; color: #1a202c;">
        <h2 style="color:#16324F;">Monthly Executive Financial Report</h2>
        <p><b>Period:</b> {context['period_label']}<br/>
        <b>Months covered:</b> {context['month_coverage']}</p>

        <h3 style="color:#16324F;">Key KPIs</h3>
        <table border="1" cellspacing="0" cellpadding="6" style="border-collapse: collapse;">
            <tr><th align="left">KPI</th><th align="right">Value</th></tr>
            <tr><td>Total Income</td><td align="right">{format_currency(k['total_income'])}</td></tr>
            <tr><td>Total Expenses</td><td align="right">{format_currency(k['total_expenses'])}</td></tr>
            <tr><td>Net Result</td><td align="right">{format_currency(k['net_result'])}</td></tr>
            <tr><td>Burn Rate</td><td align="right">{format_currency(k['burn_rate'])}</td></tr>
            <tr><td>Savings Rate</td><td align="right">{k['savings_rate']:.2f}%</td></tr>
            <tr><td>Average Monthly Income</td><td align="right">{format_currency(k['avg_monthly_income'])}</td></tr>
            <tr><td>Average Monthly Expenses</td><td align="right">{format_currency(k['avg_monthly_expenses'])}</td></tr>
        </table>

        <h3 style="color:#16324F;">Forecast</h3>
        <p>Predicted next month expenses:
        <b>{format_currency(context['prediction']) if context['prediction'] is not None else 'Not enough history'}</b></p>
    </body>
    </html>
    """


def send_email_report(
    smtp_host: str,
    smtp_port: int,
    smtp_username: str,
    smtp_password: str,
    sender_email: str,
    recipient_email: str,
    subject: str,
    html_body: str,
    attachment_bytes: bytes = None,
    attachment_filename: str = None,
    use_tls: bool = True,
):
    if not smtp_host or not smtp_port or not sender_email or not recipient_email:
        raise ValueError("SMTP host, port, sender email, and recipient email are required.")

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = sender_email
    msg["To"] = recipient_email
    msg.set_content("This email contains an HTML executive financial report.")
    msg.add_alternative(html_body, subtype="html")

    if attachment_bytes and attachment_filename:
        msg.add_attachment(
            attachment_bytes,
            maintype="application",
            subtype="pdf",
            filename=attachment_filename
        )

    if use_tls:
        server = smtplib.SMTP(smtp_host, smtp_port, timeout=30)
        server.ehlo()
        server.starttls(context=ssl.create_default_context())
        server.ehlo()
    else:
        server = smtplib.SMTP_SSL(smtp_host, smtp_port, context=ssl.create_default_context(), timeout=30)

    try:
        if smtp_username:
            server.login(smtp_username, smtp_password)
        server.send_message(msg)
    finally:
        server.quit()


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def get_secrets_config():
    env_config = {
        "smtp_host": os.getenv("SMTP_HOST", ""),
        "smtp_port": int(os.getenv("SMTP_PORT", "587")),
        "smtp_username": os.getenv("SMTP_USERNAME", ""),
        "smtp_password": os.getenv("SMTP_PASSWORD", ""),
        "email_from": os.getenv("EMAIL_FROM", ""),
        "email_to": os.getenv("EMAIL_TO", ""),
        "use_tls": _env_bool("SMTP_USE_TLS", True),
    }

    try:
        secrets = st.secrets
        secret_values = dict(secrets)
    except Exception:
        secret_values = {}

    config = {
        "smtp_host": str(secret_values.get("smtp_host", env_config["smtp_host"])),
        "smtp_port": int(secret_values.get("smtp_port", env_config["smtp_port"])),
        "smtp_username": str(secret_values.get("smtp_username", env_config["smtp_username"])),
        "smtp_password": str(secret_values.get("smtp_password", env_config["smtp_password"])),
        "email_from": str(secret_values.get("email_from", env_config["email_from"])),
        "email_to": str(secret_values.get("email_to", env_config["email_to"])),
        "use_tls": bool(secret_values.get("smtp_use_tls", env_config["use_tls"])),
    }

    required_keys = ["smtp_host", "smtp_port", "email_from", "email_to"]
    if any(not config[key] for key in required_keys):
        return None

    return config


def maybe_auto_send_monthly_email(saved_df):
    config = get_secrets_config()
    if config is None:
        return None

    prev_period = previous_closed_month_period()
    report_month = str(prev_period)

    if report_already_sent(report_month, "monthly_email_auto", config["email_to"]):
        return None

    if saved_df.empty:
        return None

    work = saved_df.copy()
    work["txn_date"] = work["txn_date"].astype("datetime64[ns]")
    work["month"] = work["txn_date"].dt.to_period("M").astype(str)
    monthly_df = work[work["month"] == report_month].copy()

    if monthly_df.empty:
        return None

    context = build_report_context(monthly_df, 1, "All")
    subject = f"Monthly Executive Financial Report - {report_month}"
    html_body = build_email_html(context)

    attachment = None
    attachment_name = None
    if REPORTLAB_AVAILABLE:
        attachment = build_pdf_report(context)
        attachment_name = f"monthly_financial_report_{report_month}.pdf"

    try:
        send_email_report(
            smtp_host=config["smtp_host"],
            smtp_port=config["smtp_port"],
            smtp_username=config["smtp_username"],
            smtp_password=config["smtp_password"],
            sender_email=config["email_from"],
            recipient_email=config["email_to"],
            subject=subject,
            html_body=html_body,
            attachment_bytes=attachment,
            attachment_filename=attachment_name,
            use_tls=config["use_tls"],
        )
        log_report_delivery(report_month, "monthly_email_auto", config["email_to"], "sent")
        return f"Auto monthly email report sent for {report_month} to {config['email_to']}."
    except Exception as e:
        return f"Auto monthly email report failed for {report_month}: {str(e)}"
