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

## Render Persistence

SQLite data is saved to `transactions.db`. On Render, attach a persistent disk at `/var/data` for production use; the app will then save the database as `/var/data/transactions.db`.
