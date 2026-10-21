# Urgent Family reconciliation and balance-only release

Verified baseline: 35516c4c54c454a383b71d4e686786a2aded4af9, exact Areti repository/service only. Independent balance action commit afb66005d407ddc735462a87d728f25d120a8b88; separate approved Family hierarchy correction follows it. Preserve all prior worktrees/candidates.

## Family cause and approved rule

ace402e0 (31 August) introduced THIRD's Item19 exclusion; its parent 0bc961a0b9d85d8507fc6747c1bb87a29c8d7056 predates that policy. Later 078e52ee extracted the helper and 9a12e21d retained mapped Income while continuing to exclude Charity. Areti's executive hierarchy includes current Setup-assigned Charity. This predates the latest report/date release and has no common cause with Import's early duplicate stop.

On 21 October the user explicitly approved: Include assigned Charity in both Family hierarchies. Localize the exception to app.py::_third_report_group_scope for Charity assigned to Family expenses or legacy 1-family, in transaction rows and Setup category options. Separate Income/Charity analysis and exclusions for all other groups remain unchanged. The reviewed-only/filterable Sample report is a distinct scope and is not forced equal to executive all-active reporting.

## Evidence and exact scope

One enforced REPEATABLE READ READ ONLY production snapshot (transaction_read_only=on) supports private transaction-level before/after reconciliation. Current Setup mapping, shared active/SPLIT/default-hidden-ID policy, configured cutoff and signed YTD calculation applied identically. Prior-year source rows are excluded by the actual report period. After approved rule: identical qualifying ID sets and signed totals, zero unexplained difference. Source rows, IDs, financial amounts and classifications stay outside Git in family-transaction-reconciliation-private.json and family-reconciliation-summary-private.json. No financial rows were changed. A Render hidden-ID environment override and live browser session filters/cache were not independently observed; browser equality remains UAT, not a claimed observation.

## Final tested application source

30/30 targeted methods PASS, zero failures/errors/skips: 21 balance/report/scope and nine Family tests. Exact final log final-guarded-integrated.log / final-guarded-test-counts.json in private urgent-import-family-20261021 evidence. Covers actual duplicate Import UI/confirmation, original localized Revolut source, missing/partial balances, complete no-op, conflict/mutation/currency/actor/stale Setup rejection, atomic rollback/retry, strict zero-activity synthetic source, existing BOC/English Revolut/localized Revolut, report date/export regressions; Family classification editing through actual disposable backend, SPLIT, review states, mapping/NULL/cutoff/sign rules, preserved Income/Charity and exact snapshot reconciliation. Scope tests prove unchanged application modules outside the two app.py paths and the single Balance Import Date audit helper. No parser, FX, financial, classification/storage module or schema change.

Tests run in credential-free network-blocked children with disposable SQLite only; PostgreSQL runtime write rehearsal not claimed. Original fixture/isolation logs retained privately. No production import/test edit/repair/backfill or schema/environment/configuration change. Deployment is authorised only for this proven package, Auto-Deploy OFF; exact final receipt must establish deployed SHA, deployment ID and HTTP/health/log result. Areti must confirm source-bound balance-only completion on her own file and matching Family scope after refresh. No automatic production test import is authorised.
