# Four-item UAT closure candidate — local only

## Baseline and scope

Based on approved `314de3bc02b2d9645948660a72c77a763be42713` in separate
`codex/areti-final-four-uat-20261026`. Direct Render evidence identifies that
same commit LIVE on manual deployment `dep-dbfm0c8ae00c738j8j0g`; Auto-Deploy
is OFF. Remote main was read back at the approved baseline. This task neither
publishes a replacement main nor deploys anything.

## Proven causes and minimal corrections

### Natalia: DATE CORRECT — EXPLAIN TO ARETI

Enforced production READ ONLY evidence for the exact September English PDF:
existing completed balance persistence and transaction-history timestamp are
12 October 2026 13:27:37 UTC. The later source-bound report-link audit is
26 October 2026 15:20:44 UTC. All required native fields are already complete;
balance imported_at and updated_at remain historical. The history still has
167 transactions. Earlier retained source comparison also established complete
matching values before this UAT link. Linking was not financial completion.

No timestamp/persistence/selection change is needed. Only the existing success
messages now explicitly explain that confirmation of an already-complete balance
does not create today's completion date. Tests distinguish genuine later missing
field completion (actual source-bound completion timestamp), historical completion,
linkage and full no-op repeats. Transaction date and source closing date remain
independent.

The existing Natalia source-bound EUR visibility exception is unchanged. Setup
currency/rate settings remain blank. It does not model other currency wallets or
force EUR globally. USD-wallet reporting requires the separate architecture;
missing FX remains unavailable and excluded under the existing rules.

### Fifth Third Woking Way Flexline

The original positively identified LOC PDF prints a positive amount owed. The
dedicated two-value metadata parser previously retained that positive sign.
Only its New Balance sign is normalized to negative debt with -abs, so an
explicitly negative source is not negated twice. Statement Date is unchanged.
The route still stops before generic activity parsing and persists zero
transactions; no payment, minimum payment, finance charge, opening balance or
period start is reconstructed. Account identity checks, record function,
locks, source proof, idempotence and rollback are unchanged.

This deliberately incomplete two-value record remains subject to the existing
report completeness/FX/total and exact debt-whitelist rules. Neither missing
reconciliation data nor eligibility is invented. Conflicting existing positive
metadata fails closed; it is never silently repaired. Read-only production
inspection found no stored metadata row or transaction for this exact source.

### AMEX

The original Screen Reader Optimized PDF's amount-owed convention was retained
as positive metadata. The parser now first validates the printed reconciliation,
then represents debt/charges negatively and payments positively. The entire
four-field summary changes sign consistently, preserving its exact arithmetic.
A fully signed negative source is checked and preserved without double-negation;
inconsistent sign/summary evidence is rejected.

The exact already-imported original CSV remains mandatory identity evidence.
Direct production inspection confirmed the completed 81-row CSV import and
matching company, bank, account base and card-member identities. Current Setup
uses the same account identity under its current ID. No manual-selection bypass
was introduced. CSV validation, account matching, record and proof functions are
unchanged. The PDF creates no transaction rows. Existing positive historical
metadata cannot be overwritten automatically. No exact-source metadata or PDF
transaction row was present in the read-only production check.

### Latest Import Balances

Content-width table cells could ignore CSS header-width hints; full date headings
remained on one line. Fixed frame allowances also left excess empty space.
Only screen padding/line height/header wrapping/background and frame height
change. Explicit two-line headings preserve their text/order. Print document,
Excel, snapshot query, balance selection, row population, dates, freshness,
native/USD values, FX, sections, whitelist and totals functions remain identical.

Installed Edge, fresh private profiles, synthetic local HTML and disabled DNS:
Deposits width 744.67 to 573.91 px; Debts width 751.33 to 585.02 px. Both sampled
tables are 50 px tall versus 55 px before, with identical row counts and cell
content. Before/after screenshots were visually inspected. This verifies the
local synthetic layout, not Areti's authenticated live browser/business UAT.

## Executed validation

- Final integrated package: **85/85 PASS**, zero failures/errors/skips, including
  the existing targeted release matrix, nine new focused cases, date/report
  regressions, Family/Charity, BOC/Revolut and Pending/SPLIT protection.
- Disposable PostgreSQL 18: PASS, writer fence, raw AMEX reconciliation,
  strict account/source identity, zero PDF transactions, CSV preservation,
  LOC concurrent-copy idempotence, rollback, Natalia linkage/original dates.
  The owned loopback cluster is stopped after the rehearsal.
- Existing report integrity: PASS on disposable SQLite and actual PostgreSQL,
  including provenance, aliases, Safra identity normalization, selection,
  date/export/total agreement and no-write reads.
- Syntax: **192 Python files PASS**. Diff and scoped-function checks PASS.
  **54 other application files unchanged** after Git CRLF normalization.
  Financial/report calculation functions, all transaction parsers, database,
  protected NOMAD repair, Family, SPLIT, Pending and configuration are unchanged.

Initial diagnostic failures are retained privately: Python 3.15 versus existing
3.12 binary dependencies (safety stopped execution), a new HTML cell regex matching
thead, old header assertions rejecting br wrapping, and Windows pg_ctl's inherited
output pipe. The runtime/harness fixes and presentation-only assertion updates
are justified by direct evidence. No financial assertion or safeguard was weakened.
The last complete integrated run is green; unrelated historical full-QA debt is
not represented as green or rerun for this bounded task.

Private runners, original-source proofs, raw logs, snapshots and screenshots are
in workspace sibling `qa-final-four-uat-20261026`, outside Git. No credentials,
private financial statements or raw production records are committed.

## Release and UAT

Implemented/tested locally; not deployed, not financially repaired and not yet
business accepted. No data/schema/Setup/environment/configuration change is
required. No production import, balance write, timestamp rewrite, classification
or SPLIT change occurred. Separate deployment authorization is required.

After deployment, test only Natalia date explanation, LOC date/negative debt with
zero transactions, AMEX original PDF/CSV proof/negative debt with zero PDF
transactions, and report compactness. Full Revolut wallet architecture remains
a separate design task. No other closed business functionality was reopened.
