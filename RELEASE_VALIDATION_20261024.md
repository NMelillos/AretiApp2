# Areti release validation — 24 October 2026

## Status and scope

BLOCKED for full-master completion and production release. Publish this branch for review; do not fast-forward main or deploy from this report. Remote main was directly verified at b823da5d0bda450c8ee71c736c5de28e9d36faa8. The preserved fc5f8dd753837d9b589066f0b58c6870242f96fa worktree and full eb46d0e candidate remain unchanged. There is no new deployment or production-data action.

The branch contains the existing approved 22 October balance packages: independent balance-event guidance, strict English Revolut/CNB completion, CSV-proven AMEX PDF metadata only, and Deposits/Debts presentation. It preserves the current Setup-only partial scope. Full Revolut currency-wallet implementation remains design-only; this report grants no schema/configuration or historical rewrite authority.

New tracked QA change: assert all twelve relevant runtime modules load from this checkout. All eight application modules match the normalized source hashes of fc5f8dd exactly; no new business, parser, financial, schema or persistence change was introduced during this validation pass.

## Executed results

- Source-pinned package integration: 38 tests PASS, zero failures/errors/skips.
- Fresh disposable PostgreSQL 18: PASS, including writer fence, rollback, idempotence, copy-file duplicate rejection, CSV/classification/history preservation, zero PDF transaction writes, raw AMEX reconciliation and English/CNB completion. Both local QA clusters stopped after execution.
- Syntax: all 188 Python files PASS. Git diff whitespace check PASS.
- Full inventory: 131 modules; 48 PASS, 79 non-passing, 4 helper-only/not applicable.
- Full reported checks: 612; assertion failures 28; errors 80; skips 6. Script checks are acceptance scenarios, not individual assertion counts. Collection errors are included in the error count, not fabricated passed tests.
- Every per-module outcome is in QA_RELEASE_20261024.json. Full QA is NOT claimed passed.

Commands: the private run_full_qa_entrypoints_20261024.py orchestrator executed the original QA module entrypoints in separate credential-free subprocesses; run_24oct_targeted.py executed qa_22oct_integrated.py and qa_22oct_postgres.py with the candidate first on sys.path. Private runners/sources/logs remain outside Git. No production credentials were inherited; external network, outside-root SQLite and arbitrary subprocesses were rejected. An exact read-only Git Python-blob exception was safety-tested; Git writes remained denied in QA children.

## Evidence corrections and retained failures

The initial generic-discovery runs also collected imported historical TestCases and did not consistently honor each module's custom suite. They are retained privately as diagnostic attempts, not authoritative current-suite coverage. A private older boc_summary_balances.py could shadow the checkout when an evidence-folder script launched Python. That harness defect was corrected by prepending the candidate, and the new runtime-path test verifies it. The valid 38-test integration and PostgreSQL rehearsal were rerun after correction. A synthetic optional-evidence script also received the harness flag as a filename; its argv was corrected and the reviewed-trigger contract passed. None of these corrections changed application behavior or financial guards.

## Remaining blockers

1. Original-source replays cannot all complete: the supplied historical evidence folder is absent at its recorded path. Missing cases include the original Comerica #5055, original BOC Statement Summary, original Citi and historical CNB PDFs. Do not fabricate replacements or call these passed.
2. Legacy full-QA qualification remains unresolved. Several tests pin older release ASTs/hashes or earlier report columns, while later approved changes are already in main. Others require absent private fixture directories/environment variables, E:-only disposable runtimes, older UI doubles without session_state, or capabilities deliberately blocked by isolation. Preserve these failures; do not revert accepted behavior or relax financial safeguards to force a green result. Not every residual failure is independently classified as an application defect. The unchanged b823da5 baseline reproduces the legacy Latest Import Balances total/warning assertion failure under the same isolated conditions.
3. The unresolved Render stop/start release gate remains held. Monitoring did not expose request/termination evidence proving idle spin-down. No fresh release authority is inferred from branch publication.
4. Full multi-currency Setup/report wallets require the separately approved representation/schema/configuration decision and original-source acceptance. No wallet implementation, migration, remapping or forced permanent currency is included.

Authenticated business UAT remains pending a separately authorised live release. The signed-out/absent live account-5055 eligibility evidence is not reclassified as a defect. No historical repair is performed.

## Publication and safety

Changed files relative to remote main b823da5:

- Application: app.py, duplicate_balance_import.py, completed_balance_sources.py, amex_balance_metadata.py, import_history.py, latest_import_balances.py, latest_balances_compact.py, balance_report_sections.py.
- QA: _qa_22oct_scope.py, _qa_amex_metadata_22oct.py, _qa_balance_events_22oct.py, _qa_balance_sections_22oct.py, _qa_completed_balances_22oct.py, _qa_revolut_currency_design_22oct.py.
- Status/design/evidence: BALANCE_PACKAGES_20261022.md, REVOLUT_CURRENCY_DESIGN_20261022.md, RUN_STATE.md, QA_RELEASE_20261024.json, RELEASE_VALIDATION_20261024.md.

The final Git commit/ref must be independently read back from GitHub after push; this document alone is not remote publication proof. Main remains unchanged because the full QA/release gates are not green. No confidential PDFs, screenshots, financial snapshots, credentials or raw logs are included. No production transaction, balance, classification, Setup, schema, environment or Render configuration change occurred.
