# Local Report Corrections and Handoff

## Safety and baseline

- Starting worktree: `E:\AretiCodex\income-group-preservation`.
- Starting branch: `codex/income-group-preservation`, clean.
- Starting HEAD and verified remote main: `9a12e21d73e9e414c561aea74cf176de5e2be6b3`.
- GitHub deployment 6408709722 reports Render success for that SHA. This is deployment-status evidence, not a production-database audit.
- Isolated branch: `codex/income-report-safe-fixes-20260914`.
- Isolated worktree: `E:\AretiCodex\income-report-safe-fixes-20260914`.
- Initial free space: C: 27.69 GiB; E: Elements 3056.07 GiB. No dependencies installed.
- Interpreter: `E:\AretiCodex\_runtime\income-charity-edit\qa-venv\Scripts\python.exe`, with `-B`.
- Verified versions: Python 3.12.2, pandas 3.0.5, Streamlit 1.61.1.
- Database URL variables and optional real-statement evidence variables were removed from suite processes. Temporary databases, shared-folder fallback, logs and caches were directed to the E: runtime directory.
- Only synthetic test databases were used. No production records, statements, credentials, schema, configuration or deployments were accessed or changed. Public deployment metadata and the logged-out application entry page were inspected.

Historical commits exist and are ancestors of the deployed baseline:

- `078e52eef214113d0067f48decdd98fda5c5174f`: Woking/THIRD correction.
- `5fdd829f7a23c55b77366374344c6a3169be5d46`: hierarchy presentation. Its existence did not prove the requested formatting was complete.

## Verified local corrections

### Income membership

Commit: `c8a2c54d9488a3068ce7ee138adf8ba282c4ecf6`.

`reporting.is_income` previously examined Category and Subcategory only. Reporting-Group-only Income was omitted. The centralized predicate now applies a case-insensitive whole-word match to Category OR Subcategory OR Reporting Group. It does not search descriptions or change taxonomy assignments.

The new regression failed before the correction. After the correction, a synthetic edit from `Income / Original` to `Projects / MISSING`, retaining Reporting Group `Income`, persists through a fresh SQLite connection. Exactly one transaction remains in Income, its amount remains 125, and the Projects hierarchy node is present. Financial and identity fields are unchanged; the existing reviewed-at timestamp behavior is preserved.

Blank, null, MISSING, mixed-case and whitespace cases are covered. Incoming, incomeable and incomes do not qualify without a valid Income match in another classification field. Multiple qualifying fields still produce one row. Removing the last Income classification from an input frame excludes it under the existing rule. The Income editor continues to reject cross-Reporting-Group edits.

No refresh or cache implementation was changed: the existing Save path clears transaction-read caches and reruns. Existing editor tests cover Save, Cancel, stale rows, protected fields and atomic failure. The browser fixture is read-only; no browser Save was performed.

### Decimal aggregation

Commit: `5580da7b06e4b8dd1a740ce30b66f2891989e160`.

Float conversions existed in monthly aggregation and shared hierarchy metrics. `report_money` converts individual available values via their string representation, preserves existing Decimal values, and sums without premature rounding. Report preparation retains available source precision before numeric aggregation. Income/Charity monthly and cumulative totals, shared hierarchy totals and monetary target variance now use Decimal. Bank parsing, rate selection and stored data are unchanged.

Exact regression evidence:

- `0.1 + 0.2 = Decimal('0.3')` for monthly, cumulative, Woking hierarchy and TOTAL.
- `90000000000.01 - 0.02 + 0 = 89999999999.99`.
- `-1.15 + 0.15 + 1 = 0.00`.
- Existing USD-converted values from different source currencies aggregate to `0.30`; no new currency conversion is invented.
- Synthetic Woking: `-97885.00 + 28500.00 = -69385.00`; average `-8673.125`; child shares display 77.4% and 22.6%.
- Whole-dollar report display and half-even rounding remain separate from exact calculations. Invalid display values still show `-`.

This does not recover precision already lost in historical binary storage. No schema migration or arbitrary rounding of stored amounts was attempted.

### Inline formatting

Commit: `4aa187f9a2b056b287abcb8dfbec09b9df916cf0`.

