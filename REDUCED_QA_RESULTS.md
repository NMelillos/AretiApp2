# Reduced-candidate executed QA

This records the historical 7ea6920 targeted checkpoint. The later conditional-release run and report-labeling follow-up are recorded in INTERIM_QA_RESULTS.md: 62/85 latest applicable outcomes pass and 23 fail; release regression remains unmet. Do not present this earlier five-script checkpoint as final release approval.

2026-10-05. Tested the actual reduced branch; the full candidate's 89/89 result is not evidence for this build. No unchanged full suite was rerun.

Private runner: `E:/AretiCodex/_runtime/master-continuation-20261005/run_qa.py`. Python: `C:/Program Files/Python312/python.exe`. Invocation pattern:

```powershell
& 'C:\Program Files\Python312\python.exe' 'E:\AretiCodex\_runtime\master-continuation-20261005\run_qa.py' 'C:\Users\Student\.codex\worktrees\areti-independent-local\test' '<receipt label>' '<script>'
```

The runner removes PostgreSQL/Supabase/database configuration from child environments, uses private E: temporary paths, and sets only the disposable PostgreSQL endpoint 127.0.0.1:55459 with an exact owned data-directory check. Test imports/DDL occurred only in synthetic SQLite fixtures and disposable local PostgreSQL databases. No production write or historical repair was executed.

| Script | Final applicable receipt | Exit | Evidence |
| --- | --- | --- | --- |
| `_qa_reduced_scope.py` | `reduced-report-final/results.json` | 0 | 123 unchanged baseline modules; protected Import/functions/routes; no new guard; one-call Setup acknowledgements; fresh pending and exhaustive buckets; no writes; unrelated Citizens bank; renamed source labels, aliases, account formatting and separate currency; unauthorized Corrections blocked |
| `_qa_report_integrity.py` | `reduced-report-final/results.json` | 0 | SQLite and actual PostgreSQL exact storage, binary uncertainty, newest incomplete rows, aliases/ambiguous labels/rates, invalid dates, currency separation, approved FX, native liability sign, green boundaries, literal/numeric Excel, shared partial total and read-only assertions |
| `_qa_app_continuation_smoke.py` | `reduced-integration-final/results.json` | 0 | Final report changes integrated with actual Streamlit AppTest; login, Setup, Pending, History, latest report and opt-in Corrections; synthetic financial counts/amounts unchanged |
| `_qa_cnb_import.py` | `reduced-final/results.json` | 0 | Unchanged baseline CNB parser/SQLite import paths, malformed-source validation, date/sign/total mapping and duplicate/retry checks |
| `_qa_decimal_roundtrip.py` | `reduced-final/results.json` | 0 | Actual disposable NUMERIC PostgreSQL persistence/monthly-report/exports with large/negative/fine precision; relevant modules remain byte-identical after this run |

Five distinct targeted scripts passed. Report and app integration were rerun because report identity matching changed, not to repeatedly execute an unchanged full suite. CNB and Decimal scripts apply to unchanged relevant baseline modules; structural/byte comparison and the final frozen source manifest establish that continuity. All individual logs and result JSONs remain private beside this runner.

Intermediate evidence is retained: initial Windows text decoding caused protected currency-text drift; the dependency test caught it and UTF-8 reconstruction corrected it. A later reduced-scope test used the wrong THIRD-route function name and failed with StopIteration; its name was corrected and the actual route comparison passed. Neither failure was waived. `reduced-final/results.json` therefore has one historical failed test and four passes; use the newer final receipts for reduced-scope and report/app results, not a claim that this older combined receipt entirely passed.

A sanitized synthetic report was printed locally with hidden headless Edge to `reduced-render/report.pdf`, rendered with pypdfium2, and both pages visually inspected: readable landscape columns, repeated header, precision and partial-total warnings, no clipping. The print/export functions were unchanged by subsequent identity-query refinements; final report QA regenerated and checked their HTML/Excel dataset. This does not constitute real-user browser UAT or source-bound financial reconciliation. No confidential screenshot, original statement, credentials, PDF or private audit output is committed.

The final source manifest and combined selected-script receipt are private `reduced-tested-source-manifest.json` and `reduced-final-receipt.json`. Documentation changes after test completion do not change tested Python bytes. Final local commit identity is obtained from Git HEAD; RUN_STATE records the branch and baseline.

Limits: no full release suite on this smaller branch, no live authenticated latency/429-provider diagnosis, no current deployment verification, no write-path user acceptance, no new original-source parser validation, and no historical data repair. These remain separate release/scope gates.
