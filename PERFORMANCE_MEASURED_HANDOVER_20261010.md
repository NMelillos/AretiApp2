# Measured performance candidate — 2026-10-10

Baseline: de8c6c7fbc963eaacf68c74c6117ba6eb3e19ae1. Independent branch codex/areti-performance-measured. LOCAL ONLY; no push, deployment, cloud configuration, production database query/write or schema change. All closed functionality and other worktrees preserved.

## Confirmed findings

Setup, Database and Reports eagerly construct unrequested downloads on every render/rerun. Setup's full backup serialization consumed 8.95 seconds of an 11.19-second local render. Database's filtered workbook serialization consumed 9.78 seconds of an 18.11-second render. SQL totals were 0.137 and 0.115 seconds respectively on disposable SQLite. Reports eagerly constructs styled workbooks, complete/group PDFs, a ZIP and separate group PDFs; baseline timed out at 90 seconds, so its completion time and exact per-export contribution are NOT known.

Read-only Render evidence for the user-specified incident, 2026-10-09 15:05–15:25 UTC (18:05–18:25 Cyprus): 502 log entries, no operation timing markers, no traceback/OOM/429/backfill/restart text. Same instance across the interval. CPU reached its 0.15-core ceiling at 15:14, 15:15, 15:18 and 15:19. Peak measured memory was 515,375,100 bytes (491.5 MiB, approximately 96% of the 512 MiB limit). HTTP latency series was empty. Repeated query warnings identify invocation sites, NOT query duration. This supports resource-pressure correlation; it does NOT prove the reported ten-minute incident's complete causal chain, an OOM, a leak, or a 429 cause.

## Minimal change

Only app.py: use Streamlit 1.61.1's native zero-argument download callables to serialize nine download definitions when requested. functools.partial binds each current view/group; the existing ZIP construction moves inside a callable with captured current inputs. Existing exporter functions, filenames, dataframes, financial/report calculations, parser/import paths, cache behavior, pending refresh, backfill and SPLIT functions are unchanged. Deferred callbacks make no database calls and execute no Streamlit commands. Requesting a large export still costs CPU and memory; this change removes that cost from idle navigation. Existing other-page export paths remain unchanged because they were not proven dominant in this bounded pass.

## Executed comparison

Windows Python 3.12, Streamlit 1.61.1, one BLAS thread, disposable SQLite. Identical synthetic fixture: 5,200 transactions, 47 accounts, 40 categories, 20 groups. First render / same-session rerun, seconds:

| Page | Baseline | Candidate |
|---|---:|---:|
| Setup | 11.243 / 10.733 | 2.592 / 1.769 |
| Database | 18.126 / 17.999 | 9.143 / 8.526 |
| Reports | timeout at 90 / not reached | 2.749 / 2.638 |
| Pending Review | 1.755 / 1.667 | 1.896 / 1.888 |
| Latest Import Balances | not reached | 1.899 / 2.020 |
| Import empty upload view | not reached | 1.603 / 1.566 |

Candidate idle views constructed zero export files. SQL call counts retained for equivalent measured pages. All measured views preserved the complete fixture database snapshot. Candidate worker exited zero in 47.94 seconds, peak working set 169.7 MiB; ceiling 640 MiB / 240 seconds. Baseline worker peak 260.8 MiB covered fewer pages and a timed-out report, so those peaks are not a leak test or like-for-like retained-memory claim. Local timing is not live PostgreSQL/network timing and does not simulate Render's CPU allocation. cProfile on the waiting main thread is not used for attribution; exporter/query wrappers timed actual calls. Browser/websocket login, live SPLIT editing, production import/post-import latency and exact live query durations remain unmeasured. No production statement import was performed.

## Targeted verification

21 distinct targeted tests resolved PASS: seven new download tests, four import preview/commit/rollback/duplicate checks, three authentication/Reports-to-THIRD navigation checks, two layout/freshness/total checks, four saved-custom-prompt/data checks, one full existing SQLite SPLIT editing exercise. New checks compare PDF bytes and every ZIP/XLSX member against de8 (except timestamp-bearing XLSX docProps/core.xml), exercise two different reporting groups, verify native callable registration, no eager idle serialization, frozen-view downloads with database access forbidden, no read-only-view mutation, and identical baseline business functions/non-app Python modules. Pending renders/count queries and Latest Import Balances also passed the measured page runs.

First runner failed before test execution because the reused presentation-test module needed its existing private source-expectations file in the new disposable root. Copying that existing file corrected fixture setup; no invented evidence. Integrated run: 20 passed, one Windows cleanup error after successful assertions due to the new test's unclosed SQLite connection. Corrected only new harness contexts with contextlib.closing; affected test rerun PASS. Original failures retained. Syntax and diff whitespace checks PASS. No unchanged full suite repeated.

Private evidence root: C:/Users/Student/.codex/visualizations/2026/10/05/01a10a5f-345e-7a42-a6c2-036223abacf9. Files: performance-incident-metrics.json, performance-incident-summary.json; performance-baseline-v2/timings.json and worker.log; performance-attribution/timings.json; performance-after/timings.json, limits.json and worker.log; performance-qa/targeted-regression.log, targeted-regression-v2.log and connection-cleanup-resolved.log. Initial nonexistent-fixture-column failure and failed stack-sampling attempt remain private and are excluded from valid timing evidence. No private fixtures, logs, statements or credentials are committed.

IMPLEMENTED / LOCALLY TESTED: export deferral. READY for controlled performance checking of this bounded correction; exact production ten-minute root-cause attribution and before/after live timings remain unverified. DEPLOYED: NO. DATA-REPAIRED: NONE. BUSINESS-ACCEPTED: pending. Do not deploy without separate approval; do not claim the complete performance complaint solved.