Rendered baseline evidence showed Category at 80% width and Subcategory at approximately 85%, with Subcategory positioned left of Category. Income branch containers did not match the connector selectors. Reporting Group expansion also lacked a connector. A Streamlit markdown negative bottom margin caused a context label to overlap the following row after compacting the branch.

The correction applies 100% -> 75% -> 50% to the hierarchy label/button column, keeps financial columns aligned, supplies missing Income branch containers and Reporting Group connector styling, moves connectors into the outside gutter, removes redundant inline headers and corrects the context-label margin. The detached report path, calculations, authorization and transaction-edit lifecycle are unchanged.

Local browser checks at 1440x900 and 1366x768:

| Requirement | Result |
| --- | --- |
| Reporting Group -> Category -> Subcategory | PASS, actual shared renderer with synthetic rows |
| 4/4 -> 3/4 -> 2/4 label widths | PASS, DOM widths measured exactly |
| Indentation and aligned financial columns | PASS |
| Compact inline spacing | PASS; repeated child headers removed |
| Green inline connectors | PASS, computed 3px rgb(15,118,110), scoped to descendant containers |
| Context text does not overlap next row | PASS, measured six-pixel clearance |
| Expand/collapse and small Close | PASS |
| Percentages | PASS: 60/40, then 50/33.3/16.7, TOTAL 100 |
| Long labels | PASS wrapping; no button clipping or page horizontal overflow; very long labels necessarily occupy more lines |
| Empty group | PASS, no rows and unchanged TOTAL 1000 |
| Editable Income controls | PASS visible; Save not clicked |
| Current authenticated LIVE report formatting | BLOCKED / NOT TESTED: logged-out entry page only; no production login/data access |
| Live THIRD-specific visual UAT | NOT TESTED; its protected detached path remains unchanged |

Before/after and long-label/empty-state screenshots were captured in the browser tool conversation. `report_visual_qa.py` reproduces the local synthetic rendering without launching the real application or accessing financial records. It rejects database URLs and requires E: runtime paths. Its Save binding deliberately refuses writes; use persistence QA for Save verification.

## QA and protected behavior

- Unchanged baseline: 23/23 scripts passed.
- Income membership correction: 24/24 scripts passed.
- Decimal correction: 25/25 scripts passed.
- Formatting correction: 26/26 scripts passed, serially in filename order.
- Final handoff rerun after the harness binding: 26/26 passed, zero failures. Logs are under `E:\AretiCodex\_runtime\income-report-safe-fixes-20260914\handoff-*.log` and are not committed.
- The first Decimal suite stopped at a historical float-dtype equality assertion. The narrow compatibility update retains exact row/field comparisons, adds independent exact Decimal monthly/total expectations, and compares historical displayed metrics. The subsequent complete suite passed.
- Historical whole-file guards remain exact hash/AST checks. Each compatibility layer accepts only the reviewed changes; no skip, xfail or broad assertion bypass was added.
- Woking, TB Tribute, current Item 19 policy, split-parent exclusion, Executive/THIRD populations, Income editing, Charity fixtures, CNB, Safra exact-IBAN mapping, zero-activity persistence, duplicate prevention, CHF/rates and manual categorization tests pass.
- `db.py`, `auth.py`, bank parsers, classification logic, requirements and deployment configuration are unchanged from the starting baseline.
- Final read-only diff review, exact guarded AST comparisons, `git diff --check`, protected-file comparison and added-line sensitive-material scans passed. No schema/migration changes, customer files or runtime artifacts are included.
- The portable visual harness also passed a Streamlit AppTest for Reporting Group -> Category -> Subcategory -> read-only transaction detail after binding its existing Excel-export helper. This was a harness-only correction, not a production application change.
- Final storage reading: C: 24.49 GiB; E: Elements 3056.07 GiB. Both exceed the 10 GiB threshold.

Changed files: `app.py`, `reporting.py`, `report_money.py`, `report_visual_qa.py`, `_qa_income_charity.py`, `_qa_income_decimal.py`, `_qa_income_groups.py`, `_qa_income_membership.py`, `_qa_income_three_fields.py`, `_qa_inline_hierarchy.py`, `_qa_report_formatting.py`, `_qa_third_group_income.py`, and this handoff document.

