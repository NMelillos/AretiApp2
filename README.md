# Statement Management

Local workspace for importing bank statements, reviewing expenses, maintaining account/rate/category tables, and exporting reports.

## Run Locally

```bash
python -m streamlit run app.py --server.port 8501
```

Then open:

```text
http://localhost:8501
```

## Main Files

- `app.py` - Streamlit application
- `db.py` - local SQLite storage and Excel import/export helpers
- `parsing.py` - statement readers for PDF, Excel, and CSV
- `classification.py` - transaction category matching
- `reporting.py` - sample expenses report export
- `transactions.db` - local data file created beside the application

## Setup Workbooks

The application can load the control workbooks from:

```text
C:\Users\Student\Dropbox\ARETI FILES ONE DRIVE
```

Expected workbook names:

- `Expenses categories.xlsx`
- `Who made the expense.xlsx`
- `Rates.xlsx`

## Production Database

For production, create a PostgreSQL database, for example Supabase Postgres or Render Postgres, and set `DATABASE_URL` on the web service to the database connection string. When `DATABASE_URL` is present, the app uses PostgreSQL for setup files, transactions, statement history, balances, and memory.

SQLite remains available only as a local fallback through `transactions.db`.

Supabase or Render setup:

1. Create a PostgreSQL database.
2. Copy the PostgreSQL connection string. For Supabase, use the connection string supplied by the project database or pooler and include the database password.
3. Add it to the `aretiapp` web service as `DATABASE_URL`.
4. Redeploy the web service.
5. Log in and load the setup files once. After that, data is stored in PostgreSQL and remains available across browsers and Render restarts.
