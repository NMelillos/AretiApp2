# Independent balance-only completion after duplicate upload

Verified live baseline 35516c4c54c454a383b71d4e686786a2aded4af9 / dep-dbc3rf3rjlhs73ao19b0; NMelillos/AretiApp2, srv-d7aj84khg0os73b4g0i0, tea-d6mhq3p4tr6s738eegm0. Auto-Deploy OFF. Separate codex/areti-urgent-import-family worktree.

## Proven origin

The latest release did not change app.py or Statement Summary. Import's exact-file duplicate stop originates in 9e934d32 (15 May); its explanatory block is from 8247ce58 (16 July). Safra's special read-only duplicate preview is ec58e2ed (23 September), preserved. c68b397 (8 October) introduced a separate BOC balance-only button in Statement Summary; it remains present. Reviewed Git -S/-G history finds no removed generic Revolut balance-only button. Therefore no first removal commit or prior generic-Revolut last-known-good is claimed. The demonstrated failure is the long-standing early duplicate stop plus BOC-only scope of the independent summary action, not a button deletion by the latest report-only release.

## Bounded correction

Import now calls one independent balance control before its existing duplicate stop. File hash binds preview/confirmation/result state; changing file clears it. No extraction until Prepare is pressed. Backend reparses/revalidates before any write. Transaction import remains unavailable on duplicates. BOC delegates to the unchanged source-bound existing action. The supplied preserved BProjects localized Revolut source uses unchanged strict parser/IBAN/currency/count/sign/reconciliation validation. Exactly one matching current Setup account required.

For Revolut, lock the existing import and balance, compare exact source transaction date/amount/description/bank/account/currency multiset and original count, preserve split parents/children, history and classifications. Insert missing balance or fill only source-proved missing metadata; conflicts reject. Append actor/hash/time/zero-transaction-change audit in balance notes. Repeat complete records return already recorded without writes. Nonempty unimported sources cannot bypass transaction import; source-validated zero activity may create one zero-count history/balance and no transactions. All writes use the existing database connection/fence mechanism, atomic commit/rollback; no schema addition or repair controller.

Other unsupported layouts fail this separate action explicitly, without changing their normal parser/import path. This does not add unverified new bank formats. Original BProjects PDF transaction amounts, account identity and independently established source summary expectations are retained in private evidence only.

Direct report dependency: the existing Balance Import Date helper recognizes the new source/hash/actor-bound Revolut completion audit using the same timestamp guards as BOC. Other report functions, balance selection, FX, totals, dates, compact presentation and guards are unchanged. Exact source-scope tests enforce all other application modules and all app.py text outside the two-line entry point unchanged at this first commit.

## Actual verification

20 distinct targeted methods PASS on this application source: 19 passed in the initial integrated run; the remaining UI method initially failed because its new test helper collided with inherited BytesIO fixture name, then passed after a harness-only rename. Original failure retained privately. Actual Streamlit test proves baseline duplicate page has no control, corrected page has it, normal transaction duplicate button stays blocked, confirmed source completion adds one balance, all other tables stay equal, repeat is a full no-op. Other tests cover original eight-row source, partial completion/report dates, conflicting amounts, transaction mutation, missing header, wrong currency, unsupported source, actor guard, forced rollback/retry, strict synthetic zero activity, original BOC completion, unchanged English/localized Revolut parsers and report date/print/Excel regressions. Only disposable SQLite writes, sanitized environment and external-network blocking. No production import or write test.

Final integrated verification supersedes the initial harness run: 30/30 methods PASS, zero failures/errors/skips, comprising 21 balance/report/scope checks and nine Family hierarchy checks. Setup identity/currency is rechecked under the atomic transaction lock; a stale prepared context rejects without writes. Original failed harness logs remain private. Final log: final-guarded-integrated.log and final-guarded-test-counts.json in the private urgent-import-family evidence folder. No PostgreSQL write-runtime rehearsal is claimed.

This preparation entry is not deployment proof; retain exact release receipt separately. No production records were changed. Authenticated controlled UAT remains necessary for Areti's current file.
