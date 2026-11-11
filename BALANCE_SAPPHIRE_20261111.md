# Remaining balance-report and Sapphire UAT issues — 11 November 2026

Baseline: 98356258e779d99a5d8b87852060f34bad828dd8, verified LIVE on
srv-d7aj84khg0os73b4g0i0 / dep-dbpmbmmtlv9c73do7kk0. Separate branch:
codex/areti-balance-sapphire-20261111. Preserve the previous candidate/worktree.

## Business control baseline

Areti's 11 November email confirms PASS for both current BOC cases, Citi signs,
the exact Credit Line's Debts placement and report alignment. They are closed.
Only compactness, missing Credit Line USD, total placement/Grand Total and the
two Sapphire balance issues are in this package. No further classification or
memory changes. No graph implementation. Current Setup-only partial scope stays.

Operational restriction: no deployment or production changes between 12:20 and
14:00 Cyprus time; respect Areti's message that she will confirm when finished.
Do not perform production financial test imports or historical rewrites.

## Proven causes and minimal corrections

1. Long identity/verification labels expanded both aligned tables. Bound only
   those text columns, retaining full values in tooltips and leaving monetary
   widths content-sized. Keep headers wrapped, reduce section-heading spacing.
   Both tables retain identical colgroups. No selection/sorting/FX redesign.

2. The approved Flexline operation intentionally records only Statement Date and
   New Balance. The generic report completeness check also demanded opening and
   period-start values, so it excluded its USD equivalent. Add a narrow read-only
   proof for the existing source/audit contract, exact bank/owner/combined account,
   source/Setup identity, negative finite USD closing value, source date/hash,
   authenticated audit, zero history/source transactions and decimal precision.
   Show CLOSING ONLY / VERIFIED CLOSING METADATA ONLY. Opening/activity/period start
   remain unknown. No metadata is fabricated, completed or rewritten in production.
   Unproved/inexact/mismatched records remain excluded. The debt whitelist and
   classification function are unchanged. USD uses existing identity conversion.

3. Full HTML/print totals were paragraphs outside the table; Excel section totals
   were in the second column. Put totals under the actual USD-equivalent column.
   Excel labels span the preceding columns without covering the numeric total.

4. Add one Grand Total from the two eligible signed USD section totals, once.
   Do not subtract already-negative debts again. Keep the independent section
   totals and all partial-scope/exclusion qualifications. UI/print/Excel agree.

5. Sapphire transactions already have the accepted signs. The generic balance
   extractor stores the printed creditor convention. Correct only this approved
   account's report projection: negate raw printed opening/closing values, not
   abs(), so a source credit balance remains a credit. Exact bank/owner/account
   token, source/Setup account and USD checks restrict that projection. No stored
   historical value or date changes. No Citi convention is borrowed. Transaction
   parsing is byte-identical to baseline. Original financial facts remain private.

6. The separate balance-only action did not support Sapphire. Add a source-bound
   metadata adapter: strict brand, full payment-coupon account plus masked account,
   Setup owner/currency, period, complete labelled summary, repeated coupon total,
   detailed interest total, exact source transaction/date/sign totals and exact
   existing stored rows/history. No manual-selection-only account binding.
   The source's documented interest is reconciled as metadata; no transaction is
   invented or inserted. The original ten parsed transaction rows remain identical.
   Existing complete source metadata returns a truthful no-op: current signs are
   corrected only in the report, without rewriting balances/timestamps/history.
   Missing fields may be completed through the authenticated explicit operation,
   with source audit and writer-fence/concurrency protection. Conflicting existing
   values, changed transactions, wrong accounts or missing transaction history
   reject; no historical correction or duplicate reimport is enabled.

## Executed validation

- Final focused tests10/10 PASS; affected date/identity/section regressions17/17
  PASS:27 unique cases, zero failures/errors/skips. No broad legacy QA rerun.
- Exact four-page Sapphire original: financial summary/activity visually reviewed;
  full account coupon proved; accepted ten-row transaction output unchanged.
- The new source contract was reviewed for mismatched coupon/interest details;
  that reproduced gap was closed in its second/final bounded attempt. Original
  and adversarial evidence are retained. No arbitrary exception acceptance.
- Read-only production evidence replayed into disposable SQLite:43 current Setup
  rows unchanged in population/order. Only approved LOC USD/status/proof and exact
  Sapphire balance presentation fields change. All41 other rows and the Deposits
  total remain identical; the new Debts and signed Grand Total reconcile exactly.
  Actual figures are kept in private evidence, not this repository.
- Local browser: full table2194->1650px; compact1298->1004px. Both sections remain
  aligned, section/Grand totals sit under USD, all43 rows remain. Screens reviewed.
  The first sandbox-only Edge token/GPU failure remains recorded; the isolated
  local-file check passed outside that sandbox, with external DNS blocked.
- Disposable PostgreSQL18: exact source, NUMERIC amounts, writer fence, complete
  historical no-op, missing metadata completion, rollback, concurrent idempotence,
  unchanged transactions/history/classifications/learning and signed report PASS.
  The disposable database was dropped and owned test cluster stopped.
- Syntax197 Python files PASS.54 other application files unchanged, including
  parsing, BOC/Citi/CNB/Safra, db/storage, classification/memory, SPLIT, import
  history and all routing/THIRD/Family code.37 existing definitions checked;
  unrelated date-provenance branches protected. Existing tests unchanged.

Private evidence: sibling qa-balance-sapphire-20261111. It contains originals,
source expectations, read-only snapshots, exact-money replay, browser artifacts,
raw failures, final tests and PostgreSQL/source-protection results. Do not commit
those files. Account matching remains exact without publishing the card number.

## Deferred and release state

Transaction Memory: AWAITING REAL USER VERIFICATION — NO FURTHER CHANGE.
Balance Trend Graph: NEW FEATURE REQUEST — NOT INCLUDED IN CURRENT FIX.
Before future graph implementation define source, time grain, opening vs closing,
historical coverage, missing months, USD conversion, signed debts and Total line.

IMPLEMENTED and TESTED locally as above. USER-ACCEPTED: pending current-item UAT.
DEPLOYED:NO in this task. DATA-REPAIRED:NONE. No production imports/writes, financial
record edits, date rewrites, account mapping/Setup/schema/environment/configuration
change. Any controlled release must use the exact tested commit and preserve
Auto-Deploy OFF and the operational restriction. Do not retest closed business
items or implement the deferred graph/multi-currency features as part of release.
