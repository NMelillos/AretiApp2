# Current Areti issues — 10 November 2026

Baseline: ba75caab3b259e3bfed8625bb5bac5b31f219e49, verified GitHub main
and Render LIVE (dep-dbg2smivcj2c73b0g720). Separate local branch:
codex/areti-current-issues-20261110. No push or deployment in this task.
Exact service srv-d7aj84khg0os73b4g0i0, workspace tea-d6mhq3p4tr6s738eegm0;
direct service readback confirms Auto-Deploy OFF. No settings changed.

## Evidence and bounded changes

Original current BOC sources ending 4626 and 1557 independently fail on their
numbered, same-account, zero-interest final annex after a fully closed activity
table. Both source periods end on 30 October, regardless of filename date.
Both contain 14 transactions. A narrow annex recognizer retains page/account,
IBAN, date, column, source-total and running reconciliation checks. It requires
the exact zero-interest body and exactly its two zero amounts in both text and
word geometry. Final review reproduced an extra undated amount outside the body;
the second/final remediation closes that gap and tests text/geometry attacks.
No other BOC branch is altered. Both exact originals complete the existing UI,
history, Pending Review and persistence paths locally; repeat/rollback pass.

The current Citi original contains a source-labelled STOP PAY REAPPLY cheque
credit adjustment. The generic path made it an outflow. A dedicated, reconciled
source contract converts that adjustment to a positive refund, retains the
negative purchase, and uses signed liability opening/closing values. It verifies
the actual labelled source movement, unique account/period, complete summary,
all row dates and all activity before persistence. The older issued-refund-cheque
branch is unchanged and its original September source tests pass. No historical
Citi rows are changed. The current source also passes the existing Import UI.

Transaction memory exists. The observed failures arise from first-three-word
merchant signatures and a weak fallback dropping saved subcategories, rather
than proven lost learning or an AI request failure. New read-only indexes use
specific stored descriptions/subcategory evidence with frequency/confidence
and ambiguity rejection. Existing exact matching, normalization, AI and learning
writes are unchanged. An explicit current Andreas Poupas Driver suggestion uses
the existing learned Transportation/Driver pair; it creates no category and
does not overwrite reviewed/history rows. Eight supplied examples are checked.
REPLIT retains the existing historically selected parent (including unusual
saved classifications); no taxonomy correction or historical rewrite occurs.
Transfer-fee and incoming-shareholder rules already worked and are unchanged.

The exact Woking Way combined former/current LOC identifier is absent from the
approved Debts whitelist. Add only that bank/owner/full combined identifier.
Do not move other accounts or change balances. Its existing incomplete/unsafe
USD eligibility remains excluded: moving the row does not change current totals
or make an unverified balance eligible.

Two independent content-sized HTML tables caused Deposits/Debts column drift.
Screen/compact presentation now uses identical content-sized colgroups across
both tables. All financial cells, rows, sorting, dates, FX, eligibility and totals
remain unchanged. Print/Excel generation is unchanged. Browser geometry and
before/after cell equality are checked with synthetic data; screenshots reviewed.

## Executed QA and qualifications

- New targeted cases: 13/13 PASS (BOC4, Citi4, memory3, report2).
- Current final BOC source/layout regression: 15/15 PASS, including both current
  originals, three historical September sources (143 rows), known-good 4441/8290,
  header/column/identity/truncation/adversarial validation.
- Related combined regression: 54 unique unittest cases assessed across bounded
  runs: 53 PASS, one pre-existing UI assertion FAIL, zero unresolved source errors.
  Tests cover date association, report section rules, Pending Review, classification
  save rollback/audit, SPLIT identity/allocation guards and duplicate preservation.
  This is an aggregate, not a claim of a single green full legacy suite.
- Existing merchant/subcategory regression script: four assertions PASS.
- Disposable PostgreSQL18, final source: all three exact current statements PASS
  with NUMERIC amounts, installed writer fence, history/Pending counts, exact USD,
  source reconciliation, forced rollback, duplicate idempotence, reviewed seed,
  import history and learning preservation. Disposable database dropped; owned
  loopback cluster stopped. No production connection in test subprocesses.
- Syntax: 195 Python files PASS. Diff checks PASS. 52 other application files
  unchanged; 51 existing functions checked and unaffected branches AST-protected.
- Before/after browser: both report views aligned, all cell text unchanged.

Legacy qualification: _qa_boc_package.SourceTests.
test_upload_atomic_history_pending_latest_balances_duplicates finishes its
financial/UI/navigation/export/preservation checks but expects the partial-scope
notice in app.info. That widget assertion fails identically on baseline ba75.
The approved current presentation uses different notice placement. Neither the
assertion nor production behavior was changed to make it green. Original raw
fixture failures and reruns remain private. Missing old download paths were
resolved with exact original email attachments, not fabricated substitutes.

Private evidence is outside Git under sibling qa-current-issues-20261110:
originals, source hashes, reviewed screenshots/pages, expected rows, read-only
learning snapshot, raw failures, baseline comparison, isolated runner, PostgreSQL
and browser measurements, source-protection manifest. No statement, screenshot,
credential or transaction snapshot is committed. Targeted tests require that
private evidence and the established isolated dependency environment.

## Status and safety

IMPLEMENTED: the six scoped current fixes only.
TESTED: targeted and affected checks above; full legacy QA is not claimed green.
DEPLOYED: NO. DATA-REPAIRED: NONE. USER-ACCEPTED: pending controlled live UAT.
No production financial write/import, historical transaction/balance/date/history
rewrite, classification/review/SPLIT edit, account remap, Setup change, schema,
environment/configuration change or repair. Normal financial storage, transaction
hashes, duplicate logic, parser dispatch, unrelated parsers and report calculations
are byte-protected. Closed Safra/CNB/NOMAD/Family functionality is not reopened.

Separate release verification/authorization is required before deployment. Areti
should test only the two exact current BOC sources, current Citi signs, the eight
suggestion examples, exact LOC in Debts and column alignment. Existing historical
records remain unchanged. The LOC's existing incomplete-balance eligibility is
not repaired or weakened in this presentation task.
