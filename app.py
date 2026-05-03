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
    get_account_registry,
    get_category_records,
    get_categories,
    get_memory,
    get_monthly_rates,
    get_saved_transactions,
    init_db,
    remember_transaction,
    replace_account_registry_records,
    replace_category_records,
    replace_memory_records,
    replace_monthly_rate_records,
    replace_saved_transaction_records,
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
    build_access_backup_csv,
    build_access_backup_excel,
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

CURRENCY_NAME_TO_CODE = {
    "DOLLAR": "USD",
    "USD": "USD",
    "US DOLLAR": "USD",
    "EURO": "EUR",
    "EUR": "EUR",
    "POUND": "GBP",
    "GBP": "GBP",
    "SHEKEL": "ILS",
    "ILS": "ILS",
    "KZT": "KZT",
    "RUB": "RUB",
}


def normalize_currency_code(value):
    text = str(value).strip().upper().replace("_", " ")
    text = text.replace("  ", " ")
    return CURRENCY_NAME_TO_CODE.get(text, text if len(text) == 3 else "")


def parse_rate_month(value):
    if pd.isna(value):
        return None

    text = str(value).strip()
    if not text:
        return None

    parsed = pd.to_datetime(text, errors="coerce")
    if pd.isna(parsed):
        for fmt in ["%b-%y", "%b-%Y", "%B-%y", "%B-%Y", "%m-%Y", "%m/%Y"]:
            try:
                parsed = pd.to_datetime(text, format=fmt)
                break
            except Exception:
                parsed = pd.NaT

    if pd.isna(parsed):
        return None

    return parsed.to_period("M").strftime("%Y-%m")


def extract_currency_from_rate_label(label):
    text = str(label).strip().replace("–", "-").replace("—", "-")
    for separator in ["/", "-"]:
        if separator not in text:
            continue

        left_part, right_part = text.split(separator, 1)
        left_currency = normalize_currency_code(left_part)
        right_currency = normalize_currency_code(right_part)

        if left_currency == "USD" and right_currency:
            return right_currency
        if right_currency == "USD" and left_currency:
            return left_currency
        if left_currency == right_currency and left_currency:
            return left_currency
        if left_currency:
            return left_currency
        if right_currency:
            return right_currency

    return ""


def convert_uploaded_rate_to_usd(label, raw_rate):
    text = str(label).strip().replace("–", "-").replace("—", "-")
    rate_value = float(raw_rate)

    for separator in ["/", "-"]:
        if separator not in text:
            continue

        left_part, right_part = text.split(separator, 1)
        left_currency = normalize_currency_code(left_part)
        right_currency = normalize_currency_code(right_part)

        if left_currency == right_currency == "USD":
            return 1.0
        if right_currency == "USD":
            return rate_value
        if left_currency == "USD" and rate_value > 0:
            return round(1 / rate_value, 8)

    return rate_value


def parse_latest_monthly_rates_excel(uploaded_file):
    uploaded_file.seek(0)
    raw_df = pd.read_excel(uploaded_file, header=None)
    report_month = pd.Timestamp.today().to_period("M").strftime("%Y-%m")
    records = []

    for row_index in range(len(raw_df)):
        label = raw_df.iat[row_index, 0]
        source_currency = extract_currency_from_rate_label(label)
        if not source_currency:
            continue

        rate_value = None
        for column_index in range(1, raw_df.shape[1]):
            raw_rate = raw_df.iat[row_index, column_index]
            numeric_rate = pd.to_numeric(pd.Series([raw_rate]), errors="coerce").iloc[0]
            if pd.notna(numeric_rate) and float(numeric_rate) > 0:
                rate_value = float(numeric_rate)
                break

        if rate_value is None:
            continue

        records.append({
            "report_month": report_month,
            "source_currency": source_currency,
            "rate_to_usd": 1.0 if source_currency == "USD" else convert_uploaded_rate_to_usd(label, rate_value),
        })

    if not records:
        raise ValueError("Could not extract any FX rates from the uploaded Excel file.")

    rates_df = pd.DataFrame(records)
    rates_df = rates_df.drop_duplicates(
        subset=["report_month", "source_currency"],
        keep="last",
    ).sort_values(["report_month", "source_currency"])
    return rates_df


