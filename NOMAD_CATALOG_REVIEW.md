# Frozen catalog review: 30 September 2026

Scope: read-only preclearance of the existing nine financial columns only.
This review does not authorize or enable migration, Repair, audit/snapshot DDL,
or changes to any existing repair value or precondition.

## Evidence binding

The private `NOMAD_Precheck_20260930_063557_UTC.json` remains outside Git.
Its complete canonical evidence digest, file digest, capture time, capture
release, PDF fingerprint, existing 24-precondition-set digest and catalog digest
are recorded in `nomad_precheck_bindings.EXTENDED_CATALOG_REVIEW`.
The capture release is historical provenance, not an additional release gate.
Runtime release authorization still requires exact equality of the two
server-side release SHA variables.

The evidence digest was recomputed successfully. All 24 hash results match the
unchanged frozen bindings; the monetary schema, target rows, dependencies,
repair-plan digest and reviewed event-trigger gate passed in that export.

## Catalog assessment

- Reviewed all 90 columns, 14 constraints, 32 indexes, 116 dependency entries,
  eight relations and the empty non-internal table-trigger result.
- The nine financial columns have no defaults. Eight are nullable;
  `rates.rate_value` is NOT NULL. The intended type conversion must retain this.
- Captured constraints are primary keys and unique constraints on identifiers
  and non-monetary keys. No inbound/outbound foreign keys, CHECK or exclusion
  constraints were returned by the scoped query.
- `idx_classified_usd_backfill` is the only captured index referencing the
  converted financial fields. It is a non-unique plain B-tree on `amount_usd`,
  `amount`, currency, rate type and date. PostgreSQL can rebuild this index for
  NUMERIC; neither its columns nor its uniqueness semantics are changed.
- Other indexes/defaults preserve existing ID, classification, Reviewed,
  SPLIT, statement and history behavior. No financial-expression default exists.
- All eight relations are ordinary persistent tables, RLS enabled and FORCE
  RLS disabled. Preserve those flags. No policy dependency is present in this
  captured scoped dependency set; this is not a global authorization audit.
- Dependencies are the captured namespace, composite-type, automatic relation,
  default and constraint dependencies. No rewrite/view, policy or function
  dependency appears in that set. Absence from `pg_depend` is not proof about
  dynamic SQL. The separate reviewed event-trigger definition gate remains
  unchanged and mandatory.

No captured object presents a new incompatibility for the nine type changes.
PostgreSQL type changes rebuild affected indexes and require ACCESS EXCLUSIVE
locking; this does not remove the execution transaction/locking/rollback gates.
Reference: https://www.postgresql.org/docs/17/sql-altertable.html

## Fail-closed boundary

The existing evaluator compares the complete live catalog's canonical digest
against the fixed reviewed digest. Added, removed or changed captured objects,
including OIDs, block. There is no automatic rebaseline or expectation refresh.
No query, authorization, connection, repair scope or write path changed.

This is the pre-migration baseline, not a baseline for post-migration NUMERIC
state. A successful read-only precheck is not permission to execute Repair.
The atomic execution guards (including separate audit/snapshot DDL review)
remain in place. Future execution must freshly validate its approved state
under its required locks before any write.
