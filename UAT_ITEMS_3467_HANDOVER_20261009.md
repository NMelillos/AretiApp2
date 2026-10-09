# Controlled UAT candidate: items 3, 4, 6, 7 only

Baseline: deployed application c68b397a7b81f4098adfa68b055408c4b5995cea. Local branch codex/areti-uat-3467-release-20261009. Candidate: commit containing this handover, pinned in private uat-final-receipt.json. New worktree only; complete59822eb and preserved0152a2d/full/reduced worktrees unchanged. No push/deployment authorized or performed in this task.

Exact application scope:
- latest_balances_compact.py: Bank/import date/account number/closing date columns, Bank then import-date sort, no grouping; section4 title class; trim trailing zeroes without rounding; native/USD/partial totals/exclusions preserved.
- latest_import_balances.py: shared closing-date calendar age0..39 green, >=40/unknown/invalid/future red; import dates black; consistent THIRD/dedicated HTML/print/Excel. Snapshot SQL, storage provenance, FX and total eligibility unchanged.
- fifth_third_import.py: strict source-labelled parser for both supplied commercial layouts; exact identity/period/counts/signs/totals/reconciliation; reject changed/truncated/reordered/wrong-identity input. Retain exact existing Comerica/USD Setup identities after acquisition; no account/taxonomy change.
- parsing.py: only Fifth Third recognition, balance extraction and explicit fail-closed error route.
- import_history.py: only Fifth Third exact-source preview/account guard before unchanged atomic persistence.
- Item7: no application change. Disposable removal/replacement of Setup account preserves reviewed classification, non-empty memory, imported transactions/history/balances and Pending state.

Items1/2/5/8 are excluded. boc_import.py, BOC summary/balance-only guards, English Revolut functions, application UI/authentication, Statement Summary, Setup/database schema/startup/backfill, classifiers/SPLIT, existing financial modules and other accepted application files match baseline byte-for-byte. No Russian Revolut adapter or new BOC layout fix included. No performance or LOC code. Scope/AST tests prove all existing parser functions unchanged and atomic boundary unchanged after removing the single additive Fifth guard.

Validation against the actual reduced source (not the earlier48-result package):
- uat-source-scope:8 unittest methods PASS (two original Fifth sources,3/8 transactions, balances/signs/reconciliation, fail-closed guards, real disposable commit/duplicates/History/Pending, actual Upload using preserved Comerica identities, source/scope preservation).
- uat-report-setup:3 unittest methods PASS (compact display/totals/exports, closing-date boundaries, historical reviewed classifications/memory preservation).
- uat-preserved-regression:35 unittest methods PASS (Comerica5055/4128, accepted BOC/zero rows, messages/buttons, rollback/Pending/History/duplicates, Safra/NOMAD, Citi/CNB, THIRD and supported BOC Statement Summary).
- TOTAL46 distinct unittest methods PASS;0 unresolved failures.
- uat-postgresql-integrity-corrected: full script PASS:2 SQLite +2 PostgreSQL assertion groups. Owned local PostgreSQL18.6 cluster verified by data_directory before creating unique disposable fixture DBs; report reads enforced read-only. NUMERIC exact values, REAL/DOUBLE exclusions, liability/unknown-data safety, identity collapse, alias/currency separation, old timestamps, invalid periods, exports/partial totals pass. No production connection.
- Original PostgreSQL failure retained: fixture omitted existing source/notes/flow columns needed by the baseline query. Corrected fixture DDL and explicit INSERT columns only; all assertions retained; no application/schema migration. Initial pg_ctl capture timed out, but the started server was independently verified through owned PID/data_directory/local port/read-only connection; never treated the controller timeout as a successful command. Validation ran only against this verified owned cluster. Final read-only catalog proves no report fixture databases remain; cluster cleanly stopped, its PID file removed.
- Syntax/diff checks PASS. Runtime strips production credentials and blocks external network/credential-file reads; SQLite confined to disposable files. Parent environment unchanged. Private logs/failed results/PDFs and final source manifest outside Git.

IMPLEMENTED/TESTED: selected local scope. DEPLOYED:NO. DATA-REPAIRED:NONE. USER-ACCEPTED/UAT:PENDING. Auto-Deploy/environment/database configuration untouched. No production writes, repairs/reimports/schema/credential changes. Recovery target f3ea9ce2d0143ef3239871bb94791ffb39fd857e unchanged. Deployment needs approval of the exact new candidate and fresh release-window gates; this readiness is controlled-UAT candidate readiness, not a deployment claim.