def parse_monthly_rates_excel(uploaded_file):
    uploaded_file.seek(0)
    raw_df = pd.read_excel(uploaded_file, header=None)

    header_row_index = None
    month_columns = {}

    for row_index, row in raw_df.iterrows():
        parsed_months = {}
        for column_index, value in row.items():
            report_month = parse_rate_month(value)
            if report_month:
                parsed_months[column_index] = report_month

        if len(parsed_months) > len(month_columns):
            header_row_index = row_index
            month_columns = parsed_months

    if not month_columns:
        return parse_latest_monthly_rates_excel(uploaded_file)

    records = []

    for row_index in range((header_row_index or 0) + 1, len(raw_df)):
        label = raw_df.iat[row_index, 0]
        source_currency = extract_currency_from_rate_label(label)
        if not source_currency:
            continue

        for column_index, report_month in month_columns.items():
            raw_rate = raw_df.iat[row_index, column_index]
            rate_value = pd.to_numeric(pd.Series([raw_rate]), errors="coerce").iloc[0]

            if pd.isna(rate_value) or float(rate_value) <= 0:
                continue

            if source_currency == "USD":
                rate_to_usd = 1.0
            else:
                rate_to_usd = convert_uploaded_rate_to_usd(label, rate_value)

            records.append({
                "report_month": report_month,
                "source_currency": source_currency,
                "rate_to_usd": rate_to_usd,
            })

    if not records:
        raise ValueError("Could not extract any monthly FX rates from the uploaded Excel file.")

    rates_df = pd.DataFrame(records)
    rates_df = rates_df.drop_duplicates(
        subset=["report_month", "source_currency"],
        keep="last",
    ).sort_values(["report_month", "source_currency"])
    return rates_df


def get_statement_currencies(rates_df):
    currencies = ["USD"]
    if rates_df is not None and not rates_df.empty:
        currencies.extend(sorted(rates_df["source_currency"].dropna().unique().tolist()))
    return list(dict.fromkeys(currencies))


def parse_account_registry_excel(uploaded_file):
    uploaded_file.seek(0)
    df = pd.read_excel(uploaded_file)
    normalized_columns = {str(column).strip().lower(): column for column in df.columns}

    required_mapping = {
        "account name": "account_name",
        "bank": "bank",
        "account number": "account_number",
        "currency": "currency",
        "rate type": "rate_type",
    }

    missing_columns = [source for source in required_mapping if source not in normalized_columns]
    if missing_columns:
        raise ValueError(
            "Account registry Excel must include columns: Account Name, Bank, Account number, Currency, Rate Type."
        )

    records_df = pd.DataFrame({
        target: df[normalized_columns[source]]
        for source, target in required_mapping.items()
    })

    records_df["account_name"] = records_df["account_name"].astype(str).str.strip()
    records_df["bank"] = records_df["bank"].fillna("").astype(str).str.strip()
    records_df["account_number"] = records_df["account_number"].astype(str).str.strip()
    records_df["currency"] = records_df["currency"].apply(normalize_currency_code)
    records_df["rate_type"] = records_df["rate_type"].fillna("").astype(str).str.strip()
    records_df = records_df[
        (records_df["account_name"] != "")
        & (records_df["account_number"] != "")
        & (records_df["currency"] != "")
        & (records_df["rate_type"] != "")
    ]
    records_df = records_df.drop_duplicates(
        subset=["account_name", "account_number"],
        keep="last",
    ).sort_values(["account_name", "bank", "account_number"])

    if records_df.empty:
        raise ValueError("No valid account registry rows found in the uploaded Excel file.")

    return records_df


def parse_category_excel(uploaded_file):
    uploaded_file.seek(0)
    df = pd.read_excel(uploaded_file)
    normalized_columns = {str(column).strip().lower(): column for column in df.columns}

    if "category" not in normalized_columns:
        raise ValueError("Excel must have a column named 'category'.")

    subcategory_column = normalized_columns.get("subcategory")
    records_df = pd.DataFrame({
        "category": df[normalized_columns["category"]],
        "subcategory": df[subcategory_column] if subcategory_column else "",
    })

    records_df["category"] = records_df["category"].fillna("").astype(str).str.strip()
    records_df["subcategory"] = records_df["subcategory"].fillna("").astype(str).str.strip()
    records_df = records_df[records_df["category"] != ""]
    records_df = records_df.drop_duplicates(subset=["category", "subcategory"], keep="last")
    records_df = records_df.sort_values(["category", "subcategory"]).reset_index(drop=True)

    if records_df.empty:
        raise ValueError("No valid category rows found in the uploaded Excel file.")

    return records_df


