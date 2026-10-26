# Three-flow UAT follow-up and report presentation

## Boundary

Isolated branch `codex/areti-uat-three-flows-20261026`, based on
`a67b8db80f903a6c7e0ecacd238c6241da220d35`. Direct Render evidence confirms
that baseline LIVE on manual deployment `dep-dbfe638m7kps738oihpg`.
This task authorises local changes and publication of this isolated branch only.
No main update or Render deployment is included.

## Confirmed causes and narrow corrections

### Natalia Revolut

The operator confirmed the exact original English September PDF. Enforced
production READ ONLY inspection found one current matching Setup account with
blank currency, its existing 167 transactions, and complete matching EUR balance
metadata. Source/account validation and all transaction values matched. The
reported repeat message was therefore a true no-op, but the report's exact
Setup-currency join could not associate EUR balances with the blank Setup row.

The existing explicit balance-only confirmation now appends a source-bound
report-link audit to the existing balance notes after the unchanged strict
source, account, transaction and concurrency checks. For an already-complete
record, only notes change: no financial field, history event, balance persistence
timestamp or updated timestamp is rewritten. Repeats remain full no-ops.
The UI distinguishes this linkage from an import or balance completion.

The read-only report accepts this proof only for Natalia's exact current
bank/owner/IBAN and EUR source, with one unambiguous compatible Setup identity,
matching native metadata fingerprint, actual history and source-row count.
Setup remains blank; there is no permanent currency remap, multi-currency wallet
implementation, generic blank-currency fallback or invented FX rate.
Existing latest/incomplete-balance selection remains intact. Other-currency
records are neither changed nor collapsed into EUR. Missing/unsafe FX still
displays NOT AVAILABLE and remains excluded.

After a separately approved deployment, Areti must confirm balance-only once
for the same PDF to create the verified linkage. Deployment itself does not
repair or backfill production data. The original Balance Import Date remains
historical because these balances were already complete.

### Woking Way Fifth Third Flexline LOC

The original PDF and visual first-page review confirm the labelled Statement
Date and New Balance. The baseline generic parser produced one false activity
row from Minimum Payment Due. The new explicit Flexline metadata route runs
before generic preview/transaction parsing and stops that path entirely.

Only the approved combined legacy/current account identifier, exact Woking Way
owner, Fifth Third successor bank and USD Setup account qualify. The action
records Statement Date as period end and New Balance as closing balance, plus
normal identity/history/audit metadata. No period start, opening balance,
payment, finance charge or transaction is inferred. Unsupported/mismatched
Flexline sources fail closed. Same-file and same-period document copies are
idempotent; a facility-row lock serializes PostgreSQL confirmations.

The report's accepted completeness/eligibility/bucket rules are unchanged.
Consequently this intentionally two-value record remains INCOMPLETE and is
excluded from USD totals; no reconciliation or missing financial value is
fabricated to promote it. Existing financial history blocks an automatic rewrite.

### AMEX

The previous UI required a generic preparation click before a second generic
confirmation action. Before CSV evidence it displayed no import action.
The source/CSV validation and persistence already worked and remain unchanged.
Both uploads now trigger read-only validation and expose the explicit
`Import AMEX Balance Metadata` action, disabled until evidence and confirmation
are available. The guidance explicitly says to use the Screen Reader Optimized
PDF. CSV evidence and PDF activity create zero new transactions.

### Passed report: presentation only, apart from the authorised Natalia lookup

Expanded blue guidance and fixed-height empty space are reduced; all policy
paragraphs and warnings remain available in a collapsed guidance section.
Screen tables use smaller padding, content-driven widths and wrapping headings.
The downloadable print document and workbook functions are unchanged.
Compact model, sorting, 30-day freshness, FX, classification, eligibility and
totals calculations are unchanged. Source/AST comparison proves the report's
entire calculation/selection tail is identical to baseline.

## Executed QA

- Final combined acceptance: **76/76 PASS**, zero failures/errors/skips.
  Includes the original 38-package matrix, 13 new UAT tests, Family/Charity,
  BOC closure, Revolut, actual Pending/SPLIT classification guards and read-only
  navigation checks.
- Disposable PostgreSQL 18: PASS, including strict writer fence, AMEX original
  CSV preservation, source completion, forced rollback, concurrent LOC copies,
  zero LOC/PDF/CSV transaction writes, Natalia linkage and original timestamps.
- Existing report integrity: PASS on disposable SQLite/PostgreSQL, including
  numeric/storage provenance, aliases/ambiguity, Safra identities, latest
  selection, unchanged totals, dates, exports and no-write report reads.
- Syntax: **191 Python files PASS**. Diff checks PASS.
- Static preservation: **53 pre-existing application files unchanged**, including
  app.py, database, parsers, financial controls, protected NOMAD repair, Family,
  SPLIT, Pending, report policy and dependency files. AMEX non-UI functions and
  print/workbook functions match baseline exactly.

Original failures are retained privately, including the old generic LOC preview,
AMEX UX reproduction, unavailable-USD test representation correction, a harness
class-name/SQL quoting error, an inherited historical scope assertion and the
introduced PostgreSQL parameter/literal-percent issue fixed before final tests.
The date-guidance test now checks unchanged policy text inside its new expander;
it no longer requires an expanded blue info box. No financial assertion or
source/account safeguard was weakened.

Private evidence/runners are in workspace sibling
`qa-uat-three-flows-20261026`; PDFs, CSVs, production snapshots and raw logs stay
outside Git. Children use the existing credential allowlist and enforced network,
credential-file and disposable-database boundaries. The owned PostgreSQL cluster
was stopped after tests. No production financial write, import, repair, schema,
Setup, environment or configuration change occurred.

This is targeted acceptance, not a claim that the unrelated historical full QA
inventory is green. Screen CSS and actual Streamlit navigation/actions were
tested; final browser readability and business acceptance remain Areti UAT.
No new browser runtime was installed. Remote publication requires direct ref
readback; local HEAD alone is not publication evidence.
