# Latest clarification — complete Grand Total coverage and September Sapphire

Preserved parent candidate: f164e000fb0c7322b716b454b23e496c7a06e475.
Production/main baseline remains98356258e779d99a5d8b87852060f34bad828dd8.
This isolated delta does not reopen accepted BOC/Citi/LOC placement/alignment,
modify Transaction Memory or implement the requested graph.

## New evidence and bounded changes

The latest requirement explicitly prohibits presenting partial section sums as
a final Grand Total. The read-only43-row current Setup snapshot has five NO IMPORT
rows without eligible USD balances. All four approved Debts are represented after
the parent fixes, but current Setup coverage is not complete. Add a single shared
grand_total guard using the existing eligibility rules. Return no final value
unless every current Setup row is eligible. UI, print and Excel show NOT AVAILABLE
and the coverage reason. Partial section sums remain visible; they are not silently
renamed or used as a final total. No missing value/rate is invented. Current Setup
scope, row selection, signed section arithmetic, sorting, FX and freshness stay.

The exact earlier Sapphire source was found by its actual stored filename, not
assumed from the October filename. Its SHA256 matches the existing historical
statement hash. The original financial summary and activity pages were visually
reviewed. Independently verify the earlier liability closing value and the later
October original; exact original transaction sets remain9 and10 respectively.
September's source transaction Counter matches the nine unchanged stored rows
read in an enforced READ ONLY transaction.

The September source explicitly has zero interest and omits the interest-total
section. The parent validator unnecessarily required that section even for zero.
Permit its absence only for an explicit zero summary with no dated interest or
interest-activity heading. Positive interest still requires its exact detail total;
conflicting coupon/summary/activity rejects. The printed fee remains an existing
transaction, not a new invented row. Recognize the old purchase-only money_out
field only when equal to the exact source Purchases and all other identity/summary/
stored transaction checks pass. Complete legacy metadata remains a no-op; do not
rewrite its field semantics, financial rows, timestamps or Import History.
New missing metadata includes all source charges and reconciles exactly.

One prior test expected the actual partial sum to be displayed as Grand Total.
Update only that expectation for the explicit new business requirement. Its row,
column, alignment and section-total assertions remain. All other existing tests
and unrelated application definitions remain protected.

## Executed evidence

- Six new requirement/source cases PASS; four affected existing Sapphire safety/UI
  regressions PASS; one explicitly updated Grand Total test PASS:11 unique cases.
- Initial paired-source test attempted to seed the same Setup account twice. The
  unchanged database UNIQUE safeguard correctly rejected it. Reuse that same
  verified account in the test fixture; only the affected case was rerun. Original
  failure/counts retained. No application safeguard changed to satisfy this error.
- Exact September source: identity, period, fee/payment/purchase signs, source
  summary reconciliation, stored nine-row comparison, complete legacy no-op,
  missing metadata completion, repeat idempotence and rollback PASS. October's
  independent original and existing safety checks remain PASS.
- Disposable PostgreSQL18/fence: September original, nine transactions unchanged,
  history/date preservation, legacy no-op, missing metadata completion, rollback,
  concurrent/repeat idempotence, source reconciliation and signed report PASS.
  Disposable DB dropped; owned loopback cluster stopped.
- Actual43-row report replay: individual balances/USD values, dates, rows and
  section totals identical to the preserved parent. Final Grand Total withheld.
  Complete synthetic coverage verifies signed addition exactly once, including
  zero/credit balances; missing FX/unknown/ambiguous/empty coverage rejects.
- Local browser checks: both aligned views retain43 rows and show NOT AVAILABLE
  under the final USD column with an explicit coverage explanation. Layout CSS,
  headings and widths are unchanged from the parent's visual checks. Private HTML
  output explicitly uses UTF-8; a temporary local-fixture encoding artifact was
  corrected without changing application code or financial test expectations.
- Syntax198 Python files and diff/protection PASS.56 other application files and
  32 existing definitions protected. No repeated unrelated full legacy QA run.

Private evidence: sibling qa-balance-coverage-20261111 and reused original source
evidence under qa-balance-sapphire-20261111. Never commit originals, financial
snapshots, account/card evidence, screenshots or credentials.

## State and exact remaining blocker

IMPLEMENTED/TESTED: guarded final total and independently verified September
source support; inherited report/Credit Line/Sapphire fixes preserved.
FINAL GRAND TOTAL: BLOCKED by five current Setup rows without eligible USD balances
in the verified read-only snapshot. Required: source-backed balance coverage or an
explicit approved Grand Total scope exclusion. Do not infer zero or change Setup.
Do not release the parent's partial combined sum as a final Grand Total.

DEPLOYED:NO. DATA-REPAIRED:NONE. No production financial writes/imports, historical
balance/date/transaction/classification/review/SPLIT/history/account changes,
schema migration, environment or configuration change. No Render action.
No deployment during12:20-14:00 Cyprus; preserve Areti's finish confirmation gate.
Transaction Memory: AWAITING REAL USER VERIFICATION — NO CHANGE.
Balance Trend Graph: NEW FEATURE REQUEST — NOT PART OF CURRENT FIX.
