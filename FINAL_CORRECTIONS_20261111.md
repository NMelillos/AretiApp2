# Final correction pass — bounded partial candidate

Preserved starting candidate: fb4dadacbab7138a58fdf410d61f371eeb7651e3.
Isolated branch: codex/areti-final-corrections-20261111. No production connection,
write, import, repair, deployment, schema, Setup, environment or configuration
change in this pass. Do not deploy this partial candidate as a completed package.

## P0: exact October Natalia source — BLOCKED

The exact email attachment was obtained and the current failure reproduced:
`Could not extract statement rows from this PDF.` Existing Revolut readers expect
English personal or Russian Business structural labels, English/Russian dates and
their supported money formats; the source uses Greek headers/dates and decimal
commas. More importantly, this is not an EUR-only document. It contains eight EUR
pages and two USD pages for the same Natalia IBAN. The EUR pages reconcile 115
completed transactions: 6.90 + 33136.73 - 22650.96 = 10492.67. The USD pages
reconcile seven completed transactions: 1073.78 - 549.47 = 524.31. Pending and
reverted sections are separate and excluded from these source-only counts.
Original pages 1/8/9/10 were visually reviewed; every page's text was inspected
by the section/date/running-balance diagnostic. This is source diagnosis, not
successful application parsing/import validation.

No parser, import or account-selection code changed. The existing single-account
import boundary applies the selected Setup currency to a frame; supplying a
mixed-wallet frame or silently dropping USD would be unsafe. The requirement
explicitly rejects currency ambiguity and excludes the full wallet redesign.
An explicit per-wallet selection/import-identity policy is needed before the
exact mixed PDF can be safely supported. No global EUR inference, Setup account
duplication, guessed wallet mapping or partial production import was attempted.

## P4: B-Projects Setup 968 report link — implemented and locally tested

The exact original September Russian Business source hash matches the stored
statement. Its complete financial metadata and all eight dated/signed source
movements match the stored records. Current Setup currency is blank. The report's
exact currency join therefore previously returned NO IMPORT although the EUR
balance exists.

Add a read-only report helper for this one existing Setup identity. The original
source hash, metadata digest and full dated/signed movement multiset are pinned
without committing the private source or financial snapshot. Verify exact bank,
IBAN, owner, Setup ID, one Setup identity, stored source label/currency, eight
source rows and history count. Reject changed metadata/activity, SPLIT source
rows, duplicate Setup identities and any other stored currency for this IBAN.
Resolve EUR only in the report query; do not persist currency or change FX rules.
This is deliberately not a multi-currency wallet resolver. A future second
currency rejects the link rather than silently assigning that wallet to EUR.

The read-only 43-row replay now exposes the existing EUR 997.23 balance closing
2026-09-30 with its actual historical transaction/balance timestamps. Existing
FX resolves USD 1116.90; no rate is invented. Only this report row changes. The
other 42 rows are identical. Deposits eligible sum becomes USD 1706011.18;
Debts remain USD -789715.99. Four Eurasian rows remain NO IMPORT/source-required,
so the final Grand Total remains NOT AVAILABLE. No historical date is refreshed.

## Protected parent work

Sapphire September/October liability signs and strict balance-only behavior,
Credit Line USD identity conversion, compact/aligned layout, section-total
placement and Grand Total completeness guard remain unchanged and were covered
by relevant affected regressions. No additional compactness redesign. Transaction
Memory remains AWAITING REAL USER VERIFICATION — NO CHANGE. Balance Trend Graph
remains NEW FEATURE REQUEST — NOT INCLUDED. Eurasian 964/965/966/967 remain
SOURCE DATA REQUIRED; historical March balances were not copied forward.

## Executed validation and limitations

- Seven new linkage tests: six passed initially. The duplicate-Setup test's
  INSERT omitted an explicit column list, so the synthetic duplicate did not
  represent the intended identity. Correct only that test fixture and rerun the
  affected test: PASS. Original failure preserved in private evidence. No
  application guard weakened. Seven distinct new cases now PASS.
- 36 affected regressions PASS, zero failures/errors/skips: original Russian and
  supported English Revolut, strict account/source rejection, exact two Sapphire
  originals, duplicate UI/no-op, rollback, reconciliation, balance/date
  preservation, liability signs, debt whitelist, totals and incomplete Grand
  Total guard. Old whole-report assertions requiring exactly five missing rows
  are superseded by the new explicit 42-unchanged/one-proven-row delta test;
  existing tests are not edited. No repeated full legacy QA.
- Disposable PostgreSQL 18 PASS: full snapshot replay, enforced read-only report
  sessions, source linkage, unchanged FX, four missing rows, withheld Grand
  Total, ambiguity rejection, rollback and table fingerprints preserved. Windows
  restricted-token startup failure retained; permitted isolated retry passed.
  Test database dropped and owned loopback cluster stopped.
- Syntax 200 Python files PASS; diff/protection PASS. 59 other application files
  byte-equivalent to the preserved candidate. All existing report definitions
  except snapshot unchanged; all existing tests unchanged. Helper SQL SELECT only.
- No new live production verification or business UAT. No claim that the exact
  October file can import. No deployment-ready claim while P0 remains blocked.

Private originals, diagnostic images/text, report snapshots, test logs and
original failure evidence are under sibling qa-final-corrections-20261111;
they are not committed. Existing private source evidence is reused read-only.

IMPLEMENTED/TESTED: Setup 968 report linkage only, on top of preserved fixes.
DEPLOYED: NO. DATA-REPAIRED: NONE. USER-ACCEPTED: pending.
RELEASE: PARTIALLY FIXED — exact October Natalia mixed EUR/USD import requires
an explicit wallet-selection/import-identity policy; do not deploy.
