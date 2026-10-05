# Interim release QA checkpoint

2026-10-05. The full reduced-candidate inventory was executed once: **54/84 PASS, 30 FAIL, runner exit1**. A subsequent log audit identified seven local PostgreSQL connection failures (not six), 22 historical whole-app fingerprint failures and one old report-contract failure. PostgreSQL had initially started on the default port; the exact owned cluster was stopped and restarted explicitly on 127.0.0.1:55459. All seven affected database tests then executed and passed. No failed assertion was waived.

Final relevant report/Streamlit/dependency tests also passed after the scope/exclusion wording follow-up. The additional synthetic pending-performance script passed separately. Combined latest applicable receipts therefore record **62/85 PASS and 23 FAIL**. This is not a passing full release suite and does not satisfy that release gate. The original full candidate's 89/89 is not used.

## Commands and isolated execution

All checks used:

```powershell
& 'C:\Program Files\Python312\python.exe' 'E:\AretiCodex\_runtime\master-continuation-20261005\run_qa.py' 'C:\Users\Student\.codex\worktrees\areti-independent-local\test' '<receipt label>' '<script(s)>'
```

For `reduced-release-regression1`, no scripts were supplied: all 84 then-present `_qa_*.py` scripts were enumerated and executed serially. The performance test was added separately; it is the 85th distinct script. `interim-final-targets1` ran report integrity, AppTest, reduced scope, Decimal roundtrip, event-trigger catalog, fenced reads, financial atomic, financial preconditions and financial remediation: **9/9 PASS**. `interim-port-retry-existing` ran existing-import comparison: **1/1 PASS**. `reduced-pending-performance2`: **1/1 PASS**. Only changed-report checks and failed local-port checks were rerun; no unchanged full suite was repeated.

The private runner strips production database/PostgreSQL/Supabase environment configuration, uses E: temporary files and verifies the exact owned PostgreSQL data directory before disposable database creation/deletion. No production import, schema change, repair, taxonomy change or write occurred. Existing financial/SPLIT/NOMAD/reconciliation dependencies remain baseline; final reduced scope test proves 123 baseline modules unchanged and protected Import/functions/routes unchanged.

## Genuine outstanding regression gate

Twenty-two checks stop at the unchanged `safra_balances_qa.py` assertion `Unreviewed Safra/balances source change`: the historical whole-app fingerprint does not accept the approved reduced UI changes. These checks are failed, not skipped or counted as passed. They need a reviewed narrow reduced-build compatibility update retaining full fingerprint/tamper rejection, followed by execution of affected behavioral assertions. No guard has been weakened, removed or bypassed.

The legacy `_qa_latest_import_balances.py` fails because it expects one download while the approved report exposes HTML and Excel. Its other historical expectations include hiding incomplete newest imports behind older complete balances, which contradicts the approved visible-uncertainty policy. Its expectations need explicit review and tests of the actual current report; passing `_qa_report_integrity.py` does not silently replace that failed release check.

The partial-report follow-up changed only report/export scope and warning labels. After visual review, ordering warnings were distinguished from exclusions; all three relevant final report/AppTest/dependency scripts reran PASS. The other unchanged financial application bytes retain their executed applicable receipts. The full release gate still requires a green final candidate after the outstanding regression review; this checkpoint makes no such claim.

## Performance, visual review and limits

Synthetic SQLite fixture: 5,141 stored records, 5,138 visible pending records, preserving the protected hidden IDs. Five measurements gave median 0.083s/max 0.126s for the query; actual authenticated Pending AppTest rendering took 2.280s and shared one fresh pending read across header/page. Full SQL-dump no-write assertion passed. The initial performance test expected all 5,141 rows and failed; its expectation was corrected to respect baseline hidden-ID protection, without changing the safeguard. Both logs are retained. Hosted PostgreSQL/network latency and Areti acceptance remain unverified.

Current report HTML was printed locally with hidden headless Edge and rendered to two PNG pages; both final pages were visually inspected. Interim current-Setup scope, partial total, visible balances and exclusion/verification reasons are readable, with repeated headers and no clipping. Scope is also present in UI and Excel; final report QA asserts literal text, precision contract, eligibility-preserving warning labels and zero writes. Private artifacts: `interim-scope-render/`; no confidential statement/screenshot/credentials/artifacts are committed. Original evidence review remains 18/18, as preserved at eb46d0e; no repeat review is claimed.