def build_account_option_label(row):
    bank = str(row.get("bank", "")).strip()
    account_number = str(row.get("account_number", "")).strip()
    currency = str(row.get("currency", "")).strip()
    rate_type = str(row.get("rate_type", "")).strip()
    parts = [str(row.get("account_name", "")).strip()]
    if bank:
        parts.append(bank)
    if account_number:
        parts.append(account_number)
    if currency:
        parts.append(currency)
    if rate_type:
        parts.append(rate_type)
    return " | ".join(parts)


def get_uploaded_file_token(uploaded_file):
    if uploaded_file is None:
        return None
    return (getattr(uploaded_file, "name", ""), getattr(uploaded_file, "size", 0))


def build_rates_lookup(rates_df):
    if rates_df is None or rates_df.empty:
        return {}

    lookup = {}

    for _, row in rates_df.iterrows():
        currency = str(row["source_currency"])
        month = parse_rate_month(row["report_month"])
        if not month:
            continue
        lookup.setdefault(currency, {})[month] = float(row["rate_to_usd"])

    return lookup


def get_current_rate(rates_lookup, statement_currency):
    currency_rates = rates_lookup.get(statement_currency, {})
    if not currency_rates:
        return None

    latest_month = max(currency_rates)
    return currency_rates[latest_month]


def attach_statement_currency(df, statement_currency, rates_df):
    work = df.copy()
    work["Amount"] = pd.to_numeric(work["Amount"], errors="coerce").fillna(0.0)
    work["currency"] = statement_currency

    if statement_currency == "USD":
        work["usd_amount"] = work["Amount"]
        return work

    rates_lookup = build_rates_lookup(rates_df)
    current_rate = get_current_rate(rates_lookup, statement_currency)
    if current_rate is None:
        raise ValueError(
            f"Missing current USD rate for {statement_currency}. Upload a newer monthly rates Excel file or use the latest saved rates upload."
        )

    work["usd_amount"] = work["Amount"] * float(current_rate)
    return work


def add_source_occurrence(df, date_col, description_col, amount_col, account_number_col="account_number"):
    work = df.copy()
    occurrence_key = pd.DataFrame({
        "txn_date": work[date_col].astype(str).fillna(""),
        "original_description": work[description_col].astype(str).fillna(""),
        "amount": pd.to_numeric(work[amount_col], errors="coerce").fillna(0.0).round(2),
        "account_number": work.get(account_number_col, "").astype(str).fillna(""),
    })
    work["source_occurrence"] = occurrence_key.groupby(
        ["txn_date", "original_description", "amount", "account_number"],
        dropna=False,
    ).cumcount()
    return work