## Counter investigation: no changes

`db.get_dashboard_counts` counts Setup category rows, configured account rows, rate rows, memory rows and import-history rows. Categories 278 is not a count of Income hierarchy nodes. Accounts 45 is not directly comparable with the Database view's distinct visible `account_name` count of 10; that latter count can also combine accounts sharing a display name.

Dashboard Pending requires active status pending AND reviewed=0. Dashboard Reviewed requires active status reviewed OR reviewed=1. Hidden IDs and inactive split parents are excluded. The Database summary applies its current filters/search and counts status alone; Excluded is computed before those current view filters. Dashboard reads are cached for 90 seconds.

A fresh synthetic database reproduces Pending=0 / Reviewed=15 at the dashboard and Pending=12 / Reviewed=3 in the status-only summary, by using twelve status=pending, reviewed=1 rows and three reviewed rows. The actual Pending Review query returns zero rows. This proves the code-level discrepancy mechanism, not that production has those flags or that caching caused Areti's screenshot. No production flags were inspected or corrected.

The screenshot's `4774 + 12 = 4786` is arithmetically consistent with the status-only breakdown. A decision is still required whether the Database counters should describe stored status or effective review eligibility. Proposed labels such as 'Configured accounts' and 'Visible account names' are not implemented.

## Import timing: no optimization

Three controlled trials used the existing synthetic three-row CNB text fixture, empty synthetic memory and a fresh local SQLite database per trial. The fixture stubs PDF text extraction. Times below are milliseconds elapsed from the synthetic received marker, not real upload latency.

| Stage | Trial 1 | Trial 2 | Trial 3 |
| --- | ---: | ---: | ---: |
| Parsing completed | 17.319 | 8.525 | 7.584 |
| Classification started | 65.900 | 24.218 | 27.015 |
| Classification completed | 72.118 | 31.863 | 34.100 |
| Commit returned | 84.752 | 55.093 | 45.261 |
| Pending Review query sees all 3 | 88.803 | 59.561 | 50.177 |

Each trial reconciles `1000 - 600 - 150 = 250`, creates exactly three rows and adds zero rows on renamed-file retry. Recorded UTC trial timestamps begin `2026-09-14T04:50:21`.

No background job exists in this tested synchronous save path. Upload transport, actual PDF extraction, browser Pending Review repaint, production locking/load and production-sized memory were not measured. The public entry page visibly experienced a Render cold start, but that does not prove the reported post-import six-minute cause.

Code inspection identifies synchronous `backfill_missing_usd_amounts()` after import, before cache clearing, and 90-second read caches. Neither is established as the six-minute bottleneck. No timing target, validation reduction, infrastructure change or speculative optimization was made. A representative-volume isolated trial with actual synthetic PDF extraction and UI timestamps remains necessary.

## Report builder: proposal only

Existing capabilities are fixed report renderers, Setup taxonomy, group visibility/settings and app settings. No draft/publish/versioned report-builder workflow was found. Do not present the following as existing UI.

UX: duplicate an approved template -> name/description -> choose permitted taxonomy nodes -> arrange Group/Category/Subcategory order -> select allowlisted date/status/account/currency filters -> set approved presentation options -> preview -> reconcile -> save Draft -> authorized publisher validates and publishes. Core reports cannot be overwritten by duplication.

Proposed model, requiring separate schema/design approval:

- Report definition: immutable ID, owner, name, description, core/custom flag and current published version ID.
- Report version: immutable version ID, definition ID, typed validated configuration, taxonomy references/snapshot, parent version, creator/time and configuration hash.
- Validation record: version ID, cutoff, population fingerprint, row count, Decimal totals, validation results and actor/time.
- Publication event: version ID, publisher/time and previous version ID.
- Append-only audit event: actor, action, report/version IDs, timestamp and configuration diff, not transaction descriptions or statement contents.

Permissions: viewer reads published reports; designer creates/edits Draft copies; publisher approves validated versions; core-report administrator alone changes protected templates. Server-side authorization applies to preview, export and publication, not just visible buttons. These roles require mapping to the actual authentication model before implementation.

