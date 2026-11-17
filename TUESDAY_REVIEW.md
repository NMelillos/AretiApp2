# Tuesday consolidated review candidate — NOT DEPLOYED

Baseline: `199ded2c33696fea8861b293deb9aaad7e4d3cb0`.
Branch: `codex/areti-master-tuesday-20261117`.
This is a review candidate, not confirmation that every reported issue is closed.

## Root-cause and scope matrix

| Issue | Baseline reproduction | Cause and bounded correction | Result |
|---|---|---|---|
| Natalia suggestions | Exact October source produced 122 unidentified rows with populated memory | Greek source frame lacks classifier input fields. Derive missing inputs on a copy and preserve source attributes; existing atomic persistence already saves suggestions. | 64/122 non-fallback suggestions on private 592-row memory snapshot; pending retrieval retains suggestions, reviewed remains false. |
| Flexible merchant history | SOCALGAS/LADWP changed-detail fixtures missed | Existing signatures include changing boilerplate. Add batch distinctive-token index with ambiguity rejection. | Focused fixtures and existing merchant tests pass. |
| Taxonomy | Swimming with empty memory missed | Existing hints came from history, not actual subcategory definitions. Pass read-only taxonomy once per batch; whole-phrase matching returns its actual parent. | Empty-memory, boundaries, ambiguity and exact-history precedence pass. |
| B-Projects | Exact October PDF yielded seven transactions but missing preview balances | Business parser validated summary internally but returned movements only. Expose that summary, bind validated owner/IBAN/period/pages, reject concealed nonzero other wallets. | 997.23 + 22.00 - 282.09 = 737.14 EUR; seven unchanged movements; real Import preview passes. |
| Citi November | Transaction -25 correct, issuer balances -486.50/-461.50 incorrectly retained | Normal card statements bypass special adjustment contract. Add one validated cardholder summary conversion, with adjacent-history and Setup revalidation before commit. | +486.50 - 25.00 = +461.50 USD. Missing prior history allowed; conflicting adjacent history rejected; special adjustments unchanged. |
| AMEX November | Exact PDF and 171-row CSV available; completed raw-hash gate reproduced on disposable data | Actual production hash/history was not accessed. Persisted rows do not retain every original CSV Account #, so full regenerated-source/card equivalence cannot be proven from them alone. | BLOCKED for regenerated CSV equivalence. No production-code relaxation. Exact completed CSV, renamed file, metadata-only preview, idempotency and zero transaction changes pass locally. |
| Fifth Third #5055 | Downloaded 8,619-byte source passes; separately downloaded 101,700-byte scan fails OCR validation | Scan OCR contains Deposits / Credits heading without required count/amount. Cannot infer zero from the different native file. | Native: two movements, 40575.60 - 31000 - 11 = 9564.60. Scan remains fail-closed. No Fifth/OCR changes. |

## Protected Natalia contract

EUR: 115 completed rows, 6.90 + 33136.73 - 22650.96 = 10492.67.
USD: 7 completed rows, 1073.78 - 549.47 = 524.31.
Total 122; two pending and eleven reversed remain excluded. Parser, wallet persistence,
September history and Latest Import Balances implementation were not changed.
Existing exact-source, atomic rollback, duplicate and wallet-report regressions pass.

## AMEX evidence boundary

Current source CSV SHA256:
`e5470fdf99840a60b4cb4417766cfb83438bc17f74e0b6226a13713014f1a4fc`.
Whether this hash exists in production history is NOT VERIFIED. Do not label a
redownload as the proven production cause. Needed evidence: uniquely matching
completed original import plus preserved original per-member card-number evidence.
No partial, fuzzy or filename identity fallback was added.

November PDF: TIMUR BEKMAMBETOV / TENGRI INC / 5-76007, closing 2026-11-11.
Source: 17759.34 - 24666.23 + 33399.92 = 26493.03.
Existing signed liability metadata stays -17759.34 / +24666.23 / -33399.92 / -26493.03.

## Verification ledger

Executed with Python 3.12 and credential-stripped disposable QA environment.
Network guard permits localhost only; private originals and QA databases stay outside Git.

* Combined protected run: 106 PASS (16 initial Tuesday checks plus 90 existing checks).
* Added current AMEX lifecycle/UI checks: 2 PASS.
* Additional Sapphire/report preservation checks: 6 PASS.
* Applied FX: 2 PASS. Comerica synthetic validation contracts: 5 PASS.
* Disposable PostgreSQL 18: 9 PASS (seven original Tuesday lifecycle/UI checks plus
  two added AMEX checks). Cluster stopped after both runs.
* Total successful distinct test/backend cases: **130 PASS**; **0 assertion failures**.
* Five legacy AMEX tests: **5 ERROR**, identical on clean baseline and candidate,
  missing `2026-09-11 (2)screen reader optimised.pdf`. Their private manifest was
  restored from existing QA evidence; original PDF remains unavailable. No test edits.
* **0 new errors, 0 new failures, 0 skips** in the executed comparison.
* Syntax: **221 Python files PASS**. Diff whitespace/protection: PASS.
* Existing standalone memory script: four assertions PASS (not added to test total).

Non-additive coverage breakdown: Tuesday focused 18 (five classification, four source
checks including synthetic Citi edge cases, nine SQLite lifecycle/UI); existing merchant
tests three; protected-bank tests 85; report/FX tests 15; PostgreSQL nine.
Current real Import-page UI checks run on both SQLite and PostgreSQL: B-Projects/Citi,
native Fifth, Natalia classification/explicit disposable commit, and AMEX metadata preview.
These are local AppTest results, not production UAT. Broad legacy QA is not fully green.

Current QA file: `_qa_tuesday_20261117.py`. Existing modules include
`_qa_revolut_greek`, `_qa_revolut_sections`, `_qa_natalia_wallet_report`,
`_qa_cnb_wrapped_zero`, `_qa_cnb_other_credits`, `_qa_cnb_empty_preview`,
`_qa_safra_zero_balance`, `_qa_revolut_closure`, `_qa_revolut_urgent`,
`_qa_fifth_native_check`, `_qa_fifth_scanned`, `_qa_fifth_third_uat`,
`_qa_current_memory_report_20261110.MemoryTests`, current BOC/Citi classes,
report refinements, final corrections, UAT-004, UAT-016 and Comerica validation tests.
Private runner manifests and full logs are retained under `qa-tuesday-20261117` and
`qa-greek-revolut-20261112/tuesday-*.log` outside the repository.

## Performance (local representative snapshot, not production timing)

592 memory rows, 306 taxonomy definitions, exact Natalia 122 rows:
baseline 0.375 seconds / 0 suggestions; candidate 0.659 seconds / 64 non-fallback
results (63 suggestions, one existing similarity result, 58 unidentified).
B-Projects parse/summary: 0.285 seconds. Native Fifth parse: 0.151 seconds.
Unchanged scanned Fifth diagnostic: 7.593 seconds. AMEX equivalence matcher was not
implemented, so no scalability claim is made for it.

## Preservation / release boundary

No production access, writes, imports, deployment, main update, online FX, schema
change, Setup change or historical rewrite. Test inserts/updates and forced failures
occurred only in disposable databases. No source statement or private history is committed.
No report layout, Safra, Fifth Third/OCR, CNB, BOC, AMEX production code or rates changed.

Review the implemented corrections with the two explicit open items above.
NOT DEPLOYED — awaiting review.