def merge_saved_review_state(classified_df, saved_df):
    work = add_source_occurrence(classified_df, "Date", "Description", "Amount")
    if saved_df is None or saved_df.empty:
        if "category" not in work.columns:
            work["category"] = ""
        return work

    saved_work = saved_df.copy()
    if "source_occurrence" not in saved_work.columns:
        saved_work = add_source_occurrence(saved_work, "txn_date", "original_description", "amount")
    else:
        saved_work["source_occurrence"] = pd.to_numeric(
            saved_work["source_occurrence"], errors="coerce"
        ).fillna(0).astype(int)

    saved_work["txn_date"] = saved_work["txn_date"].astype(str).fillna("")
    saved_work["original_description"] = saved_work["original_description"].astype(str).fillna("")
    saved_work["amount"] = pd.to_numeric(saved_work["amount"], errors="coerce").fillna(0.0).round(2)
    saved_work["account_number"] = saved_work.get("account_number", "").astype(str).fillna("")

    saved_state = saved_work[[
        "txn_date",
        "original_description",
        "amount",
        "account_number",
        "source_occurrence",
        "reviewed",
        "category",
    ]].copy()
    saved_state = saved_state.sort_values(["reviewed"]).drop_duplicates(
        subset=["txn_date", "original_description", "amount", "account_number", "source_occurrence"],
        keep="last",
    )

    merged = work.merge(
        saved_state,
        left_on=["Date", "Description", "Amount", "account_number", "source_occurrence"],
        right_on=["txn_date", "original_description", "amount", "account_number", "source_occurrence"],
        how="left",
        suffixes=("", "_saved"),
    )
    merged["reviewed"] = pd.to_numeric(merged["reviewed_saved"], errors="coerce").fillna(
        pd.to_numeric(merged.get("reviewed", 0), errors="coerce").fillna(0)
    ).astype(int)
    merged["final_category"] = merged["category"].fillna(merged.get("suggested_category", ""))
    merged = merged.drop(
        columns=[
            "txn_date",
            "original_description",
            "amount",
            "reviewed_saved",
            "category",
        ],
        errors="ignore",
    )
    return merged


def prepare_saved_transactions_for_review(saved_df):
    if saved_df is None or saved_df.empty:
        return pd.DataFrame()

    pending_df = saved_df.copy()
    pending_df["reviewed"] = pd.to_numeric(pending_df.get("reviewed", 0), errors="coerce").fillna(0).astype(int)
    pending_df = pending_df[pending_df["reviewed"] == 0].copy()
    if pending_df.empty:
        return pending_df

    pending_df = pending_df.rename(columns={
        "txn_date": "Date",
        "original_description": "Description",
        "amount": "Amount",
        "category": "final_category",
    })
    pending_df["suggested_category"] = pending_df["final_category"].fillna("")
    pending_df["matched_reference"] = (
        pending_df["matched_reference"] if "matched_reference" in pending_df.columns else ""
    )
    pending_df["dup_flag"] = pending_df["dup_flag"] if "dup_flag" in pending_df.columns else False
    pending_df["normalized_description"] = (
        pending_df["normalized_description"].fillna("")
        if "normalized_description" in pending_df.columns
        else ""
    )
    pending_df["currency"] = (
        pending_df["currency"].fillna("USD")
        if "currency" in pending_df.columns
        else "USD"
    )
    usd_amount_series = (
        pending_df["usd_amount"]
        if "usd_amount" in pending_df.columns
        else pending_df["Amount"]
    )
    pending_df["usd_amount"] = pd.to_numeric(usd_amount_series, errors="coerce").fillna(
        pd.to_numeric(pending_df["Amount"], errors="coerce").fillna(0.0)
    )
    pending_df["source_occurrence"] = pd.to_numeric(
        pending_df.get("source_occurrence", 0), errors="coerce"
    ).fillna(0).astype(int)
    return pending_df


