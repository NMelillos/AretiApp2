# Areti autonomous package — 2026-10-08

This bounded package starts from deployed 13d7845b703ae8a4c960111d26c241d289624d3d. Worktree: areti-autonomous-final-20261008/test; branch codex/areti-autonomous-final-20261008. All earlier candidates, their handovers and the unrelated unexpected documentation edit were preserved. No live development/test import or financial query was used.

## Implemented and tested

- Citi: narrowly source-proved refund-cheque direction. Labelled liability summary and exact row sum must reconcile; unrelated cards are unchanged and recognised inconsistent Citi evidence fails closed. Existing descriptions, row count, negative card outflows and classifications remain intact. No historical sign repair.
- BOC Statement Summary: explicit Import balances only, source preview and authenticated confirmation. Source bytes are reverified using the existing summary and bank-column proof, Decimal arithmetic and exact existing import/transaction matching. Nonempty new sources require normal Upload; the action inserts no transactions. Empty verified sources get a zero-count history/balance record. Matching existing imports retain hash, ID, original time/count and every transaction/classification; only missing, source-proved metadata can be recorded. Conflicts reject atomically. Exact repeats are non-writing. PostgreSQL locks header/balance and matched source rows; existing writer fences remain active. No automatic startup/deployment repair.
- CNB: source-labelled zero-debit layouts, electronic credits and check-number-first activity are now consumed and validated strictly. All three original fixtures pass summary/count/sign/date/reconciliation, history, pending eligibility, rollback and duplicate tests. Existing CNB fixture remains passing.
- THIRD REPORT: section/button 5 follows Income/Charity section 4, including availability when the transaction report is empty. Four-column 10px nowrap list with escaped concise account labels, native closing, existing monthly-FX USD closing and one visible-row-reproducible USD sum. Missing FX and unverified/liability exclusions stay explicit but concise. Detailed warnings and partial-scope/export labels remain on the dedicated report; no new FX or latest-selection engine.
- Performance: only unreachable duplicate-preview lookup variants are omitted. Every required lookup retains the complete historical row set, original matching keys, exclusions and SPLIT handling. Independent preserved-live-code oracle proves identical output for mixed scopes/signs and every single-row case. All persistence, commit-time duplicate, fence and repair code remains unchanged. The new BOC action also omits a redundant broader-account read after its stricter exact-source proof.

## Executed final validation

17 new focused + 44 relevant integrated regressions + 20 summary/auth-routing tests = **81 unittest methods PASS, 0 FAIL**. Two standalone authenticated workflow and latest-balance/export regression scripts PASS. Total **83 checks PASS, 0 FAIL**; method counts do not inflate source subtests or individual assertions.

Commands use the private allowlisted child runner: python -B -m unittest -v (explicit IDs in final-focused-ids.txt, final-functional-regression-ids.txt, final-summary-regression-ids.txt), plus _qa_latest_import_balances.py and _qa_app_continuation_smoke.py. All Python sources parse; git diff --check passes. Parent application code was never run. Child credentials absent; external network, credential files, nested subprocesses and out-of-root SQLite are denied. No production SQL or test imports.

Protected AST/byte comparisons prove normal Upload, auth/routing, Setup, Pending, history, classification, SPLIT, financial fences, startup/backfills, reporting engine and extraction helpers unchanged, except the explicitly scoped preview lookup function, Citi dispatcher integration and THIRD presentation route. Source tests and import regressions execute against the actual worktree modules.

## Bounded measurements and limitations

Windows CPython 3.12; synthetic disposable SQLite with 2,000 rows; three repeated workflows; 20ms RSS sampling plus tracemalloc; 400 MiB post-operation guard and 180s child timeout. Instrumentation overhead and desktop scheduling affect timings; no hosted stress test or production financial rows.

Safra warm preview median: **19.02s -> 5.43s**; queries 12/13 -> 7/8, fetched rows 14,012 -> 4,012. One cold PDF open/four page-text extractions; subsequent warm previews opened/extracted none. 100-row synthetic actual UI import: **38.84s -> 25.14s**, one commit each, queries 225 -> 220, fetched rows 26,115 -> 16,115. Common-workflow sampled RSS peak **232.09 -> 210.73 MiB**; retained memory fluctuated rather than proving a leak. Setup/Pending timings did not consistently improve. Report export was about 0.07–0.09s in this small account fixture; no claim for a large hosted report.

Source-pinned BOC action comparison restores only the extra account read in the before variant: 10 -> 9 queries, 2,020 -> 14 fetched rows. Median timing 5.51 -> 6.30s: **no latency improvement claimed**. Two PDF opens remain deliberate re-verification; no validation/cache shortcut added.

Original failed profiling fixtures/logs retained: nonexistent description/incorrect usd_amount column, aggregate candidate timeout at 180s, and older private BOC helper selected by script-directory precedence. Invalid BOC metrics are excluded; corrected comparison pins current source and records its explicit before variant. Scope-test name lookup was corrected to preserve duplicate helper-definition order; no application change was made to pass that assertion. These failures are separate from final passing application QA.

The reported 7–10+ minute live delay, 429 cause and historical OOM cause remain unproven by these local measurements. No hosting upgrade or claim of production memory safety. Production platform/data/concurrency differ.

## Independent blocker

Comerica Line of Credit: original three pages have zero extractable text. The existing optional OCR wrapper is available locally, but native Tesseract is absent; the production build declares wrappers without a verified native OCR/source adapter. Production OCR capability remains unverified. No guessed LOC parser, transaction import or hard-coded balances added. Obtain a readable/searchable original or prove an existing supported OCR path against the original pages; retain statement-balances-only policy and principal distinction.

## Release and acceptance

Standing user authority permits ONE exact integrated deployment after gates. Exact repository NMelillos/AretiApp2; Render srv-d7aj84khg0os73b4g0i0, owner tea-d6mhq3p4tr6s738eegm0; main; Auto-Deploy OFF. Previous live/main confirmed at 13d7845; no competing deployment observed. Retained recovery deployment dep-db0j78mq1p3s73edtobg identifies f3ea9ce2d0143ef3239871bb94791ffb39fd857e. Operator previously confirmed Deploy a specific commit capability and accepted rebuild/dependency risks; no settings/env/credentials changed.

At this committed source snapshot: IMPLEMENTED YES; TESTED YES; DEPLOYED not yet requested; DATA-REPAIRED NO; USER-ACCEPTED pending. The exact source SHA and actual deployment/HTTP/startup outcome are recorded in the private deployment-receipt.json after rollout. Business UAT and any unavailable authenticated live check remain distinct from technical deployment success. No production upload or balance-confirmation action is performed by the agent.

Private evidence: C:/Users/Student/.codex/visualizations/2026/10/05/01a10a5f-345e-7a42-a6c2-036223abacf9/autonomous-20261008. Originals, source financial expectations, screenshots, exploratory failures, profiles and receipts remain outside Git. The source manifest freezes executable/test hashes; receipt records exact candidate and live SHA. Do not rerun historical repairs, weaken the unapproved full-candidate coverage guard or claim all master work complete.
