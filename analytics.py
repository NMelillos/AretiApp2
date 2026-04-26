# =========================
# FILE: analytics.py
# =========================
import pandas as pd


def _with_analysis_amount(df: pd.DataFrame):
    work = df.copy()
    fallback_amount = pd.to_numeric(work.get("amount"), errors="coerce")

    if "usd_amount" in work.columns:
        usd_amount = pd.to_numeric(work["usd_amount"], errors="coerce")
        work["analysis_amount"] = usd_amount.fillna(fallback_amount)
    else:
        work["analysis_amount"] = fallback_amount

    work["analysis_amount"] = work["analysis_amount"].fillna(0.0)
    return work


def detect_duplicates(df: pd.DataFrame):
    if df.empty:
        df = df.copy()
        df["dup_flag"] = False
        return df

    work = df.copy()
    subset_cols = [c for c in ["Date", "Amount", "normalized_description"] if c in work.columns]
    work["dup_flag"] = work.duplicated(subset=subset_cols, keep=False)
    return work


def detect_saved_duplicates(df: pd.DataFrame):
    if df.empty:
        df = df.copy()
        df["dup_flag"] = False
        return df

    work = df.copy()
    subset_cols = [c for c in ["txn_date", "amount", "normalized_description"] if c in work.columns]
    work["dup_flag"] = work.duplicated(subset=subset_cols, keep=False)
    return work


def detect_anomalies(df: pd.DataFrame):
    if df.empty:
        df = df.copy()
        df["anomaly"] = False
        return df

    df = _with_analysis_amount(df)
    df["anomaly"] = False

    expense_df = df[df["analysis_amount"] < 0].copy()

    if expense_df.empty:
        return df

    mean_abs = expense_df["analysis_amount"].abs().mean()
    std_abs = expense_df["analysis_amount"].abs().std()

    if pd.isna(std_abs) or std_abs == 0:
        return df

    z_scores = (df["analysis_amount"].abs() - mean_abs) / std_abs
    df["anomaly"] = (df["analysis_amount"] < 0) & (z_scores > 2)

    return df.drop(columns=["analysis_amount"], errors="ignore")


def detect_recurring_expenses(df: pd.DataFrame):
    if df.empty:
        return pd.DataFrame()

    work = _with_analysis_amount(df)
    work["txn_date"] = pd.to_datetime(work["txn_date"], errors="coerce")
    work = work.dropna(subset=["txn_date"])
    work = work[work["analysis_amount"] < 0].copy()

    if work.empty:
        return pd.DataFrame()

    work["month"] = work["txn_date"].dt.to_period("M").astype(str)
    work["abs_amount"] = work["analysis_amount"].abs().round(2)

    recurring = (
        work.groupby(["normalized_description", "category"], dropna=False)
        .agg(
            occurrences=("id", "count"),
            months_active=("month", "nunique"),
            avg_amount=("abs_amount", "mean"),
            min_amount=("abs_amount", "min"),
            max_amount=("abs_amount", "max"),
            sample_description=("original_description", "first"),
            last_seen=("txn_date", "max"),
        )
        .reset_index()
    )

    recurring = recurring[recurring["months_active"] >= 2].copy()

    if recurring.empty:
        return recurring

    recurring["variation_pct"] = (
        (recurring["max_amount"] - recurring["min_amount"]) /
        recurring["avg_amount"].replace(0, pd.NA)
    ) * 100

    recurring["variation_pct"] = recurring["variation_pct"].fillna(0).round(1)

    recurring = recurring[recurring["variation_pct"] <= 25].copy()

    recurring["avg_amount"] = recurring["avg_amount"].round(2)
    recurring["min_amount"] = recurring["min_amount"].round(2)
    recurring["max_amount"] = recurring["max_amount"].round(2)

    return recurring.sort_values(
        by=["months_active", "occurrences", "avg_amount"],
        ascending=[False, False, False]
    )


def predict_next_month_expense(df: pd.DataFrame):
    if df.empty:
        return None, pd.DataFrame()

    work = _with_analysis_amount(df)
    work["txn_date"] = pd.to_datetime(work["txn_date"], errors="coerce")
    work = work.dropna(subset=["txn_date"])
    work = work[work["analysis_amount"] < 0].copy()

    if work.empty:
        return None, pd.DataFrame()

    work["month"] = work["txn_date"].dt.to_period("M").astype(str)

    monthly = (
        work.groupby("month")["analysis_amount"]
        .sum()
        .abs()
        .reset_index(name="expense_total")
        .sort_values("month")
    )

    if monthly.empty:
        return None, monthly

    if len(monthly) == 1:
        return round(float(monthly["expense_total"].iloc[0]), 2), monthly

    monthly["prev"] = monthly["expense_total"].shift(1)
    monthly["delta"] = monthly["expense_total"] - monthly["prev"]

    avg_delta = monthly["delta"].dropna().mean()
    if pd.isna(avg_delta):
        avg_delta = 0

    prediction = float(monthly["expense_total"].iloc[-1] + avg_delta)
    if prediction < 0:
        prediction = 0.0

    return round(prediction, 2), monthly


def detect_seasonality(df: pd.DataFrame):
    if df.empty:
        return pd.DataFrame()

    work = _with_analysis_amount(df)
    work["txn_date"] = pd.to_datetime(work["txn_date"], errors="coerce")
    work = work.dropna(subset=["txn_date"])

    if work.empty:
        return pd.DataFrame()

    work["month_num"] = work["txn_date"].dt.month
    work = work[work["analysis_amount"] < 0].copy()

    if work.empty:
        return pd.DataFrame()

    seasonal = (
        work.groupby("month_num")["analysis_amount"]
        .sum()
        .abs()
        .reset_index(name="expense_total")
    )

    seasonal["month_name"] = pd.to_datetime(
        seasonal["month_num"], format="%m"
    ).dt.strftime("%b")

    return seasonal.sort_values("month_num")