def render_review_queue(review_df, categories, section_key, action_container=None):
    if review_df is None or review_df.empty:
        return

    if not categories:
        st.error("No categories are configured. Upload your categories Excel first.")
        return

    working_df = review_df.copy()
    if "reviewed" not in working_df.columns:
        working_df["reviewed"] = 0

    working_df = working_df[working_df["reviewed"] == 0].copy()
    if working_df.empty:
        st.success("No pending transactions to review.")
        return

    reviewed_rows = []

    for i, row in working_df.iterrows():
        with st.container(border=True):
            col1, col2, col3, col4 = st.columns([2, 4, 2, 3])

            with col1:
                st.write(f"**Date:** {row['Date']}")
            with col2:
                st.write(f"**Description:** {row['Description']}")
                st.caption(f"Normalized: {row['normalized_description']}")
            with col3:
                st.write(f"**Amount:** {row['Amount']:.2f} {row['currency']}")
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

            default_category = ""
            if row.get("final_category") in categories:
                default_category = row["final_category"]
            elif row.get("suggested_category") in categories:
                default_category = row["suggested_category"]

            selected_category = st.selectbox(
                f"Category for row {i + 1}",
                categories,
                index=categories.index(default_category) if default_category in categories else None,
                placeholder="Select a category",
                key=f"cat_{section_key}_{i}",
            )

            reviewed = st.checkbox(
                f"Mark row {i + 1} as reviewed",
                value=bool(row.get("reviewed", 0)),
                key=f"reviewed_{section_key}_{i}",
            )

            final_row = row.to_dict()
            final_row["final_category"] = selected_category
            final_row["reviewed"] = 1 if reviewed else 0
            reviewed_rows.append(final_row)

    button_host = action_container if action_container is not None else st

    if button_host.button("Save reviewed classifications", type="primary", key=f"save_{section_key}"):
        final_df = pd.DataFrame(reviewed_rows)
        if final_df.empty:
            st.info("No transactions available to save.")
            return

        missing_review_categories = final_df[
            (final_df["reviewed"] == 1)
            & final_df["final_category"].fillna("").astype(str).str.strip().eq("")
        ]
        if not missing_review_categories.empty:
            st.error("Select a category for every reviewed transaction before saving.")
            return

        final_df["normalized_description"] = final_df["Description"].apply(
            lambda x: simplify_merchant(normalize_description(x))
        )

        final_df["normalized_description"] = final_df.apply(
            lambda r: r["normalized_description"]
            if str(r["normalized_description"]).strip()
            else str(r["Description"]).upper().strip(),
            axis=1,
        )

        final_df = final_df.drop_duplicates(
            subset=["Date", "Description", "Amount", "account_number", "source_occurrence"]
        )

        for idx, row in final_df.iterrows():
            clean_desc = row.get("normalized_description", "")

            if not str(clean_desc).strip():
                clean_desc = simplify_merchant(normalize_description(row["Description"]))

            if not str(clean_desc).strip():
                clean_desc = str(row["Description"]).upper().strip()

            if int(row.get("reviewed", 0)) == 1 and str(row.get("final_category", "")).strip():
                remember_transaction(
                    clean_desc,
                    row["beneficiary"],
                    row["transaction_type"],
                    row["final_category"],
                )

            final_df.at[idx, "normalized_description"] = clean_desc

        save_classified_transactions(final_df)
        update_access_backup_state(get_saved_transactions())
        st.success(
            f"Saved {int((final_df['reviewed'] == 1).sum())} reviewed transactions. "
            f"{int((final_df['reviewed'] == 0).sum())} transactions remain pending."
        )
        st.rerun()

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


def apply_audit_filters(df, key_prefix):
    if df is None or df.empty:
        return df

    st.caption("Use the filter controls above each column to narrow the editable rows.")
    filter_columns = st.columns(len(df.columns))
    filtered_df = df.copy()

    for idx, column in enumerate(df.columns):
        column_values = df[column].dropna().astype(str).str.strip()
        column_values = [value for value in column_values.unique().tolist() if value]
        column_values.sort(key=str.lower)

        with filter_columns[idx]:
            st.markdown(
                (
                    "<div style='font-size:0.8rem; color: rgba(250, 250, 250, 0.6); "
                    "margin-bottom: 0.25rem; white-space: nowrap; overflow: hidden; "
                    f"text-overflow: ellipsis;' title='{column}'>{column}</div>"
                ),
                unsafe_allow_html=True,
            )

            selected_values = st.session_state.get(f"{key_prefix}_{column}", [])
            if selected_values:
                filter_summary = f"{len(selected_values)} selected"
            else:
                filter_summary = "All"

            with st.popover(filter_summary):
                selected_values = st.multiselect(
                    f"Filter {column}",
                    options=column_values,
                    default=selected_values,
                    key=f"{key_prefix}_{column}",
                    label_visibility="collapsed",
                    placeholder="All",
                )

                if st.button("Clear", key=f"clear_{key_prefix}_{column}"):
                    st.session_state[f"{key_prefix}_{column}"] = []
                    st.rerun()

        if selected_values:
            filtered_df = filtered_df[
                filtered_df[column].astype(str).isin(selected_values)
            ]

    return filtered_df


