# Temporary NOMAD Final Repair

Location: Setup > NOMAD Final Repair, main authenticated username exactly Areti.
THIRD, anonymous users and other usernames have no access. No standalone route,
query-parameter identity, automatic precheck or automatic repair is provided.

## Operator steps after deployment

1. Refresh and sign into the main application as Areti.
2. Open Setup and upload the original PDF in NOMAD Final Repair.
3. Press RUN NOMAD FINAL PRECHECK once.
4. If blocked, stop and report only the safe reason shown. Do not change the PDF,
   scope or database to bypass the check.
5. Only after PASS, type CONFIRM NOMAD REPAIR exactly and press EXECUTE NOMAD
   CONTROLLED REPAIR once. Keep other writers inactive through verification.
6. If verification fails or the connection is interrupted, do not retry. The
   operation may have committed; authorized independent verification is required.
7. Accept completion only when NOMAD COMPLETED AND VERIFIED appears. Then check
   normal reports/export, Reviewed, SPLIT and Import History without re-importing.

This deployment does not itself run any of these stages. Removal of the
temporary interface is a separate step after verified successful UAT.

## Runtime evidence contract

The PDF is held in memory and checked against the approved fingerprint. No PDF
or private exported manifest is embedded in Git or written to a local file.
The existing parser/Compare performs deterministic section/transaction matching.
Exactly the approved seven transaction amount fields and eleven balance fields
must differ. Matching rows 5912/5913 and every other field are excluded from the
repair plan. Unexpected differences, counts, missing identities or SPLIT
dependencies block precheck. Monetary values come from the PDF, not screenshots.

Fresh hashes bind current live metadata/dependencies at precheck. They do not
assert that metadata is unchanged since the earlier diagnostic export. Current
validated metadata is preserved exactly; stale precheck state blocks execution.

The plan is server-side session state, invalidated by a new session, file change,
sign-out or leaving Setup. One attempt consumes it before invoking the reviewed
atomic core. The core's persistent operation identity and source reconciliation
prevent a second successful application after refresh or in another session.

The reviewed core is unchanged: table locks, all-state checks, append-only
snapshot, exact legacy conversion, 18 corrections, reconciliation and audit share
one transaction. Independent fresh read-back is required before success. The
snapshot intentionally retains financial evidence inside the restricted recovery
schema; it never contains the PDF bytes. In-database snapshots are not external
disaster-recovery backups and do not constrain a database owner/superuser.

Live unreviewed functions/event triggers, schema differences, missing privileges,
project/TLS mismatches or changed dependencies remain fail-closed blockers.
The UI does not disable or bypass them. No exchange rate or USD recalculation is
performed. There is no general database editing, arbitrary SQL, or editable ID.
