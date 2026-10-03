# Safra August import and Latest Import Balances

Branch: `codex/safra-balances-report`.
Base: `dfa218f944bb7204df179b9a7e4ce0f31d5d8c74`.

## Reproduced cause and correction

The August PDF fails in the account ending 4000, after its sole booking.
The next extracted line is a wrapped description. It has no booking date or
amount tokens and therefore reaches `_parse_safra_pdf_text`'s unknown-content
rule while the transaction section is still open. All five no-booking pages
already pass the production lexer; they are not the cause of the failure.

`safra_layout.booking_text` reconstructs wrapped descriptions only when PDF
word geometry establishes immediate adjacency and alignment within the
description column. It preserves the complete text, placing the continuation
before the value date and amounts. Financial/date tokens, control lines,
misaligned text, gaps and absent/ambiguous geometry do not gain continuation
status. The existing strict lexer, Decimal arithmetic, account/IBAN checks,
reconciliation, duplicate controls and section persistence remain unchanged.
Only the recognized Safra dispatch invokes this normalization.

The external August PDF is used locally and is not committed. The optional
`SAFRA_AUGUST_EVIDENCE_PDF` QA variable enables the exact reproduction, six
account sections, five empty sections, exact amounts and isolated persistence
checks. Geometry and rejection tests also run without confidential evidence.

## Account snapshot

The account population is the current Setup account list. One SQL window query
ranks completed, balance-backed imports independently by account ID, newest
import timestamp, then highest import ID. Statement dates and opening/closing
balances come from the same selected balance/import join. The selection uses
the existing completed-history rule: source transaction count excludes split
children, and zero-row imports require complete period/account/currency
metadata and equal opening/closing balances. Transaction bodies are not loaded.
Older imports and incomplete newer attempts cannot displace a completed import.

Matching uses the stored account name, bank, currency and number, including
Safra's canonical IBAN representation for labeled Setup numbers. Setup accounts
without a matching completed import are displayed once with NO IMPORT, empty
statement/balance fields, and no contribution to the total. Historical imports
and removed Setup accounts are not the account population of this snapshot.

One additional batched rate lookup reuses the application's existing approved
FX functions at each selected statement's end date. USD balances remain exact.
Non-USD values use the existing Decimal conversion policy. Missing/invalid
dates or missing/nonpositive rates yield NOT AVAILABLE, a visible warning,
and exclusion from the Decimal grand total. No rates are changed or fetched
from the internet. The report explicitly discloses the inherited earliest-rate
policy when no configured rate precedes a statement month.

## Presentation and limitations

Authenticated navigation includes Latest Import Balances. The renderer provides
an HTML preview and downloadable standalone HTML with an A4 landscape print
stylesheet, repeating table headers, generation timestamp, report note,
browser Print / Save as PDF button, warnings and eligible-row USD total.
All data-derived HTML is escaped and limited to the requested metadata.
No native PDF dependency is added.

There is no existing deterministic business freshness rule, so statuses are
IMPORTED and NO IMPORT. Legacy import timestamps without timezone information
retain the existing "timezone unverified" presentation. This change does not
repair historical records, change SQLite's existing REAL storage compatibility
path, or refresh frozen NOMAD source/release approvals.

## QA

- `_qa_safra_august_balances.py`: original failure, layout boundaries, unknown
  content rejection, exact six-account evidence and isolated import/duplicates.
- `_qa_latest_import_balances.py`: independent latest dates, ID ties, incomplete
  imports, NO IMPORT, two-query bound, approved FX, unavailable conversion,
  exact totals, escaped printable output, actual UI dispatch, no writes, and
  local PostgreSQL NUMERIC/timestamp semantics in read-only transactions.
- Historical source comparisons restore only the exact hash-pinned Safra
  dispatch and UI additions. Whole-file equality with the pinned production
  baseline continues to reject unrelated changes. Behavioral tests use the
  current implementation.

All database fixtures run in disposable SQLite or loopback PostgreSQL databases.
Final targeted QA: 3/3 PASS (August, report, recovery-page route preservation).
Full serial coverage: 81/81 PASS after reruns. The first traversal completed all
81 scripts with 74 passing; six failures were missing local QA dependencies
(`pglast`, `reportlab`, `xlwt`/`xlrd`), and one historical whole-file recovery
assertion needed the exact hash-pinned UI compatibility adapter. All seven
subsequently passed. The final targeted run used the finished implementation.
Browser print-media inspection passed without clipping, and the print button
invoked browser printing. No production database or service was contacted.