def merge_filtered_audit_edits(original_df, filtered_original_df, edited_filtered_df):
    if "id" not in original_df.columns:
        return edited_filtered_df.copy()

    full_df = original_df.copy()
    visible_ids = set(
        pd.to_numeric(filtered_original_df["id"], errors="coerce").dropna().astype(int)
    )

    edited_df = edited_filtered_df.copy()
    edited_df["id"] = pd.to_numeric(edited_df["id"], errors="coerce")

    full_df = full_df[
        ~pd.to_numeric(full_df["id"], errors="coerce").isin(visible_ids)
    ]

    edited_existing_rows = edited_df[edited_df["id"].notna()].copy()
    new_rows = edited_df[edited_df["id"].isna()].copy()

    updated_df = pd.concat(
        [full_df, edited_existing_rows, new_rows],
        ignore_index=True,
    )

    return updated_df.reindex(columns=original_df.columns)


def update_access_backup_state(saved_df=None):
    backup_df = get_saved_transactions() if saved_df is None else saved_df.copy()
    if backup_df is None or backup_df.empty:
        st.session_state.pop("access_backup_csv", None)
        st.session_state.pop("access_backup_excel", None)
        st.session_state.pop("access_backup_file_stub", None)
        return

    file_stub = f"areti_access_backup_{pd.Timestamp.now().strftime('%Y%m%d_%H%M%S')}"
    st.session_state["access_backup_csv"] = build_access_backup_csv(backup_df)
    st.session_state["access_backup_excel"] = build_access_backup_excel(backup_df)
    st.session_state["access_backup_file_stub"] = file_stub


