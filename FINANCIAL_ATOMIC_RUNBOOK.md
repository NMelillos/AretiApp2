# Snapshot-backed financial remediation

LOCAL IMPLEMENTATION ONLY. No deployment or production execution has occurred.
The command is not imported by app startup and has no browser button or endpoint.
It uses the existing server-side application database configuration; it never
prints a connection string or reads credentials from command-line arguments.

## Scope and snapshot

Convert only the nine columns in financial_schema.FINANCIAL_COLUMNS to
unconstrained NUMERIC. Preserve NULL, nullability, defaults and constraints.
Every legacy monetary value is decoded from PostgreSQL IEEE send bytes, not its
potentially shortened text representation. Unknown source values remain
LEGACY_BINARY_UNVERIFIED; no guessed cents or global scale are applied.
NUMERIC does not retain IEEE negative-zero sign, but its original bits are kept
for exact reverse. Monetary equality is Decimal equality, without tolerance.

The command pins the digest of the private approved 18-field/11-row manifest.
That private file contains the complete 24 exported preconditions and PDF hash.
It is not part of Git or deployment artifacts. Only amount and source-supported
balance fields can be corrected. amount_usd, fx_rate and split_original_amount
retain their original exact stored values. No delete/re-import or metadata edits.

Before financial DDL, the locked transaction inserts a uniquely identified,
losslessly tagged snapshot into financial_recovery.snapshots. It includes all
rows of the three financial tables, all nine monetary columns, original IEEE
bytes, financial schema, logical public catalog, complete row versions, imports,
taxonomy, change log, accounts and classification memory. The approved manifest,
all hashes, fingerprint, conversion provenance and actor are bound into evidence.
financial_recovery.events records APPLY and REVERSE as separate append-only events.
Original import and transaction timestamps are never changed.

The dedicated schema/tables deny PUBLIC access and reject unreviewed grants.
ALWAYS triggers reject UPDATE, DELETE and TRUNCATE, including replica-mode DML.
Stored payload hashes detect corruption. A database owner/superuser can still
alter/drop guards or the database itself; this is not external WORM storage or
disaster recovery. Do not represent it as protection against total database loss.
No snapshot is visible through normal application UI or exports.

## Atomicity and reverse

An advisory transaction lock plus ACCESS EXCLUSIVE locks protect the financial
tables and captured dependencies. Locks have a 3-second acquisition limit and
statements a 120-second limit. Unknown FK dependencies, RLS, user triggers/rules,
schema changes, stale hashes or expected-old differences abort before DDL.
Unreviewed DDL event triggers, public functions and inherited/partitioned target tables also block;
their behavior is not inferred from dependency hashes.
All historical conversion, 18 corrections, reconciliation and audit happen in
one transaction. Errors and interruption roll back that transaction, including
the snapshot insert. After rollback the original database remains unchanged.

Pre-commit checks require exact source reconciliation of six sections/nine rows,
balance equations, counts, financial values and preserved metadata/catalog.
After COMMIT a separate read-only connection checks the recorded post-state and
reruns the existing Compare core. Any new writer or failed read-back prevents a
success report. An uncertain COMMIT never triggers an automatic retry.

Reverse requires the recorded post-state hash and exact current row versions.
It refuses even same-value intervening updates. Inside one locked transaction,
converted columns first receive and verify their exact snapshot values while still
NUMERIC. Only those proven preimage values are encoded into the original types;
current repaired values are never blindly cast. Negative zero is restored from
the snapshot, without staging NULL or weakening constraints/defaults. Original
IEEE bytes, catalog and metadata must match before
commit. Constraint failures roll back rather than being ignored. Reverse has its
own immutable event and independent fresh read-back; repeated reverse is zero-write.
Do not refresh original production hashes to bypass a stale check.

## Future server process - NOT EXECUTED

