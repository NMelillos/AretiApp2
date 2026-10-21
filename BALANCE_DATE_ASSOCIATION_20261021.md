# Latest Import Balances — date association correction

## Baseline and boundaries

Verified 21 October 2026: NMelillos/AretiApp2, Areti service srv-d7aj84khg0os73b4g0i0, workspace tea-d6mhq3p4tr6s738eegm0. LIVE d82913432879c516890e70fa2b313a70fadfd42b, deployment dep-dbb1tcvf3r2c73fvf4tg. Auto-Deploy OFF, main. Baseline worktree clean and preserved. New branch codex/areti-balance-date-association is isolated from accepted work.

The 19 October grouping fix, accepted Income correction, report location and current-Setup-only partial population are protected. THIRD remains hidden. No production SQL, source import, data/schema/environment change or repair is part of this package. Changes to application code are confined to latest_import_balances.py and latest_balances_compact.py. Their callers are the existing report renderers in app.py; no import/persistence/classification/parser workflow calls these report helpers. All other application Python modules and requirements remain byte-identical to the verified baseline, tested explicitly.

## Reproduced report defects

1. The query omitted statement_balances.imported_at and updated_at; no Balance Import Date was exposed in compact, detailed HTML or Excel.
2. A newly uploaded older verified BOC source correctly does not displace a newer complete statement balance, but Transaction Import Date was selected independently as the account's latest transaction-bearing history. A disposable reproduction shows September balance data paired with the October 15 import of an August statement instead of the October 1 import of that September statement.
3. A source-verified overlap balance similarly used an unrelated newer account transaction event instead of the original imports referenced by its unchanged source transactions. Focused test failed with October 3 instead of October 1 before correction; original failure retained privately.

These are code/fixture reproductions, not claims about which individual live orange/red cells Areti marked. The browser is signed out. No current marked workbook or authenticated per-account fields were inspected; no statement is classified as never imported merely because a report field is missing. Existing incomplete-source, label/identity, stored precision, liability and missing-FX exclusions remain authoritative. No balance fallback, record repair or inferred financial value is introduced.

## Exact report mapping

Transaction Import Date: statement_imports.imported_at for the selected statement when it has real transaction rows and a positive count. For a source-verified balance-only overlap, follow its already validated distinct transaction ID references to their original statement_imports; show the latest contributing import timestamp under existing timezone/legacy ordering rules. Missing linked history produces — rather than an unrelated account event. A genuine zero-row/non-overlap balance retains the independently recorded account transaction event, preserving the prior no-new-transactions rule. Balance imports never rewrite this timestamp.

Balance Import Date: selected balance record's independently stored imported_at when no later unresolved update exists. For the explicit BOC completion action, use the JSON audit confirmed_at only with the exact action, actor, zero transaction_changes, matching statement hash and matching updated_at; this proves the event that persisted the current balance metadata. Generic updated_at alone cannot identify an import: db.update_statement_balance_rows changes financial/metadata fields, while account-label synchronization also writes that field. If current-value timing is unresolved, or a timestamp is absent/invalid, display — with a precise date-provenance reason in detailed print/Excel verification. Never substitute transaction time. Existing naive timestamps remain explicitly timezone-unverified.

Statement Closing Date: period_end on the same selected statement_balances row as the native balance. Selection is otherwise unchanged: completed import time/ID with disclosed legacy fallback; verified older BOC/CNB sources cannot displace a newer complete period; newer incomplete records stay visible, not silently replaced with old healthy balances. No new import-date ordering rule, chronology alarm or age threshold.

The compact view adds one requested column between Transaction Import Date and Statement Closing Date; full HTML/Excel append Balance Import Date so existing detailed column positions remain stable. Import dates stay black. Closing freshness remains inclusive 0–30 Cyprus calendar days; older/unknown/future dates red. All existing financial values, two-decimal presentation, sorting, precision guards, partial population, ownership safeguards and totals are unchanged. New date diagnostics are summarized once in the detailed Streamlit view, not emitted as individual large warning blocks; per-account reasons remain in exports.

## FX and preservation evidence

Use existing imported Setup rates exactly as before: existing rate type and statement closing month lookup, latest configured applicable month at/before the statement month (existing earliest fallback if none precedes it), existing USD identity and rounding. Do not reinterpret this as globally newest FX, call external rates, or change conversion policy. Existing unsafe/ambiguous/precision/liability/missing-rate exclusions and exact USD arithmetic remain unchanged. Tests compare every nondated financial field and total against the pinned production snapshot, verify storage/FX/alias-duplication guards, literal-safe exports and full local database snapshots before/after reads.

## Actual tests

Final application source: 44 integrated unittest methods PASS, zero failures/errors/skips; one additional bound-SELECT/missing-linked-history test PASS, plus the source-scope check repeated after final QA updates. Total 45 distinct passing methods on the same final application source. Existing SQLite report/storage/FX/total/alias integrity script PASS. Changed-source syntax, diff checks and environment isolation PASS. Production PostgreSQL is not connected; PostgreSQL query AST parsing is tested, not live PostgreSQL execution.

Coverage includes normal imports; later balance records; source-bound BOC completion through actual preserved source; unchanged classifications/review/history/memory; zero-activity source PDF; overlap source/history association; older source selection; bank/currency boundaries; missing/invalid/naive/audited/generic timestamps; no false chronology alarm; null transaction counts; repeated balance-only idempotence; conflicts/rollback; print/Excel/compact headers and dates; no grouping; freshness; FX/precision/exclusion/total guards; own Reports/return/auth/error/standalone and THIRD hiding.

An old integrity assertion counted account identifiers across data and warnings. New date-provenance diagnostics add a legitimate third mention. Original failure retained; corrected QA asserts exactly one financial table row and the exact ambiguity and date diagnostics separately. No application safeguard changed to satisfy that test. Earlier 42-method intermediate pass and expected overlap failure remain private evidence, not substituted for final-source validation.

## Monthly graph and live acceptance

NOT IMPLEMENTED — REQUIREMENT/DATA SEMANTICS NEED CONFIRMATION. No established definition chooses latest closing balance within a month, latest import during a month, or latest known state at month-end. Generic in-place metadata edits are not complete historical value snapshots, and current Setup is not a versioned historical account population. Never use today's balance as a historical substitute. No graph, new calculation or schema work included.

The task authorizes an exact tested report-only deployment with Auto-Deploy OFF. Record actual release outcome separately; this preparation document is not deployment evidence. Technical root/health/log checks do not prove authenticated financial correctness. Areti UAT must verify the previously marked account rows, three date concepts, remaining missing values with their actual Status/Verification/Applied FX, and output formatting. No real statement may be imported merely for technical smoke verification. Per-account red/orange classification remains unverified until that evidence is available.