def render_access_backup_panel():
    backup_csv = st.session_state.get("access_backup_csv")
    backup_excel = st.session_state.get("access_backup_excel")
    file_stub = st.session_state.get("access_backup_file_stub", "areti_access_backup")

    if not backup_csv or not backup_excel:
        return

    st.warning(
        "Hosted app storage can reset. Download this backup after each save so you can import it into Access."
    )
    backup_col1, backup_col2 = st.columns(2)

    with backup_col1:
        st.download_button(
            label="Download Latest Access Backup CSV",
            data=backup_csv,
            file_name=f"{file_stub}.csv",
            mime="text/csv",
        )

    with backup_col2:
        st.download_button(
            label="Download Latest Access Backup Excel",
            data=backup_excel,
            file_name=f"{file_stub}.xlsx",
            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
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
    render_access_backup_panel()

    rates_df = get_monthly_rates()
    account_registry_df = get_account_registry()

    st.markdown("### Monthly FX Rates")
    rates_file = st.file_uploader(
        "Upload monthly rates Excel (optional)",
        type=["xlsx", "xls"],
        key="monthly_rates_upload",
    )

    if rates_file is not None:
        try:
            rates_file_token = get_uploaded_file_token(rates_file)
            if st.session_state.get("processed_rates_file") != rates_file_token:
                imported_rates_df = parse_monthly_rates_excel(rates_file)
                replace_monthly_rate_records(imported_rates_df)
                rates_df = get_monthly_rates()
                st.session_state["processed_rates_file"] = rates_file_token
                st.success(f"Imported {len(imported_rates_df)} monthly FX rate rows.")
        except Exception as e:
            st.error(str(e))

    if rates_df.empty:
        st.warning("No monthly FX rates imported yet. Non-USD statements will require the rates Excel first.")
    else:
        rates_table = (
            rates_df
            .pivot(index="source_currency", columns="report_month", values="rate_to_usd")
            .sort_index()
        )
        st.dataframe(rates_table, use_container_width=True)
        st.caption("Rates are stored as USD equivalent per 1 unit of source currency and reused until you upload a newer rates file.")

    available_currencies = get_statement_currencies(rates_df)

    st.markdown("### Account Registry")
    account_registry_file = st.file_uploader(
        "Upload 'Who made the expense' Excel",
        type=["xlsx", "xls"],
        key="account_registry_upload",
    )

    if account_registry_file is None:
        st.session_state["processed_account_registry_file"] = None

    if account_registry_file is not None:
        try:
            account_registry_file_token = get_uploaded_file_token(account_registry_file)
            if st.session_state.get("processed_account_registry_file") != account_registry_file_token:
                imported_accounts_df = parse_account_registry_excel(account_registry_file)
                replace_account_registry_records(imported_accounts_df)
                account_registry_df = get_account_registry()
                st.session_state["processed_account_registry_file"] = account_registry_file_token
                st.success(f"Imported {len(imported_accounts_df)} account registry rows.")
        except Exception as e:
            st.error(str(e))

    if account_registry_df.empty:
        st.warning("Upload the 'Who made the expense' Excel to populate account name, bank, account number, currency, and rate type.")
        account_options = []
        selected_account = None
    else:
        account_options = account_registry_df.to_dict("records")
        selected_account_df = pd.DataFrame(account_options)
        st.dataframe(selected_account_df, use_container_width=True, height=220)

    st.markdown("### Account Information")
    if account_options:
        selected_account = st.selectbox(
            "Select account",
            account_options,
            format_func=build_account_option_label,
            key="selected_account_registry",
        )

    account_name = selected_account["account_name"] if selected_account else ""
    account_number = selected_account["account_number"] if selected_account else ""
    account_bank = selected_account["bank"] if selected_account else ""
    statement_currency = selected_account["currency"] if selected_account else "USD"
    rate_type = selected_account["rate_type"] if selected_account else ""

    st.text_input("Account name", value=account_name, disabled=True)
    st.text_input("Bank", value=account_bank, disabled=True)
    st.text_input("Account number / IBAN", value=account_number, disabled=True)
    st.text_input("Statement currency", value=statement_currency, disabled=True)
    st.text_input("Rate type", value=rate_type, disabled=True)

    st.markdown("### Manual Transaction Entry")

    with st.expander("Add transaction manually", expanded=False):
        m_date = st.date_input("Date")
        m_desc = st.text_input("Description")
        m_amount = st.number_input("Amount", format="%.2f")
        m_currency = st.selectbox("Currency", available_currencies, key="manual_currency")
        manual_categories = get_categories()
        if manual_categories:
            m_category = st.selectbox("Category", manual_categories, key="manual_category")
        else:
            st.warning("Upload your categories Excel before adding manual transactions.")
            m_category = None

        if st.button("Add Manual Transaction", disabled=not manual_categories):
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
                "beneficiary": beneficiary,
                "transaction_type": txn_type,
                "final_category": m_category,
                "match_type": "manual",
                "confidence": 1.0,
                "reviewed": 1,
                "account_name": selected_account["account_name"] if selected_account else "",
                "account_number": selected_account["account_number"] if selected_account else "",
                "bank": selected_account["bank"] if selected_account else "",
            }

            df_manual = attach_statement_currency(
                pd.DataFrame([manual_row]),
                selected_account["currency"] if selected_account else m_currency,
                rates_df,
            )
            save_classified_transactions(df_manual)
            remember_transaction(
                norm_desc,
                beneficiary,
                txn_type,
                m_category,
            )
            update_access_backup_state(get_saved_transactions())
            st.success("Manual transaction saved successfully.")
            st.rerun()

    uploaded_file = st.file_uploader(
        "Choose a file", type=["csv", "xlsx", "xls", "pdf"]
    )

    pending_review_df = prepare_saved_transactions_for_review(get_saved_transactions())

    if uploaded_file is None and not pending_review_df.empty:
        pending_action_container = st.container()
        st.markdown("### Pending transactions")
        st.caption("Previously saved pending transactions remain here until you review them.")
        render_review_queue(
            pending_review_df,
            get_categories(),
            "pending_db",
            action_container=pending_action_container,
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
            df["bank"] = account_bank

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
            categories = get_categories()
            if not categories:
                st.error("Upload your categories Excel before classifying transactions.")
                st.stop()

            memory_df = get_memory()
            classified_df = classify_transactions(df, memory_df)
            if "reviewed" not in classified_df.columns:
                classified_df["reviewed"] = 0

            if "dup_flag" in df.columns:
                classified_df["dup_flag"] = df["dup_flag"].values

            classified_df = attach_statement_currency(
                classified_df,
                statement_currency,
                rates_df,
            )
            classified_df["account_name"] = account_name
            classified_df["account_number"] = account_number
            classified_df["bank"] = account_bank
            classified_df = merge_saved_review_state(classified_df, get_saved_transactions())

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

            render_review_queue(classified_df, categories, "uploaded")

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
        if "usd_amount" in saved_df.columns:
            saved_df["usd_amount"] = pd.to_numeric(saved_df["usd_amount"], errors="coerce")

        if "currency" not in saved_df.columns:
            saved_df["currency"] = "USD"
        if "usd_amount" not in saved_df.columns:
            saved_df["usd_amount"] = saved_df["amount"]
        else:
            saved_df["usd_amount"] = saved_df["usd_amount"].fillna(saved_df["amount"])

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
        st.caption("All KPIs, summaries, charts, and exports use USD equivalent amounts when available.")

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

        st.markdown("### Full Backup For Access")
        st.caption("This exports the full saved transactions table, not only the filtered report above.")
        access_backup_col1, access_backup_col2 = st.columns(2)

        with access_backup_col1:
            access_backup_csv = build_access_backup_csv(saved_df)
            st.download_button(
                label="Download Full Access Backup CSV",
                data=access_backup_csv,
                file_name="areti_access_full_backup.csv",
                mime="text/csv",
            )

        with access_backup_col2:
            access_backup_excel = build_access_backup_excel(saved_df)
            st.download_button(
                label="Download Full Access Backup Excel",
                data=access_backup_excel,
                file_name="areti_access_full_backup.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )

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
        ["Transactions", "Memory", "Categories", "Accounts", "Rates"]
    )

    audit_df = None
    save_audit_changes = None

    if view_option == "Transactions":
        audit_df = get_saved_transactions()
        save_audit_changes = replace_saved_transaction_records
    elif view_option == "Memory":
        audit_df = get_memory()
        save_audit_changes = replace_memory_records
    elif view_option == "Categories":
        audit_df = get_category_records()
        save_audit_changes = replace_category_records
    elif view_option == "Accounts":
        audit_df = get_account_registry()
        save_audit_changes = replace_account_registry_records
    elif view_option == "Rates":
        audit_df = get_monthly_rates()
        save_audit_changes = replace_monthly_rate_records

    if audit_df is not None:
        filtered_audit_df = apply_audit_filters(
            audit_df,
            f"audit_filter_{view_option.lower()}",
        )

        column_config = None
        if view_option == "Transactions":
            category_options = get_categories()
            column_config = {
                "category": st.column_config.SelectboxColumn(
                    "category",
                    options=category_options,
                    required=False,
                )
            } if category_options else None

        edited_audit_df = st.data_editor(
            filtered_audit_df,
            use_container_width=True,
            num_rows="dynamic",
            column_config=column_config,
            key=f"audit_editor_{view_option.lower()}",
        )

        if st.button(f"Save {view_option} changes", key=f"save_{view_option.lower()}"):
            updated_audit_df = merge_filtered_audit_edits(
                audit_df,
                filtered_audit_df,
                edited_audit_df,
            )
            save_audit_changes(updated_audit_df)
            st.success(f"{view_option} updated successfully.")
            st.rerun()

    if audit_df is not None and not audit_df.empty:
        csv = audit_df.to_csv(index=False).encode("utf-8")
        st.download_button(
            "Download as CSV",
            csv,
            file_name=f"{view_option}.csv",
            mime="text/csv",
        )

    st.subheader("Manage categories")

    current_category_records = get_category_records()
    if current_category_records.empty:
        st.info("No categories uploaded yet.")
    else:
        st.dataframe(
            current_category_records[["category", "subcategory"]],
            use_container_width=True,
            hide_index=True,
        )

    new_category = st.text_input("Add new category")
    new_subcategory = st.text_input("Add subcategory (optional)")

    if st.button("Add category"):
        add_category(new_category, new_subcategory)
        st.success(f"Category added: {new_category}")
        st.rerun()

    st.markdown("### Upload Categories from Excel")

    cat_file = st.file_uploader("Upload categories file", type=["xlsx", "xls"])

    if cat_file is not None:
        try:
            from db import replace_categories

            imported_categories_df = parse_category_excel(cat_file)

            with st.expander(
                f"Imported category rows ({len(imported_categories_df)})",
                expanded=True,
            ):
                st.dataframe(
                    imported_categories_df,
                    use_container_width=True,
                    height=320,
                    hide_index=True,
                )

            replace_categories(imported_categories_df.to_dict("records"))
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
