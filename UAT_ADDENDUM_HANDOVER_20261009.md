# Areti UAT addendum — local candidate only

Baseline: 39aed4c853de9cf0f78d159dcfb64d25fa123792, preserving the completed explicit ex-Comerica read-only report match. Live baseline remains 43b6773f23ab7325431fba22b3d6915202d92cf3. No push or deployment in this task.

## Implemented and tested

1. THIRD REPORT columns: Bank, Account, Account Number, Import Date, Statement Closing Date, Currency, Closing Balance, Closing Balance USD. Content-sized table, reduced cell padding, no grouping, one-row data and preserved account ellipsis/title.
2. Compact native/USD balances use two decimals for display only. Exact stored values, eligibility and USD arithmetic are unchanged; unverified precision stays excluded.
3. Closing-date freshness is inclusive 0–30 calendar days in Cyprus. Older, unknown/invalid and future dates remain red. Import date stays black. The same helper controls compact, printable and workbook display.
4. Compact sort: Bank, Account name, closing date oldest first, then account number for deterministic ties. Unknown dates last.
6. The exact original Woking Way 2 (1).pdf was retrieved from Areti's email and is SHA-256 identical to the previously tested Woking Way 2.pdf (private evidence). Source values/counts/signs/reconciliation unchanged. Screenshot proves the selected Setup bank is Fifth Third (ex-Comerica); preview rejected that explicit label. Added only its normalized spelling to the allowed bank labels. Full exact account/USD/source movement/count/reconciliation checks remain authoritative.
7. Prior report matching fix preserved; existing Comerica and ambiguity guards retested.

## Item 5 — exact live exclusion still blocked

Areti screenshots identify USD rows for Sapphire Chase and Comerica account ending 5055. Direct read-only Latest Import Balances navigation in the connected live browser reaches the sign-in screen. Status, Verification and Applied FX for 5055 are UNAVAILABLE; no current workbook was obtainable. Do not claim their values or infer the exact stored-state exclusion.

Existing report code: complete, exact and unambiguous USD balances use identity conversion (no rate required). Therefore a missing configured FX rate alone does not explain a USD 5055 row. Required metadata, source/setup ownership labels, precision/storage provenance, ambiguity and compact eligibility can exclude it. Exact current reason requires the authenticated read-only report/workbook. Sapphire Chase is normalized to CHASE and the existing liability/card convention excludes it from compact USD/total pending approved convention, independently of rate availability. Other possible metadata failures for that live row are not independently observed.

No FX policy change, invented rate, production inspection/query/write, data repair, remapping, schema change, BOC conflict work, Revolut currency mapping, LOC/OCR or performance work.

## Verification

21 targeted tests PASS: exact fourth-source preview safeguards and upload UI; Pending/History/exact transaction multisets/duplicate repeat; both existing Fifth Third sources; six report bank-match/FX/identity cases; columns/formatting/sort/30-day boundaries; compact and backend display; THIRD and Reports read-only routes. Two SQLite report/storage/export integrity groups PASS. PostgreSQL checks not run in this addendum; no production DB connection.

Original rejection and initial unrelated command quoting/test ordering failures retained in private logs. Test correction compares date/Decimal amount multisets because Pending orders rows by date and SQLite numeric spelling drops trailing zeros; count, dates and exact amounts still enforced. No application code changed for that test mismatch.

Syntax and final diff review required before commit. Business/live UAT pending. Candidate local functionality is prepared; complete item 5 remains BLOCKED by the unauthenticated report session, not treated as passed.
