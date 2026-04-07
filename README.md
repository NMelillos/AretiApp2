# AretiApp Deployment

This project is a Streamlit application.

## Vercel

Vercel is not a good fit for this codebase in its current form.

Reasons:

- The UI is built with Streamlit, which expects a long-running app server and websocket-based interaction.
- The project stores data in a local SQLite file named `transactions.db`.
- Vercel Python runs as request-driven serverless functions, so local filesystem state is not reliable for application data.

If you want to use Vercel, the app should be refactored into a Vercel-compatible web app, for example:

- FastAPI or Flask backend
- external database such as Neon, Supabase Postgres, or Turso
- separate frontend if needed

## Recommended deployment options

### Option 1: Render

This repository includes a `render.yaml` file.

Steps:

1. Push the repository to GitHub.
2. Open Render and create a new Blueprint instance from the repository.
3. Render will detect `render.yaml`.
4. Deploy the service.

If you need SMTP configuration, add these environment variables in the target platform:

- `SMTP_HOST`
- `SMTP_PORT`
- `SMTP_USERNAME`
- `SMTP_PASSWORD`
- `EMAIL_FROM`
- `EMAIL_TO`
- `SMTP_USE_TLS`

Start command used by Render:

```bash
streamlit run app.py --server.address 0.0.0.0 --server.port $PORT
```

### Option 2: Streamlit Community Cloud

Steps:

1. Push the repository to GitHub.
2. Open Streamlit Community Cloud.
3. Create a new app from the repository.
4. Set the main file path to `app.py`.
5. Add secrets if you want email sending.

Supported Streamlit secret keys:

- `smtp_host`
- `smtp_port`
- `smtp_username`
- `smtp_password`
- `email_from`
- `email_to`
- `smtp_use_tls`

## Important runtime notes

- `transactions.db` is local application state. On hosted platforms with ephemeral storage, the data may reset on redeploy or restart.
- For production use, replace SQLite with a managed database.
- OCR-based PDF parsing may require system packages such as Tesseract and Poppler. If those are not available on the host, scanned PDF OCR may not work.

## Local run

```bash
pip install -r requirements.txt
streamlit run app.py
```