# Reduced release regression resolution review

2026-10-05. Hosting access is an external blocker acknowledged by the user. Do not poll Render or ask the same service/workspace/access question until new information is provided. Conditional interim release approval is already recorded. Deployment remains HELD; this work is local only.

## Historical source guards: exact compatibility, not a waiver

The 22 historical app-fingerprint failures were caused by source assertions pinned to earlier UI bodies. The financial modules and protected app functions were already unchanged from f3ea9ce; the reduced UI was not covered by those old fingerprints.

QA-only `reduced_release_qa.py` now recognizes the exact reviewed app fingerprint and exact current report/count companion fingerprints. Only after those checks does it supply f3ea9ce app bytes to the existing historical source-comparison chain. The original Safra whole-file hash and equality against its pinned baseline remain in place. The only edit to `safra_balances_qa.py` is the three-line app-only compatibility hook; `_qa_reduced_scope.py` verifies that removing exactly that hook reproduces the entire original guard file. No parser, database, reconciliation, duplicate, classification, permission, SPLIT or repair source is normalized or substituted.

This is the same distinction enforced by the original compatibility chain: pinned-source assertions compare their historical bodies; runtime tests import/compile the real current candidate. The new helper is not imported by application runtime and does not change financial behavior or import eligibility. It neither copies nor imports the full candidate's parser/reconciliation/history-coverage modules.

`_qa_reduced_source_guards.py` executes adversarial rejection tests for unknown app bytes, changed Setup and permission code, changed report/count companion files, and unexpected parser source. It also verifies the intended historical comparisons, current runtime files remain untouched, and excluded full-candidate modules remain absent. These checks passed before the final full run; no failed assertion was marked passed without execution.

Baseline preservation now explicitly distinguishes 121 unchanged Python modules from two deliberately updated baseline QA files (the compatibility hook and old report test). All baseline application modules outside app/report/count remain byte-equivalent; existing Import branch/functions and early report-link routes are structurally unchanged. The updated authenticated AppTest additionally asserts the actual report scope message. No application Python file changed during this regression resolution.

## Report contract: stronger checks for approved partial scope

The historical `_qa_latest_import_balances.py` expected one HTML download, complete all-account totals and fallback to an older complete import. The user-approved reduced contract requires both HTML and Excel and explicitly preserves a newer incomplete committed import. Its expectations were revised to that approved contract; they were not removed or replaced with unconditional success.

The test now executes each missing-field case, checks the current incomplete balance remains visible and excluded, validates exact timestamp/ID ties before incomplete imports are added, asserts eligible exact totals and configured FX, and proves whole SQLite database contents remain unchanged by reporting. A separate actual SQLite REAL fixture proves binary amounts remain visible but excluded. Exact-text SQLite and actual NUMERIC PostgreSQL fixtures test precise eligible arithmetic; the disposable PostgreSQL data directory is verified before any test database creation, and report connections are read-only. Large NUMERIC values retain exact display and arithmetic in their explicit eligible fixture. Native signs and liabilities remain subject to the unchanged report policy and integrity tests.

The actual main-app dispatch test asserts the scope banner, both exports, workbook scope/partial total, HTML escaping, no secret metadata, and safe failure behavior without a database call outside snapshot. Final AppTest checks scope after authenticated navigation. Existing report-integrity tests retain alias, uncertainty, literal Excel, applied FX, no-write and partial-total checks. The production report/print functions are unchanged from the previously visually verified two-page output; no duplicate visual run is required merely for QA-only edits.

Intermediate revised-test failures are retained: an expectation initially selected the wrong missing-field value; a synthetic newer Safra fixture was initially placed before the no-write assertion and was moved into the explicit fixture-edit phase. These did not alter application code or relax no-write assertions. The corrected report test subsequently executed PASS on SQLite and PostgreSQL. No unchanged full suite was repeatedly run; a new complete run is justified by the reviewed compatibility/test changes and the previously unmet release-regression gate.

## Remaining boundary

The final full suite must execute on a committed, frozen candidate; its actual results supersede the earlier 62/85 checkpoint only after completion. Record pass/fail counts and any corrections without skipping or counting blocked checks as passes. Hosting ownership/access/live SHA, complete inherited diff against verified live code, practical rollback, hosted performance, necessary work pause and user acceptance remain independent external release gates. Historical financial repairs, missing originals, original 429 cause, BOC/Citi parsing and statement-import outcomes remain outside this approved interim scope.