def build_monthly_income_expense(report_df: pd.DataFrame):
    if report_df.empty:
        return pd.DataFrame(columns=["month", "income", "expense", "net"])

    work = _with_analysis_amount(report_df)
    work["txn_date"] = pd.to_datetime(work["txn_date"], errors="coerce")
    work = work.dropna(subset=["txn_date"])
    work["month"] = work["txn_date"].dt.to_period("M").astype(str)

    monthly_income_expense = (
        work.assign(
            income=work["analysis_amount"].where(work["analysis_amount"] > 0, 0),
            expense=work["analysis_amount"].where(work["analysis_amount"] < 0, 0).abs()
        )
        .groupby("month")[["income", "expense"]]
        .sum()
        .reset_index()
        .sort_values("month")
    )

    monthly_income_expense["net"] = monthly_income_expense["income"] - monthly_income_expense["expense"]
    return monthly_income_expense


def calculate_kpis(report_df: pd.DataFrame, monthly_income_expense: pd.DataFrame):
    work = _with_analysis_amount(report_df)
    income_df = work[work["analysis_amount"] > 0].copy()
    expense_df = work[work["analysis_amount"] < 0].copy()

    total_income = float(income_df["analysis_amount"].sum()) if not income_df.empty else 0.0
    total_expenses = float(expense_df["analysis_amount"].abs().sum()) if not expense_df.empty else 0.0
    net_result = total_income - total_expenses

    avg_monthly_income = float(monthly_income_expense["income"].mean()) if not monthly_income_expense.empty else 0.0
    avg_monthly_expenses = float(monthly_income_expense["expense"].mean()) if not monthly_income_expense.empty else 0.0

    if not monthly_income_expense.empty:
        burn_series = (monthly_income_expense["expense"] - monthly_income_expense["income"]).clip(lower=0)
        burn_rate = float(burn_series.mean())
    else:
        burn_rate = 0.0

    if total_income > 0:
        savings_rate = (net_result / total_income) * 100
    else:
        savings_rate = 0.0

    return {
        "total_income": round(total_income, 2),
        "total_expenses": round(total_expenses, 2),
        "net_result": round(net_result, 2),
        "burn_rate": round(burn_rate, 2),
        "savings_rate": round(savings_rate, 2),
        "avg_monthly_income": round(avg_monthly_income, 2),
        "avg_monthly_expenses": round(avg_monthly_expenses, 2),
        "months_covered": int(monthly_income_expense["month"].nunique()) if not monthly_income_expense.empty else 0,
    }


def build_period_label(months: int, selected_category: str):
    category_label = selected_category if selected_category != "All" else "All Categories"
    return f"Last {months} month(s) | {category_label}"


def build_report_context(report_df: pd.DataFrame, months: int, selected_category: str):
    context = {}
    work = _with_analysis_amount(report_df)
    work["txn_date"] = pd.to_datetime(work["txn_date"], errors="coerce")
    work = work.dropna(subset=["txn_date"]).copy()
    work["month"] = work["txn_date"].dt.to_period("M").astype(str)

    period_label = build_period_label(months, selected_category)
    month_coverage = ", ".join(sorted(work["month"].unique().tolist())) if not work.empty else "N/A"

    summary = (
        work.groupby("category", dropna=False)["analysis_amount"]
        .agg(["count", "sum", "mean"])
        .reset_index()
    )

    monthly_summary = (
        work.groupby(["month", "category"])["analysis_amount"]
        .sum()
        .reset_index()
        .sort_values(["month", "category"])
    )

    expense_monthly = work[work["analysis_amount"] < 0].copy()
    monthly_total = (
        expense_monthly.groupby("month")["analysis_amount"]
        .sum()
        .abs()
        .reset_index(name="amount")
        .sort_values("month")
    )

    if not monthly_total.empty:
        monthly_total["diff"] = monthly_total["amount"].diff().round(2)
        monthly_total["change_%"] = (
            monthly_total["diff"] / monthly_total["amount"].shift(1) * 100
        ).round(1)
        monthly_total["trend"] = monthly_total["diff"].apply(
            lambda x: "⬆ Increase" if x > 0 else ("⬇ Decrease" if x < 0 else "—")
        )
    else:
        monthly_total["diff"] = pd.Series(dtype="float64")
        monthly_total["change_%"] = pd.Series(dtype="float64")
        monthly_total["trend"] = pd.Series(dtype="object")

    monthly_income_expense = build_monthly_income_expense(work)
    kpis = calculate_kpis(work, monthly_income_expense)
    recurring_df = detect_recurring_expenses(work)
    seasonal_df = detect_seasonality(work)
    prediction, prediction_source = predict_next_month_expense(work)

    category_expenses = (
        work[work["analysis_amount"] < 0]
        .groupby("category")["analysis_amount"]
        .sum()
        .abs()
        .reset_index(name="expense_total")
        .sort_values("expense_total", ascending=False)
    )

    context["report_df"] = work.drop(columns=["analysis_amount"], errors="ignore")
    context["summary"] = summary
    context["monthly_summary"] = monthly_summary
    context["monthly_total"] = monthly_total
    context["monthly_income_expense"] = monthly_income_expense
    context["recurring_df"] = recurring_df
    context["seasonal_df"] = seasonal_df
    context["prediction"] = prediction
    context["prediction_source"] = prediction_source
    context["category_expenses"] = category_expenses
    context["kpis"] = kpis
    context["period_label"] = period_label
    context["month_coverage"] = month_coverage
    context["selected_category"] = selected_category
    context["months"] = months

    return context
