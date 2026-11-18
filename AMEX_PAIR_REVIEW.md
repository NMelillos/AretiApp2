# Final consolidated AMEX closure — NOT DEPLOYED

Production baseline: `199ded2c33696fea8861b293deb9aaad7e4d3cb0`.
Previous candidate: `f4dc3c3bf87918e6af4a56a36a2e9b9f8211de4e`.
Branch: `codex/areti-master-tuesday-20261117`.

## Corrected requirement and source contract

The client confirmed no successful November AMEX transaction import occurred.
The existing completed-CSV gate is appropriate for historical balance recovery,
but was the wrong prerequisite for this NEW statement. No canonical historical
fallback was added. No historical secondary-card number is reconstructed.

The new workflow composes the existing CSV parser/account transformation with
the existing screen-reader summary parser. The PDF contributes account identity,
member/card summaries, dates and balances; it never supplies persisted movements.
CSV supplies all 171 transaction rows, native dates, descriptions and amounts.

Exact supplied PDF: `2026-11-11.pdf`, TIMUR BEKMAMBETOV / TENGRI INC / 5-76007.
CSV: `2026.11.17 activity (11).csv`, SHA256
`e5470fdf99840a60b4cb4417766cfb83438bc17f74e0b6226a13713014f1a4fc`.
No assumption is made about this hash being present in production.

Period: 2026-10-13 through 2026-11-11. Exact issuer equation:
17759.34 - 24666.23 + 33399.92 = 26493.03.
Existing internal liability convention:
-17759.34 + 24666.23 - 33399.92 = -26493.03.
CSV sign conversion is the existing importer conversion, unchanged.

The PDF's labelled member summaries independently prove:

| Member | Card ending | CSV rows | Charges | Credits excluding payments |
|---|---|---:|---:|---:|
| TIMUR BEKMAMBETOV | 5-76007 | 91 | 15804.99 | 0.00 |
| NATALIYA FISHMAN | 5-72022 | 39 | 2854.85 | 68.33 |
| MAX REINER | 5-71032 | 41 | 14740.08 | 33.90 |

Payments 24564.00 are source-proven against the primary holder. Total incoming
24666.23 and outgoing 33399.92 match the Account Total exactly. Two CSV dates,
October 10 and 12, precede the calculated billing window but are explicitly
listed in this statement's dated activity. Date coverage is checked per member
against the PDF; dates are neither rewritten nor discarded. This date-only
coverage extraction creates no alternative PDF transaction dataset.

Every CSV member/card must match the PDF's labelled summary. The demonstrated
41-row MAX REINER card alteration to 99999 is rejected directly from source
evidence. Setup must resolve to exactly one matching USD AMEX facility, and every
derived member account must retain that facility. No Setup row is created/changed.
The disposable facility was `3715-373275 [append the name on the .csv file]`;
production Setup was not accessed or asserted to match it.

## Lifecycle, duplicates and audit

Upload and preview are read-only. Classification uses the current shared batch
classifier. Explicit authenticated confirmation commits CSV transactions, PDF
balances and one CSV-hash Import History event in a single database transaction.
The balance audit retains both hashes, both filenames, actor, Setup identity,
count, period and source summary. No alias or second transaction event is created.

Commit reparses/revalidates the pair, checks the preview token and locks/rechecks
Setup. Failed transaction persistence, balance persistence or history update
rolls everything back. Transactions enter ordinary Pending Review, unreviewed.
Exact/renamed repeated pairs return already imported without changes. Conflicting
or partial history fails closed. Reordered CSV rows validate as a source multiset,
but a different raw CSV against an already completed pair requires review and
does not create duplicates. No weak regenerated-history fallback is introduced.

Previously completed exact-hash CSV imports retain the separate metadata-only
recovery path and original proof/idempotency checks.

## Format stability

An independent synthetic December fixture uses a different period, one Swimming
transaction for 12.00 and source balances 100.00/112.00. Source validation,
taxonomy suggestion and atomic persistence pass unchanged on SQLite and
PostgreSQL. No November dates, count, amounts or source hashes appear as
production decision constants. Unsupported or inconsistent summaries fail closed.

## Additional production files and reasons

* `amex_statement.py`: source-pair validation, card evidence, read-only preview,
  atomic commit, completed-pair verification and UI orchestration.
* `amex_balance_metadata.py`: route the UI to new-pair handling while retaining
  exact-history recovery; allow reuse of one PDF text extraction.
* `db.py`: opt-in, owned-transaction batch persistence used only by this AMEX
  path. Existing duplicate keys and row values remain unchanged; other importers
  retain their default path. Bounded multi-row inserts replace per-row queries.

Additional QA: `_qa_amex_pair.py`. Existing tests were not weakened or edited.
All prior Tuesday production corrections remain unchanged.

## Final verification ledger

* Functional cases: **156 PASS**, **0 failures**, **0 new errors**, **0 skips**.
* This comprises 134 successful SQLite/source/regression cases and 22 disposable
  PostgreSQL 18 cases. Repeated affected checks are not double-counted.
* New AMEX focused: 13 cases on each backend, including exact November source,
  synthetic future month, actual Streamlit new import/duplicate, card mutation,
  count/amount/date/identity rejection, rollback, Setup/upload changes and history.
* Current Tuesday lifecycle/UI cases: nine on each backend, preserving the old
  exact-hash recovery and all earlier corrected workflows.
* Existing protected-bank cases: 85 PASS. Report/FX cases: 15 PASS.
* Classification: five focused plus three historical merchant cases PASS;
  representative Natalia remains 64/122 non-fallback, EUR 115 + USD 7, balances,
  pending/reversed exclusions and wallet-report behavior unchanged.
* Current Import-page AppTest coverage: five test methods on each backend,
  covering B-Projects/Citi, Natalia, Fifth native, AMEX recovery and AMEX new import.
* Syntax: 223 Python files PASS. Diff/protection PASS.

Five legacy September AMEX tests still ERROR because the private
`2026-09-11 (2)screen reader optimised.pdf` is absent from the local QA fixture
location. Same test IDs and exact FileNotFoundError were verified on clean
199ded2 and this candidate. Full legacy QA is NOT claimed green.
The combined runner initially pointed Safra at the report-output directory;
its original hash-checked PDF was copied into that disposable fixture location,
and all seven affected Safra tests passed without application/test changes.
The final strict CSV-amount check was verified separately on both databases.
PostgreSQL was stopped after validation.

## Local performance (not production latency)

AMEX PDF parse 2.519 s; CSV parse 0.155 s; source pairing 2.732 s;
classification of 171 rows against 592 memory rows / 306 taxonomy definitions
0.150 s, 136 non-fallback results. SQLite atomic import including revalidation
3.636 s. Trace verified six transaction insert batches and zero per-row hash queries.
Natalia classification 0.700 s (baseline 0.342 s), still 64/122 versus 0/122.
B-Projects parse 0.290 s; native Fifth #5055 parse 0.148 s.

## Safety

No production access/writes/imports, deployment, main change, Render trigger,
schema change, historical rewrite, Setup change, online FX or external AI.
All financial test writes and failure injection used disposable databases only.
Scanned Fifth #5055 remains fail-closed; native #5055 and other banks remain protected.

NOT DEPLOYED — awaiting final release review.
