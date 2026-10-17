# Balance dates and temporary THIRD REPORT visibility

Baseline: Render live commit 7bac13a968c243f8e91c28f8020d5bbe4c5b882a, verified read-only on 17 October 2026. Work is isolated on codex/areti-balance-date-validation. Other local Areti branches are not the live baseline and are preserved.

## Independent dates

| Date | Current source and meaning |
|---|---|
| Statement Closing Date | `statement_balances.period_end`: source statement period end, not upload time. Existing Cyprus calendar freshness is green at 0–30 days, otherwise red, including unknown/future dates. |
| Transaction Import Date | Latest qualifying `statement_imports.imported_at` for the exact existing bank/account/currency identity, with positive transaction count and actual matching original transaction rows. No qualifying event or absent timestamp displays —. Balance-only history never supplies this date. It stays black. |
| Balance Import Date | `statement_balances.imported_at`: first persistence of that balance record. Upsert preserves it; subsequent balance metadata updates use `updated_at`. These are independent of transaction history. No new balance-date column is introduced. |
| General Report Date | Setup `report_until`, read by `get_configured_report_until`: transaction-date inclusion cutoff for expense reports. Existing fallback is supplied by the calling report. It is not a commit timestamp. |
| Report generation time | Latest Import Balances HTML “As of” and workbook “Generated”: time the current snapshot/export is rendered. Existing closing freshness uses this Cyprus calendar date, not the expense-report cutoff. |

## Valid older transaction dates and evidence limits

A transaction import timestamp before a later statement's closing date is not inherently anomalous. Example: July transactions already imported; a later September zero-activity statement updates only balance metadata. September closing freshness and the July transaction import date describe different events. Likewise, all source movements may already have been imported, so a balance-only action can add zero new rows without zero source activity.

The strict BOC source parser can prove zero source activity using validated sections, dates, labelled amount columns, totals, source movements and `zero_activity_validated`. Atomic zero-row commits require equal finite opening/closing balances and zero known flows. BOC overlap imports instead preserve exact stored-row references. `boc_summary_balances.record` verifies original row identities/counts/amounts before completing missing metadata, preserving transaction history. A zero history count alone, an old date, equal balances alone or an absent history entry cannot establish that no source activity exists.

The report does not persist/reconstruct an authoritative per-statement “no new transactions” diagnosis for all layouts. Therefore this package adds no red/green transaction-age comparison or speculative missing-import warning. Existing actual incomplete/ambiguous/legacy-ordering/FX warnings and all eligibility protections remain unchanged. No financial values, source facts or dates are inferred. The explanatory note is shared by the full UI, print and Excel verification sheet.

## Visibility-only change

Remove the `third_latest_import_balances` button on all THIRD REPORT data branches and ignore the old `third_latest_balances_open` route so an existing session cannot reopen it. Expense report calculations, authentication and contents otherwise remain identical. Preserve the normal authenticated Reports `reports_latest_import_balances` button/compact view/return, standalone Latest Import Balances navigation, exports and underlying report modules.

## Safety boundary

No database connection to production, writes, migration, data repair, history rewrite, parser changes or deployment. Targeted tests run in a credential-free, network-blocked child with disposable local SQLite. Application module equality against the verified live tree protects all unchanged import, account, financial, SPLIT, Pending, classification and bank parser code.
