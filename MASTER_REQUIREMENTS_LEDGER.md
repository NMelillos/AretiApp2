# Reduced-scope reconciliation with the full master

The authoritative full master ledger and all evidence mappings remain preserved at full candidate `eb46d0e`, `C:/Users/Student/.codex/worktrees/areti-master-continuation/test/MASTER_REQUIREMENTS_LEDGER.md`. This is a scope delta, not a replacement or claim that the master is complete.

| Requirement area | Reduced IMPLEMENTED | Reduced TESTED | DEPLOYED / DATA-REPAIRED / USER-ACCEPTED | Remaining evidence or decision |
| --- | --- | --- | --- | --- |
| Normal Setup access / diagnostics loading | Yes; authenticated opt-in Corrections | Setup/AppTest/auth gates | No / none / pending | Real 429 cause and live latency/access remain unverified |
| Setup save outcome visibility | Yes; one-call guidance and success rerun | Targeted 429/unknown acknowledgement/success | No / none / pending | Baseline replacement behavior unchanged; no atomic shared-workbook guarantee |
| Statement import outcomes | Deferred; baseline retained | Protected baseline Import AST and CNB targeted regressions only | No / none / pending | Coupled parsing/commit/duplicate/reconciliation work remains in held full candidate |
| Pending header, selected counts, exhaustive matches | Yes; shared fresh population, seven metrics | Actual SQLite flags/no-write; AppTest | No / none / pending | Live latency and Areti workflow acceptance |
| Latest balances, HTML/print/Excel | Yes; newest uncertain visible, conservative aliases, precise exports, partial totals | SQLite/PostgreSQL/read-only, identity/precision/FX/export cases; final AppTest; two visually reviewed print pages | No / none / pending | No source reconciliation claim; historical binary precision/periods unknown; liability netting approval and live verification remain |
| Source parser and exact atomic imports; semantic duplicates; history coverage | Full candidate preserved; no new changes here | 123 unchanged module comparison; targeted CNB/NUMERIC regressions | No / none / pending | Full candidate restriction still unapproved; missing source fixtures and broader original-source gates unresolved |
| Stable IDs / non-destructive Setup replacement | Excluded | Baseline writers unchanged | No / none / pending | Separate persistence review and approved workbook provenance |
| Historical CNB/NOMAD/period/amount corrections | No new repair; NOMAD protection retained | Existing repair/financial dependencies unchanged | No / none / pending | Separate source-bound approval; no delete/reimport or invented values |
| Release identity, rollback and authenticated verification | Local scope prepared only | Fresh remote main / local identity checks | No / none / pending | Current service/SHA/access, practical rollback, approved non-production UAT, final required release suite and explicit hold lift |

Evidence: full master/eighteen screenshots already reviewed, preserved with original candidate; current reduced tests documented in REDUCED_QA_RESULTS.md. New candidate is a separate local branch from verified f3ea9ce, not the full candidate with its guard disabled. All five status dimensions must remain separate in future handovers.