Prerequisites: separately authorized deployment of reviewed source; private PDF
and approved manifest; executable authorized server environment; existing TLS
database configuration for the pinned Supabase project; isolated validation of
its actual dependencies/privileges and connection limits. Direct connection is
preferred for migration. Session pooling on 5432 is supported only after review;
transaction pooling and routing overrides are rejected. Configuration is never
rewritten automatically. Keep normal writers quiesced through verification/UAT.

Create a private operator directory outside the application checkout. On POSIX
it must have mode 0700; use equivalent restricted ACLs on Windows. The plan file
contains hashes and operator binding, not confidential monetary values. It is
created exclusively, never overwritten. Configure a server-only random
NOMAD_ATOMIC_TOKEN (at least 32 characters) in the private operator environment;
do not send it through chat. Apply/reverse require a TTY and hidden token entry.

```sh
set +x
python -B financial_atomic_command.py preflight \
  --manifest "$NOMAD_MANIFEST" --pdf "$NOMAD_PDF" --plan "$NOMAD_PLAN" \
  --operation-id "$NOMAD_OPERATION_ID" --actor "$OPERATOR_ACTOR"
```

Review the exact plan digest and live preconditions before separate execution
authorization. A mismatch is a STOP, not permission to regenerate the allowlist.
The first command is read-only; it does not create the database snapshot yet.

```sh
# DO NOT RUN until separately authorized.
python -B financial_atomic_command.py apply \
  --manifest "$NOMAD_MANIFEST" --pdf "$NOMAD_PDF" --plan "$NOMAD_PLAN" \
  --operation-id "$NOMAD_OPERATION_ID" --actor "$OPERATOR_ACTOR" \
  --execute --confirm-plan "$APPROVED_PLAN_SHA256"

python -B financial_atomic_command.py verify \
  --manifest "$NOMAD_MANIFEST" --pdf "$NOMAD_PDF" --plan "$NOMAD_PLAN" \
  --operation-id "$NOMAD_OPERATION_ID" --actor "$OPERATOR_ACTOR"
```

Verify emits the post-state hash, not financial data. Run authorized read-only
reports/XLSX, Compare, duplicate, Reviewed, SPLIT and History UAT before reopening
writers. The command's automatic database verification is not a substitute for
production UI/report acceptance. It does not log in as Areti or Import a statement.

```sh
# Separate recovery authorization is required; never retry writes blindly.
python -B financial_atomic_command.py reverse \
  --manifest "$NOMAD_MANIFEST" --pdf "$NOMAD_PDF" --plan "$NOMAD_PLAN" \
  --operation-id "$NOMAD_OPERATION_ID" --actor "$OPERATOR_ACTOR" \
  --execute --confirm-plan "$APPROVED_PLAN_SHA256" \
  --confirm-after "$APPROVED_POST_STATE_SHA256"

python -B financial_atomic_command.py verify --reversed-state \
  --manifest "$NOMAD_MANIFEST" --pdf "$NOMAD_PDF" --plan "$NOMAD_PLAN" \
  --operation-id "$NOMAD_OPERATION_ID" --actor "$OPERATOR_ACTOR"
```

If COMMIT outcome is uncertain, keep writers closed and inspect/verify on a fresh
connection before deciding whether apply or reverse committed. Never cast NUMERIC
back to REAL as a shortcut. Any subsequent legitimate change blocks reversal and
requires a new, explicitly reviewed recovery design preserving that change.

## Local acceptance evidence

_qa_financial_atomic.py uses a generated six-page synthetic PDF, nine statement
transactions, 18 discrepancies over 11 rows and 24 actual readiness hashes.
It checks every injected rollback stage, interruption/uncertain commit, exact
Decimal cents including a synthetic 148351.30 debit, XLSX read-back, duplicate
recognition, Reviewed/classification preservation, unrelated SPLIT/FX/large/NULL
values, immutable DML guards, repeated apply/reverse, restored IEEE bytes and
newer-change refusal. _qa_financial_atomic_command.py verifies read-only default,
private manifest/source/operator binding and refusal before DB access without
deliberate approval. All production evidence stays outside Git.

No production execution is authorized by this document.
