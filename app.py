# =========================
# FILE: app.py
# =========================
import pandas as pd
import streamlit as st

from analytics import (
    build_report_context,
    detect_anomalies,
    detect_duplicates,
    detect_saved_duplicates,
)
from classification import classify_transactions
from db import (
    DB_PATH,
    add_category,
    full_reset_database,
    get_categories,
    get_memory,
    get_saved_transactions,
    init_db,
    remember_transaction,
    reset_runtime_data,
    save_classified_transactions,
)
from mailer import (
    build_email_html,
    get_secrets_config,
    maybe_auto_send_monthly_email,
    send_email_report,
)
from parsing import parse_csv, parse_excel, parse_pdf
from reporting import (
    REPORTLAB_AVAILABLE,
    build_csv_report,
    build_excel_report,
    build_pdf_report,
)
from utils import (
    format_currency,
    normalize_description,
    simplify_merchant,
    extract_beneficiary,
    infer_transaction_type,
)

st.set_page_config(page_title="CFO Financial Dashboard", layout="wide")
init_db()

st.title("CFO Financial Dashboard")
st.caption(
    "Controlled logic, memory, executive reporting, KPI dashboard, exports, "
    "PDF, email reporting, duplicates, and reset tools"
)

tab1, tab2, tab3, tab4 = st.tabs([
    "1. Upload & Classify",
    "2. Memory",
    "3. Reports & CFO Dashboard",
    "4. Categories & System"
])