Validation: typed enums, no SQL or arbitrary expressions, bounded filters, valid taxonomy references, unique hierarchy positions, unchanged core eligibility/split/hidden-row rules, explicit percentage denominators, exact Decimal reconciliation, currency separation and export parity. Multiple Income matches count once. Overlapping analytical sections must not be blindly added into a grand total. Stale taxonomy/data fingerprints require a fresh preview, not silent publication.

Rollback: repoint publication through an audited event to a previously validated immutable version. Never roll back financial transactions. Draft configuration changes do not modify categories or transactions.

Build incrementally after approval: read-only template catalog and preview; Draft storage and validation; permission-controlled publication; version history/rollback; supervised UAT. Risks include taxonomy renames, stale previews, misleading overlapping totals, authorization leaks, large previews and implicit currency mixing. No implementation or migration has been performed.

Decisions for George/Areti: who may publish; whether taxonomy labels follow renames or require revalidation; allowed report scopes; percentage denominators; core-report ownership; retention; and whether 4/4 widths refer to the label column (current correction) rather than shrinking financial grids.

## Practical demonstration and training

Use synthetic data in an isolated approved test environment only. The visual fixture is intentionally read-only; its Save button does not persist. Automated persistence tests demonstrate the actual Save path separately.

1. Explain Reporting Group versus Category versus Subcategory. Income membership is an OR across these fields, not a rewrite of Reporting Group.
2. Open Income, then a Category and a Subcategory. Read the displayed account and amount before any edit.
3. Demonstrate the paired Category/Subcategory dropdown and protected amount/identity fields. Reporting Group is not editable in this Income control.
4. In the approved synthetic editing lab, change the 125 row from Income/Original to Projects/MISSING while keeping its group Income. Save once; verify the success message, new node and unchanged 125 total. Reload and confirm one row. `_qa_income_three_fields.py` automates this proof.
5. Demonstrate Cancel and a stale-edit rejection. Reload instead of repeatedly pressing Save after a conflict. Cross-group changes must not be forced through the Income editor.
6. Explain exact arithmetic versus display precision: 0.1+0.2 is exactly 0.3 internally even when a whole-dollar summary displays $0. Use transaction detail for source precision.
7. Show Woking parent/children reconciliation and the independent Income view. Do not add overlapping analytical views into a new grand total.
8. Show group/category/subcategory widths, green lines, expand/collapse, Close, long labels and an empty group. Confirm values do not change on reopen.
9. Explain configured versus filtered counters and the unresolved status/reviewed-flag distinction. A Category count is not a Subcategory count.
10. For a missing row, record report, period, filters and the three classifications. Do not recategorize production merely to make a row appear.
11. Capture a full-screen screenshot showing report title, filters, hierarchy and error. Record exact steps, expected/actual totals, time and masked transaction identifier; redact private account/description data before sharing.
12. Explain that Create/Duplicate/Draft/Publish/Version History/Rollback are proposed builder features, not current controls. Train those only after their separate implementation and approval.

George's demonstration checkpoints: membership commit -> persisted 125 and one Projects node; Decimal commit -> exact 0.3 and Woking reconciliation; formatting commit -> both inline hierarchy views, Close, long/empty cases and unchanged totals. Areti's supervised acceptance remains pending.

## Deployment and recovery plan

No push, merge or deployment is authorized by this work. Before a separately approved release: fetch and verify current production SHA, inspect the exact release diff, preserve a recovery reference, rerun combined QA on the integration SHA, and coordinate a brief Areti pause if concurrent edits could create risk. No schema/data migration is included or needed here.

Deploy only through the established approved workflow. Confirm exact deployed SHA and safe startup/health routes without importing statements or editing production transactions. If recovery is needed, use normal Git reverts of the application corrections in reverse order through that workflow; do not rewrite history or restore/reset a database.

Remaining limits: authenticated live report UAT, production counter flags, real six-minute latency cause, full-volume/PDF/UI timings, report-builder design decisions and supervised training acceptance. Existing Streamlit `use_container_width` deprecation warnings remain; no unrelated API modernization was attempted.