Private receipts/manifests: `interim-final-receipt.json`, `interim-final-source-manifest.json` (130 Python files), plus all individual labels/logs and the earlier pre-label-refinement `interim-regression-source-manifest.json`. The original 7ea6920 tested manifest and full eb46d0e evidence are preserved. See INTERIM_RELEASE_GATES.md for live service/access, inherited diff, rollback, pause and acceptance blockers.

## Complete latest-applicable script outcomes

| Script | Receipt | Exit | Result |
| --- | --- | --- | --- |
| _qa_analytical_widths.py | reduced-release-regression1 | 1 | FAIL |
| _qa_app_continuation_smoke.py | interim-final-targets1 | 0 | PASS |
| _qa_cnb_import.py | reduced-release-regression1 | 0 | PASS |
| _qa_database_identity.py | reduced-release-regression1 | 1 | FAIL |
| _qa_decimal_ai.py | reduced-release-regression1 | 0 | PASS |
| _qa_decimal_boundaries.py | reduced-release-regression1 | 0 | PASS |
| _qa_decimal_pipeline.py | reduced-release-regression1 | 0 | PASS |
| _qa_decimal_roundtrip.py | interim-final-targets1 | 0 | PASS |
| _qa_decimal_ui.py | reduced-release-regression1 | 0 | PASS |
| _qa_decimal_xls.py | reduced-release-regression1 | 0 | PASS |
| _qa_deferred_editor.py | reduced-release-regression1 | 0 | PASS |
| _qa_deployment_identity.py | reduced-release-regression1 | 0 | PASS |
| _qa_event_trigger_catalog.py | interim-final-targets1 | 0 | PASS |
| _qa_event_trigger_diagnostics.py | reduced-release-regression1 | 0 | PASS |
| _qa_executive_total.py | reduced-release-regression1 | 0 | PASS |
| _qa_existing_import_compare.py | interim-port-retry-existing | 0 | PASS |
| _qa_fenced_reads.py | interim-final-targets1 | 0 | PASS |
| _qa_financial_atomic.py | interim-final-targets1 | 0 | PASS |
| _qa_financial_atomic_command.py | reduced-release-regression1 | 0 | PASS |
| _qa_financial_preconditions.py | interim-final-targets1 | 0 | PASS |
| _qa_financial_remediation.py | interim-final-targets1 | 0 | PASS |
| _qa_financial_writer_fence.py | reduced-release-regression1 | 0 | PASS |
| _qa_followup_visibility.py | reduced-release-regression1 | 0 | PASS |
| _qa_import_history_reliability.py | reduced-release-regression1 | 0 | PASS |
| _qa_income_amount_precision.py | reduced-release-regression1 | 1 | FAIL |
| _qa_income_amount_representation.py | reduced-release-regression1 | 1 | FAIL |
| _qa_income_charity.py | reduced-release-regression1 | 0 | PASS |
| _qa_income_charity_edit.py | reduced-release-regression1 | 0 | PASS |
| _qa_income_conflict_diagnostic.py | reduced-release-regression1 | 1 | FAIL |
| _qa_income_decimal.py | reduced-release-regression1 | 0 | PASS |
| _qa_income_groups.py | reduced-release-regression1 | 0 | PASS |
| _qa_income_membership.py | reduced-release-regression1 | 0 | PASS |
| _qa_income_save_backend.py | reduced-release-regression1 | 0 | PASS |
| _qa_income_save_types.py | reduced-release-regression1 | 1 | FAIL |
| _qa_income_save_usd.py | reduced-release-regression1 | 1 | FAIL |
| _qa_income_three_fields.py | reduced-release-regression1 | 0 | PASS |
| _qa_inline_hierarchy.py | reduced-release-regression1 | 0 | PASS |
| _qa_latest_import_balances.py | reduced-release-regression1 | 1 | FAIL |
| _qa_memory_subcategory.py | reduced-release-regression1 | 0 | PASS |
| _qa_nomad_attempt_evidence.py | reduced-release-regression1 | 0 | PASS |
| _qa_nomad_controlled_execution.py | reduced-release-regression1 | 0 | PASS |
| _qa_nomad_execution_gates.py | reduced-release-regression1 | 0 | PASS |
| _qa_nomad_final_precheck.py | reduced-release-regression1 | 0 | PASS |
| _qa_nomad_frozen_catalog.py | reduced-release-regression1 | 0 | PASS |
| _qa_nomad_precheck_catalog.py | reduced-release-regression1 | 0 | PASS |
| _qa_nomad_recovery_page.py | reduced-release-regression1 | 1 | FAIL |
| _qa_nomad_recovery_release_ui.py | reduced-release-regression1 | 0 | PASS |
| _qa_nomad_recovery_status.py | reduced-release-regression1 | 0 | PASS |
| _qa_nomad_runtime.py | reduced-release-regression1 | 1 | FAIL |
| _qa_nomad_runtime_postgres.py | reduced-release-regression1 | 0 | PASS |
| _qa_nomad_transport.py | reduced-release-regression1 | 0 | PASS |
| _qa_nomad_transport_pool.py | reduced-release-regression1 | 0 | PASS |
| _qa_pending_amount_override.py | reduced-release-regression1 | 0 | PASS |
| _qa_pending_performance.py | reduced-pending-performance2 | 0 | PASS |
| _qa_pending_save_normalization.py | reduced-release-regression1 | 0 | PASS |
| _qa_rates_chf_persistence.py | reduced-release-regression1 | 0 | PASS |
| _qa_reduced_scope.py | interim-final-targets1 | 0 | PASS |
| _qa_repair_diagnostics_export.py | reduced-release-regression1 | 0 | PASS |
| _qa_repair_readiness.py | reduced-release-regression1 | 0 | PASS |
| _qa_report_cutoff_notice.py | reduced-release-regression1 | 0 | PASS |
| _qa_report_formatting.py | reduced-release-regression1 | 0 | PASS |
| _qa_report_integrity.py | interim-final-targets1 | 0 | PASS |
| _qa_review_counters.py | reduced-release-regression1 | 0 | PASS |
| _qa_review_status.py | reduced-release-regression1 | 1 | FAIL |
| _qa_reviewed_event_triggers.py | reduced-release-regression1 | 0 | PASS |
| _qa_reviewed_income_save.py | reduced-release-regression1 | 1 | FAIL |
| _qa_revolut_business.py | reduced-release-regression1 | 1 | FAIL |
| _qa_safra_august_balances.py | reduced-release-regression1 | 1 | FAIL |
| _qa_safra_completion.py | reduced-release-regression1 | 1 | FAIL |
| _qa_safra_duplicate_preview.py | reduced-release-regression1 | 1 | FAIL |
| _qa_safra_exact_iban.py | reduced-release-regression1 | 1 | FAIL |
| _qa_safra_history.py | reduced-release-regression1 | 0 | PASS |
| _qa_safra_history_identity.py | reduced-release-regression1 | 0 | PASS |
| _qa_safra_import.py | reduced-release-regression1 | 1 | FAIL |
| _qa_safra_lifecycle.py | reduced-release-regression1 | 0 | PASS |
| _qa_safra_uat.py | reduced-release-regression1 | 1 | FAIL |
| _qa_split_double_count.py | reduced-release-regression1 | 0 | PASS |
| _qa_split_editing.py | reduced-release-regression1 | 1 | FAIL |
| _qa_supabase_project_identity.py | reduced-release-regression1 | 0 | PASS |
| _qa_third_cutoff_visibility.py | reduced-release-regression1 | 1 | FAIL |
| _qa_third_group_income.py | reduced-release-regression1 | 1 | FAIL |
| _qa_third_nested.py | reduced-release-regression1 | 1 | FAIL |
| _qa_third_report_refinement.py | reduced-release-regression1 | 0 | PASS |
| _qa_top_priority.py | reduced-release-regression1 | 0 | PASS |
| _qa_trend_average.py | reduced-release-regression1 | 0 | PASS |
