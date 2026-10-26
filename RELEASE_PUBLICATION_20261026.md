# Approved package publication — 26 October 2026

## Publication boundary

The latest user instruction authorises publishing the completed, tested package
to GitHub main and explicitly prohibits Render deployment in this task. This
supersedes the earlier documentation-only main-publication hold; it does not
authorise production financial writes or expand the functional package.

Preflight directly read GitHub main at
`b823da5d0bda450c8ee71c736c5de28e9d36faa8` and the clean release-validation
branch at `0b24961ab8edc19b74e74b4a5ec9a27d5efa5091`. The origin is
`https://github.com/NMelillos/AretiApp2.git`. Render independently reported
that same baseline LIVE on `dep-dbcd6pjbc2fs73c2ruvg`, Auto-Deploy OFF,
for the exact authorised service/workspace. No deployment is requested here.

## Completed application scope

The implementation already exists in the preserved candidate. No new
application correction was justified by the current evidence. All 57
application Python modules match verified application basis
`f3e650ec0416096564fa49a1cce470ae53dbe28b` after checkout newline
normalisation. No completed business functionality was reopened.

Application files changed relative to production:

- `app.py`: stop normal PDF transaction handling for the verified AMEX
  metadata-only route; CSV transaction workflow remains unchanged.
- `amex_balance_metadata.py`: source- and imported-CSV-proven PDF metadata.
- `completed_balance_sources.py`: reuse strict English Revolut/CNB validation.
- `duplicate_balance_import.py`: bounded balance completion and truthful repeat
  guidance, preserving transactions, identity, duplicate and rollback guards.
- `import_history.py`: recognise verified zero-transaction AMEX PDF metadata.
- `latest_import_balances.py`, `latest_balances_compact.py`,
  `balance_report_sections.py`: independent balance dates and approved
  Deposits/Debts presentation, preserving eligibility, FX and partial scope.

Existing package tests and evidence documents are also included. See
`git diff --name-status b823da5d0bda450c8ee71c736c5de28e9d36faa8 <release-sha>`
for the complete publication file list.

## Verification

Fresh results are recorded in `QA_PUBLICATION_20261026.json`. The full QA
inventory remains distinct from the narrow package acceptance suite. Failed,
fixture-blocked and environment-bound checks must not be labelled passed.
The 24 October detailed classifications and original logs remain preserved;
see `QA_TRIAGE_20261024.md` for their baseline comparisons and limitations.

The fresh runner is the existing `final_run.py`, copied into private
`qa-release-20261026` evidence without replacing the 24 October results.
It uses the existing allowlisted child environment and audit hook: no
production credentials, external network or outside-root SQLite access.
PostgreSQL uses only the verified disposable loopback cluster and temporary
test databases. The initial sandbox server-start denial and unavailable
Python-path invocation remain recorded; the installed CPython 3.12 runtime
and authorised local server start were used for the executed tests.

Source protection verifies unchanged `db.py`, `parsing.py`,
`financial_writer_control.py` and `requirements.txt` against production.
The package scope/AST test protects Family/Charity, SPLIT, Pending Review,
Setup, report navigation and all app branches outside Import. No application
schema, migration, financial calculation, parser, historical repair or
production configuration change is included. No GitHub workflow exists in
the candidate to trigger a deployment on main publication.

## Remaining work and handover

Full QA is not declared green. Missing original PDFs, historical scope
assertions, E:-bound harnesses and obsolete UI doubles remain explicitly
qualified; no invented fixture or relaxed financial guard is included.
Full Revolut currency-wallet implementation remains design-only and requires
the separate representation/schema/configuration decision. Account 5055
eligibility still requires exact authenticated evidence; it is not declared
a defect. Historical restart cause and live business acceptance are not
established by local QA or GitHub publication.

Areti UAT remains pending for Natalia Revolut balances, CNB Woking Way
balances, AMEX PDF metadata with CSV transactions, Deposits/Debts totals and
truthful Balance Import Date completion/repeat behavior. No production import
or financial test edit was performed. The user will handle manual Render
deployment separately; publication is not deployment or business acceptance.

The publication receipt is the exact final commit and direct GitHub ref
readback in the task response, not this document alone.