# -------------------------
# TAB 1
# -------------------------
with tab1:
    st.subheader("Upload CSV, Excel, or PDF statement")

    st.markdown("### Currency Settings")
    usd_rate = st.number_input(
        "EUR → USD rate",
        min_value=0.5,
        max_value=2.0,
        value=1.08,
        step=0.01,
    )

    st.markdown("### Manual Transaction Entry")

    with st.expander("Add transaction manually", expanded=False):
        m_date = st.date_input("Date")
        m_desc = st.text_input("Description")
        m_amount = st.number_input("Amount", format="%.2f")
        m_category = st.selectbox("Category", get_categories(), key="manual_category")

        if st.button("Add Manual Transaction"):
            norm_desc = simplify_merchant(normalize_description(m_desc))
            beneficiary = extract_beneficiary(m_desc)
            txn_type = infer_transaction_type(m_desc, m_amount)

            if not norm_desc:
                norm_desc = str(m_desc).upper().strip()

            manual_row = {
                "Date": str(m_date),
                "Description": m_desc,
                "normalized_description": norm_desc,
                "Amount": m_amount,
                "currency": "EUR",
                "usd_amount": m_amount * usd_rate,
                "beneficiary": beneficiary,
                "transaction_type": txn_type,
                "final_category": m_category,
                "match_type": "manual",
                "confidence": 1.0,
                "reviewed": 1,
                "account_name": "",
                "account_number": "",
            }

            df_manual = pd.DataFrame([manual_row])
            save_classified_transactions(df_manual)
            remember_transaction(
                norm_desc,
                beneficiary,
                txn_type,
                m_category,
            )
            st.success("Manual transaction saved successfully.")

    st.markdown("### Account Information")
    account_name = st.text_input("Account name (e.g. Revolut, Bank of Cyprus)")
    account_number = st.text_input("Account number / IBAN")

    uploaded_file = st.file_uploader(
        "Choose a file", type=["csv", "xlsx", "xls", "pdf"]
    )

    if uploaded_file is not None:
        try:
            lower_name = uploaded_file.name.lower()

            if lower_name.endswith(".pdf"):
                df = parse_pdf(uploaded_file)
            elif lower_name.endswith(".xlsx") or lower_name.endswith(".xls"):
                df = parse_excel(uploaded_file)
            else:
                df = parse_csv(uploaded_file)

            # account info for all file types
            df["account_name"] = account_name
            df["account_number"] = account_number

            # normalization
            df["normalized_description"] = df["Description"].apply(normalize_description)
            df["normalized_description"] = df["normalized_description"].apply(simplify_merchant)
            df["normalized_description"] = df.apply(
                lambda r: r["normalized_description"]
                if str(r["normalized_description"]).strip()
                else str(r["Description"]).upper().strip(),
                axis=1,
            )

            # beneficiary / transaction type
            df["beneficiary"] = df["Description"].apply(extract_beneficiary)
            df["transaction_type"] = df.apply(
                lambda r: infer_transaction_type(r["Description"], r["Amount"]),
                axis=1,
            )

            # duplicates
            df = detect_duplicates(df)

            # classify
            memory_df = get_memory()
            classified_df = classify_transactions(df, memory_df)
            if "reviewed" not in classified_df.columns:
                classified_df["reviewed"] = 0

            if "dup_flag" in df.columns:
                classified_df["dup_flag"] = df["dup_flag"].values

            classified_df["currency"] = "EUR"
            classified_df["usd_amount"] = classified_df["Amount"] * usd_rate
            classified_df["account_name"] = account_name
            classified_df["account_number"] = account_number

            st.success(f"Loaded {len(classified_df)} transactions")

            c1, c2, c3, c4, c5 = st.columns(5)
            c1.metric("Exact matches", int((classified_df["match_type"] == "exact").sum()))
            c2.metric("Similar matches", int((classified_df["match_type"] == "similar").sum()))
            c3.metric("Rule-based", int((classified_df["match_type"] == "rule").sum()))
            c4.metric("AI fallback", int((classified_df["match_type"] == "ai").sum()))
            c5.metric(
                "Potential duplicates",
                int(classified_df["dup_flag"].sum()) if "dup_flag" in classified_df.columns else 0,
            )

            st.markdown("### Review transactions")

            categories = get_categories()
            if "reviewed" not in classified_df.columns:
                classified_df["reviewed"] = 0

            classified_df = classified_df[classified_df["reviewed"] == 0]

            reviewed_rows = []

            for i, row in classified_df.iterrows():
                with st.container(border=True):
                    col1, col2, col3, col4 = st.columns([2, 4, 2, 3])

                    with col1:
                        st.write(f"**Date:** {row['Date']}")
                    with col2:
                        st.write(f"**Description:** {row['Description']}")
                        st.caption(f"Normalized: {row['normalized_description']}")
                    with col3:
                        st.write(f"**Amount:** {row['Amount']:.2f}")
                        st.caption(f"USD: {row['usd_amount']:.2f}")
                    with col4:
                        st.write(f"**Match type:** {row['match_type']}")
                        st.caption(f"Currency: {row['currency']}")

                    if row.get("dup_flag", False):
                        st.error("⚠️ Potential duplicate transaction detected")

                    if row["match_type"] == "exact":
                        st.info(
                            f"Identical to past transaction: `{row['matched_reference']}` "
                            f"→ suggested category: **{row['suggested_category']}**"
                        )
                    elif row["match_type"] == "similar":
                        st.warning(
                            f"Similar to past transaction: `{row['matched_reference']}` "
                            f"→ suggested category: **{row['suggested_category']}** "
                            f"(confidence {row['confidence']})"
                        )
                    elif row["match_type"] == "rule":
                        st.info(
                            "Rule-based classification "
                            f"→ suggested category: **{row['suggested_category']}**"
                        )
                    elif row["match_type"] == "ai":
                        st.info(
                            "AI fallback suggestion "
                            f"→ suggested category: **{row['suggested_category']}** "
                            f"(confidence {row['confidence']})"
                        )
                    else:
                        st.error("New or unusual transaction. Review required.")

                    selected_category = st.selectbox(
                        f"Category for row {i + 1}",
                        categories,
                        index=categories.index(row["suggested_category"])
                        if row["suggested_category"] in categories else 0,
                        key=f"cat_{i}",
                    )

                    reviewed = st.checkbox(
                        f"Mark row {i + 1} as reviewed",
                        value=False,
                        key=f"reviewed_{i}",
                    )

                    final_row = row.to_dict()
                    final_row["final_category"] = selected_category
                    final_row["reviewed"] = 1 if reviewed else 0
                    reviewed_rows.append(final_row)

            if st.button("Save reviewed classifications", type="primary"):
                final_df = pd.DataFrame(reviewed_rows)
                reviewed_only = final_df[final_df["reviewed"] == 1].copy()

                reviewed_only["normalized_description"] = reviewed_only["Description"].apply(
                    lambda x: simplify_merchant(normalize_description(x))
                )

                reviewed_only["normalized_description"] = reviewed_only.apply(
                    lambda r: r["normalized_description"]
                    if str(r["normalized_description"]).strip()
                    else str(r["Description"]).upper().strip(),
                    axis=1,
                )

                reviewed_only = reviewed_only.drop_duplicates(
                    subset=["Date", "Description", "Amount"]
                )

                for idx, row in reviewed_only.iterrows():
                    clean_desc = row.get("normalized_description", "")

                    if not str(clean_desc).strip():
                        clean_desc = simplify_merchant(normalize_description(row["Description"]))

                    if not str(clean_desc).strip():
                        clean_desc = str(row["Description"]).upper().strip()

                    remember_transaction(
                        clean_desc,
                        row["beneficiary"],
                        row["transaction_type"],
                        row["final_category"],
                    )

                    reviewed_only.at[idx, "normalized_description"] = clean_desc

                save_classified_transactions(reviewed_only)

                st.success(
                    f"Saved {len(reviewed_only)} reviewed transactions and updated memory."
                )

            if reviewed_rows:
                st.markdown("### Preview")
                preview_df = pd.DataFrame(reviewed_rows)[[
                    "Date",
                    "Description",
                    "normalized_description",
                    "Amount",
                    "currency",
                    "usd_amount",
                    "match_type",
                    "suggested_category",
                    "final_category",
                    "reviewed",
                    "dup_flag",
                ]]
                st.dataframe(preview_df, use_container_width=True)

        except Exception as e:
            st.error(str(e))


