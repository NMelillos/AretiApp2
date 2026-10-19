# Latest Import Balances finalisation — 19 October 2026

## Verified baseline and isolated scope

Production: bed3e3506909ddb95284a9c642bd6426b03d6a55, LIVE deployment dep-dbb14ck9v7es73aac0k0 on the authorised Areti service srv-d7aj84khg0os73b4g0i0, workspace tea-d6mhq3p4tr6s738eegm0, repository NMelillos/AretiApp2. Auto-Deploy OFF; root 200 and health 200/ok checked read-only. Local baseline worktree remains clean and unchanged. New work is isolated on codex/areti-balances-finalisation.

Latest report-related production commit is 127599fec5c4ecc9028ab155f9abfcd0a0a5ad25. The later Income correction is accepted by Areti and is preserved. Commit 2f243996fab8ce3f306ea95baafe4ced6c6248c8 explicitly superseded 40-day freshness with inclusive 0–30 Cyprus calendar days and compact sorting by Bank, Account Name, Statement Closing Date ascending (unknown dates last). No threshold, sorting, financial or FX policy change in this package.

## Already implemented; preserve

The eight-column compact report stays in Areti Reports, with the standalone detailed view and print/Excel still available; it stays hidden from THIRD REPORT. Compact rows are ungrouped, content-sized, and financial display uses two decimals. Independent transaction timestamps require genuine transaction-bearing history and matching original rows. Balance-only timestamps cannot supply or refresh them. Closing-date freshness is independent. Existing source-verified BOC/CNB older-statement protection, incomplete/precision/ownership/liability exclusions, approved monthly FX lookup and deterministic partial totals remain unchanged.

Ordering is committed import timestamp plus ID, or disclosed legacy ID fallback when timezone/ordering is unverified. An older verified BOC/CNB source cannot displace a newer complete period. A newer incomplete record remains visible and ineligible; no old healthy balance is silently substituted. This is an intentional existing safeguard, not newly classified as a defect.

No universal authoritative source-activity/no-new-transactions flag exists for every supported bank layout. A zero history count alone is insufficient. Therefore no speculative chronology warning is added; genuine incomplete/ambiguous/precision/FX warnings remain available.

## Demonstrated presentation defect and minimal correction

The compact view was already ungrouped, but detailed HTML/print inserted an extra bank/account heading row before each account. Remove only that heading insertion and its unused CSS. Keep all detailed columns, ordering, values, precision formatting, totals, exclusion disclosures, dates, links and workbook implementation unchanged.

## Actual validation

Baseline: 35 targeted unittest methods PASS, zero failures/errors/skips, plus SQLite storage/FX/total integrity and latest-selection integrity scripts PASS. The baseline observation specifically reproduces grouped detailed print and current-Setup-only population without changing historical financial rows.

Changed candidate: 37 targeted unittest methods PASS, zero failures/errors/skips, including two new detailed-print no-grouping/one-row/repeated-render tests. Both existing SQLite integrity scripts PASS on the changed candidate. Changed-source syntax and diff checks PASS. The only application diff is inside print_document and its unused grouping CSS; all parsers, financial/storage/selection/rate logic, app.py, classification, Pending, SPLIT, and compact UI source are unchanged from the verified baseline.

Coverage: transaction-only and later balance-only timestamps; missing transaction timestamp; actual source-bound balance completion with reviewed classifications, transaction/history/memory preservation; idempotence; conflict and forced rollback rejection; verified duplicate overlap; real synthetic strict zero-activity PDF; older BOC statement protection; latest incomplete visibility; exact native/FX totals; missing/unsafe rates; alias ambiguity; known/unknown currency precision; HTML escaping/Excel literal safety; independent 30-day boundaries; authenticated disposable Reports/Return/standalone and THIRD hiding.

Tests run under Python 3.12 with the final pglast 7.20 runtime. Child environment excludes production credentials; safety test enforces external-network, credential-file, nested-subprocess and outside-root SQLite rejection. All test writes are disposable local SQLite. PostgreSQL SELECT parsing is checked, but no PostgreSQL runtime execution is claimed. Existing source-bound BOC original is preserved privately; unavailable CNB/Citi original PDFs were not rerun or claimed passing. No unchanged broad parser or performance suite was run.

## Report population decision — explicitly resolved by George

The previously approved INTERIM PARTIAL REPORT starts from current account_list (Setup) and explicitly says absent Setup accounts are outside scope. A disposable reproduction removes a Setup row and proves its transaction, balance and import history remain unchanged while the report omits it. George explicitly resolved the apparent conflict in the new instruction: preserve current partial scope; historical records remain preserved in the database, and this pass must not expand the report beyond current Setup accounts. No population, ownership, FX or total-policy change is required. The existing partial-scope labels must remain visible.

## Release boundary

At preparation this is a local presentation candidate, not evidence of deployment. The attached task authorizes exact tested deployment after checks; record actual release outcome separately rather than infer it from this source document. No production SQL, import, repair, schema/environment/configuration change, or historical rewrite is needed. The last explicitly recorded recovery target c68b397a7b81f4098adfa68b055408c4b5995cea was not re-executed or revalidated in this task. The immediate verified production predecessor is bed3e3506909ddb95284a9c642bd6426b03d6a55. New authenticated live business verification is not claimed. Areti has already accepted the report location and the Income correction; do not ask to reaccept them.

The scope confirmation changes no executable source or expected test outcome and does not require repeating unchanged tests. Live account/FX totals and safe balance-only UAT remain to be checked by Areti without importing financial records merely for a smoke test.