# -------------------------
# TAB 2
# -------------------------
with tab2:
    st.subheader("Transaction memory")

    memory_df = get_memory()

    if memory_df.empty:
        st.info("No learned transactions yet.")
    else:
        st.dataframe(memory_df, use_container_width=True)

        search_term = st.text_input("Search memory")
        if search_term:
            filtered = memory_df[
                memory_df["normalized_description"].str.contains(
                    search_term.upper(),
                    na=False,
                )
            ]
            st.dataframe(filtered, use_container_width=True)


# -------------------------
# TAB 3
# -------------------------
with tab3:
    st.subheader("Reports & CFO Dashboard")

    saved_df = get_saved_transactions()

    auto_status = None
    if not saved_df.empty:
        saved_df["txn_date"] = pd.to_datetime(saved_df["txn_date"], errors="coerce")
        auto_status = maybe_auto_send_monthly_email(saved_df.copy())

    if auto_status:
        st.info(auto_status)

    if saved_df.empty:
        st.info("No classified transactions saved yet.")
    else:
        saved_df["txn_date"] = pd.to_datetime(saved_df["txn_date"], errors="coerce")
        saved_df["amount"] = pd.to_numeric(saved_df["amount"], errors="coerce").fillna(0)

        if "currency" not in saved_df.columns:
            saved_df["currency"] = "EUR"
        if "usd_amount" not in saved_df.columns:
            saved_df["usd_amount"] = saved_df["amount"] * 1.08

        saved_df = detect_saved_duplicates(saved_df)

        r1, r2 = st.columns(2)

        with r1:
            months = st.selectbox("Period", [1, 3, 6, 12], index=2)

        with r2:
            report_categories = ["All"] + get_categories()
            selected_category = st.selectbox("Category", report_categories)

        cutoff = pd.Timestamp.now() - pd.DateOffset(months=months)
        report_df = saved_df[saved_df["txn_date"] >= cutoff].copy()

        report_df = report_df.drop_duplicates(
            subset=["txn_date", "original_description", "amount"]
        )
        report_df = report_df.sort_values("txn_date", ascending=False)

        if selected_category != "All":
            report_df = report_df[report_df["category"] == selected_category]

        report_df = detect_anomalies(report_df)
        report_df["month"] = report_df["txn_date"].dt.to_period("M").astype(str)

        context = build_report_context(report_df, months, selected_category)
        kpis = context["kpis"]
        monthly_total = context["monthly_total"]
        monthly_income_expense = context["monthly_income_expense"]
        recurring_df = context["recurring_df"]
        seasonal_df = context["seasonal_df"]
        prediction = context["prediction"]
        prediction_source = context["prediction_source"]
        summary = context["summary"]
        category_expenses = context["category_expenses"]

        st.markdown(f"### Transactions for the last {months} month(s)")

        display_cols = [
            "txn_date",
            "month",
            "original_description",
            "normalized_description",
            "amount",
            "category",
            "match_type",
            "reviewed",
            "anomaly",
            "dup_flag",
        ]
        if "currency" in report_df.columns:
            display_cols.append("currency")
        if "usd_amount" in report_df.columns:
            display_cols.append("usd_amount")

        display_df = report_df[display_cols].sort_values("txn_date", ascending=False)

        def highlight_flags(row):
            styles = [""] * len(row)
            if row["dup_flag"]:
                styles = ["background-color: #fff4cc"] * len(row)
            if row["anomaly"]:
                styles = ["background-color: #ffcccc"] * len(row)
            return styles

        st.dataframe(
            display_df.style.apply(highlight_flags, axis=1),
            use_container_width=True,
        )

        duplicates_only = report_df[report_df["dup_flag"]].copy()
        if not duplicates_only.empty:
            st.markdown("### Potential duplicate transactions")
            dup_cols = [
                "txn_date",
                "month",
                "original_description",
                "normalized_description",
                "amount",
                "category",
                "match_type",
            ]
            if "currency" in duplicates_only.columns:
                dup_cols.append("currency")
            if "usd_amount" in duplicates_only.columns:
                dup_cols.append("usd_amount")

            st.dataframe(
                duplicates_only[dup_cols].sort_values("txn_date", ascending=False),
                use_container_width=True,
            )

        anomalies_only = report_df[report_df["anomaly"]].copy()
        if not anomalies_only.empty:
            st.markdown("### Suspicious expenses detected")
            an_cols = [
                "txn_date",
                "month",
                "original_description",
                "normalized_description",
                "amount",
                "category",
                "match_type",
            ]
            if "currency" in anomalies_only.columns:
                an_cols.append("currency")
            if "usd_amount" in anomalies_only.columns:
                an_cols.append("usd_amount")

            st.dataframe(
                anomalies_only[an_cols].sort_values("txn_date", ascending=False),
                use_container_width=True,
            )

        st.markdown("### Executive KPI Dashboard")
        d1, d2, d3, d4, d5 = st.columns(5)
        d1.metric("Total Income", format_currency(kpis["total_income"]))
        d2.metric("Total Expenses", format_currency(kpis["total_expenses"]))
        d3.metric("Net Result", format_currency(kpis["net_result"]))
        d4.metric("Burn Rate", format_currency(kpis["burn_rate"]))
        d5.metric("Savings Rate", f"{kpis['savings_rate']:.2f}%")

        d6, d7, d8 = st.columns(3)
        d6.metric("Avg Monthly Income", format_currency(kpis["avg_monthly_income"]))
        d7.metric("Avg Monthly Expenses", format_currency(kpis["avg_monthly_expenses"]))
        d8.metric("Months Covered", str(kpis["months_covered"]))

        st.markdown("### Summary by category")
        st.dataframe(summary, use_container_width=True)

        if st.button("Show 'Subscriptions' paid in the last 6 months"):
            subs_cols = [
                "txn_date",
                "original_description",
                "normalized_description",
                "amount",
                "category",
            ]
            if "currency" in saved_df.columns:
                subs_cols.append("currency")
            if "usd_amount" in saved_df.columns:
                subs_cols.append("usd_amount")

            subs_df = saved_df[
                (saved_df["txn_date"] >= pd.Timestamp.now() - pd.DateOffset(months=6))
                & (saved_df["category"] == "Subscriptions")
            ][subs_cols].sort_values("txn_date", ascending=False)

            st.dataframe(subs_df, use_container_width=True)

        st.markdown("### Monthly totals")
        st.dataframe(context["monthly_summary"], use_container_width=True)

        st.markdown("### Monthly Expense Difference")

        if not monthly_total.empty:
            def highlight_diff(val):
                if pd.isna(val):
                    return ""
                if val > 0:
                    return "color: red; font-weight: bold; background-color: #ffe6e6;"
                if val < 0:
                    return "color: green; font-weight: bold; background-color: #e6ffe6;"
                return "color: gray;"

            st.dataframe(
                monthly_total[["month", "amount", "diff", "change_%", "trend"]]
                .style.applymap(highlight_diff, subset=["diff"]),
                use_container_width=True,
            )
        else:
            st.info("No monthly expense difference available.")

        st.markdown("### Dashboard with charts")

        if not monthly_income_expense.empty:
            st.markdown("#### Income vs Expenses")
            st.line_chart(monthly_income_expense.set_index("month")[["income", "expense", "net"]])

        if not category_expenses.empty:
            st.markdown("#### Expenses by category")
            st.bar_chart(category_expenses.set_index("category"))

        if not monthly_total.empty:
            st.markdown("#### Monthly expense trend")
            st.bar_chart(monthly_total.set_index("month")[["amount"]])

        st.markdown("### Recurring expenses detection")
        if recurring_df.empty:
            st.info("No recurring expenses detected yet.")
        else:
            recurring_display = recurring_df[[
                "sample_description",
                "category",
                "occurrences",
                "months_active",
                "avg_amount",
                "min_amount",
                "max_amount",
                "variation_pct",
                "last_seen",
            ]].copy()
            recurring_display["period"] = context["period_label"]
            recurring_display["month"] = "ALL"
            st.dataframe(recurring_display, use_container_width=True)

        st.markdown("### Seasonal expense patterns")
        if seasonal_df.empty:
            st.info("Not enough data for seasonality detection.")
        else:
            seasonal_display = seasonal_df.copy()
            seasonal_display["period"] = context["period_label"]
            seasonal_display["month"] = seasonal_display["month_name"]
            st.dataframe(seasonal_display, use_container_width=True)
            st.bar_chart(seasonal_df.set_index("month_name")[["expense_total"]])

        st.markdown("### Prediction for next month expenses")
        if prediction is None:
            st.info("Not enough expense history for prediction.")
        else:
            p1, p2 = st.columns(2)
            p1.metric("Predicted next month expenses", format_currency(prediction))

            if not prediction_source.empty:
                last_month_value = float(prediction_source["expense_total"].iloc[-1])
                p2.metric("Difference vs last month", format_currency(prediction - last_month_value))

                prediction_chart = prediction_source[["month", "expense_total"]].copy()
                next_period = pd.Period(prediction_chart["month"].iloc[-1], freq="M") + 1
                prediction_chart = pd.concat([
                    prediction_chart,
                    pd.DataFrame({
                        "month": [str(next_period)],
                        "expense_total": [prediction],
                    }),
                ], ignore_index=True)

                st.line_chart(prediction_chart.set_index("month"))

        st.markdown("### Export Reports")
        export_col1, export_col2, export_col3 = st.columns(3)

        with export_col1:
            csv_bytes = build_csv_report(context)
            st.download_button(
                label="Download CSV Report",
                data=csv_bytes,
                file_name="cfo_transactions_report.csv",
                mime="text/csv",
            )

        with export_col2:
            excel_bytes = build_excel_report(context)
            st.download_button(
                label="Download Excel Report",
                data=excel_bytes,
                file_name="cfo_financial_report.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )

        with export_col3:
            if REPORTLAB_AVAILABLE:
                pdf_bytes = build_pdf_report(context)
                st.download_button(
                    label="Download PDF Report",
                    data=pdf_bytes,
                    file_name="cfo_executive_financial_report.pdf",
                    mime="application/pdf",
                )
            else:
                st.info("Install reportlab for PDF report generation.")

        st.markdown("### Monthly Email Reports")

        secrets_cfg = get_secrets_config()
        default_to = secrets_cfg["email_to"] if secrets_cfg else ""
        default_from = secrets_cfg["email_from"] if secrets_cfg else ""
        default_host = secrets_cfg["smtp_host"] if secrets_cfg else ""
        default_port = secrets_cfg["smtp_port"] if secrets_cfg else 587
        default_user = secrets_cfg["smtp_username"] if secrets_cfg else ""
        default_use_tls = secrets_cfg["use_tls"] if secrets_cfg else True

        with st.expander("Email Configuration", expanded=False):
            smtp_host = st.text_input("SMTP Host", value=default_host)
            smtp_port = st.number_input("SMTP Port", min_value=1, max_value=65535, value=int(default_port))
            smtp_username = st.text_input("SMTP Username", value=default_user)
            smtp_password = st.text_input("SMTP Password", type="password")
            sender_email = st.text_input("Sender Email", value=default_from)
            recipient_email = st.text_input("Recipient Email", value=default_to)
            use_tls = st.checkbox("Use TLS", value=default_use_tls)
            attach_pdf = st.checkbox("Attach PDF Report", value=True)

        email_subject = f"Executive Financial Report - {context['period_label']}"
        email_html = build_email_html(context)

        if st.button("Send Email Report Now"):
            try:
                attachment_bytes = None
                attachment_name = None
                if attach_pdf:
                    if REPORTLAB_AVAILABLE:
                        attachment_bytes = build_pdf_report(context)
                        attachment_name = "executive_financial_report.pdf"
                    else:
                        st.warning("PDF attachment skipped because reportlab is not installed.")

                send_email_report(
                    smtp_host=smtp_host,
                    smtp_port=int(smtp_port),
                    smtp_username=smtp_username,
                    smtp_password=smtp_password,
                    sender_email=sender_email,
                    recipient_email=recipient_email,
                    subject=email_subject,
                    html_body=email_html,
                    attachment_bytes=attachment_bytes,
                    attachment_filename=attachment_name,
                    use_tls=use_tls,
                )
                st.success("Email report sent successfully.")
            except Exception as e:
                st.error(str(e))


# -------------------------
# TAB 4
# -------------------------

with tab4:
    st.info(f"Database location: {DB_PATH}")

    st.markdown("## 👁️ Audit & Database Transparency")

    view_option = st.selectbox(
        "Select data to view",
        ["Transactions", "Memory", "Categories"]
    )

    audit_df = None

    if view_option == "Transactions":
        audit_df = get_saved_transactions()
    elif view_option == "Memory":
        audit_df = get_memory()
    elif view_option == "Categories":
        audit_df = pd.DataFrame({"category": get_categories()})

    if audit_df is not None:
        st.dataframe(audit_df, use_container_width=True)

    if audit_df is not None and not audit_df.empty:
        csv = audit_df.to_csv(index=False).encode("utf-8")
        st.download_button(
            "Download as CSV",
            csv,
            file_name=f"{view_option}.csv",
            mime="text/csv",
        )

    st.subheader("Manage categories")

    current_categories = get_categories()
    st.write(", ".join(current_categories))

    new_category = st.text_input("Add new category")

    if st.button("Add category"):
        add_category(new_category)
        st.success(f"Category added: {new_category}")
        st.rerun()

    st.markdown("### Upload Categories from Excel")

    cat_file = st.file_uploader("Upload categories file", type=["xlsx"])

    if cat_file is not None:
        try:
            from db import replace_categories

            df_cat = pd.read_excel(cat_file)
            st.write(df_cat.head())

            if "category" not in df_cat.columns:
                st.error("Excel must have a column named 'category'")
            else:
                replace_categories(df_cat["category"].dropna().tolist())
                st.success("Categories updated from Excel.")
                st.rerun()

        except Exception as e:
            st.error(str(e))

    st.markdown("### System Reset")

    c1, c2 = st.columns(2)

    with c1:
        if st.button("Reset transactions, memory, and report logs"):
            reset_runtime_data()
            st.success("Runtime data cleared successfully.")
            st.rerun()

    with c2:
        if st.button("Full reset (including categories)"):
            full_reset_database()
            st.success("Full database reset completed.")
            st.rerun()
