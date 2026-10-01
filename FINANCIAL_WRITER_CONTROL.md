# Controlled repair writer barrier

No deployment/startup action creates the fence or executes remediation. The
existing authenticated main Areti session and deliberate confirmation are
required. All frozen values and hashes remain unchanged.

## Write coverage

`db.PostgresCursor.execute/executemany` guard application mutations with shared
transaction advisory lock 617294382 and a fresh READ COMMITTED fence query.
Existing SERIALIZABLE/SPLIT saves retain their isolation; their fence query uses
a separate read-only lease from the same application pool after lock acquisition,
so a stale financial transaction snapshot cannot hide a committed blocking fence.
This covers db.py imports, balances, rates, edits, backfills, classifications,
Reviewed, SPLIT, memory, history, settings and reset helpers; split_editing,
import_history and safra_history use that same connection/cursor wrapper.
PostgreSQL syntax-tree classification guards mutating CTEs, SELECT INTO,
lock-taking SELECTs, unknown/mutating functions and multi-statement execution.
Read-only CTEs and ordinary SELECT/SHOW reads do not acquire the writer lock.
There is no client-controlled exemption. Comments and string contents are not
treated as SQL operations; unsupported syntax remains guarded.

The raw atomic apply/reverse helpers require the same writer guard unless called
by the dedicated reviewed controller. The separate financial_remediation helper
already rejects non-loopback/non-QA databases. Raw connection use by read-only
diagnostics does not authorize mutation. Direct SQL by infrastructure operators
or old application binaries is not made cooperative by an advisory lock: all
application instances must run this release before enabling Repair.

## Durable objects and reviewed DDL

The controller creates only financial_control.fence (an append-only ledger),
its identity sequence, and its mutation-rejection function/trigger. PUBLIC has
no schema/table/function privileges. Every transition records repair ID, release,
frozen catalog binding, actor, phase and UTC database timestamp; the first
REPAIR_IN_PROGRESS event supplies the immutable start time. No timeout clears it.

The exact reviewed Supabase ensure_rls body enables RLS only on objects in public;
financial_control and financial_recovery are outside that schema. The reviewed
PostgREST watcher only notifies schema reload for these DDL commands. Extension
watchers' tags do not match these operations. No provider trigger, policy, role
or application-table RLS setting is modified. The seven-trigger frozen identity,
definition and dependency digests must match before control/audit DDL.

The eight reviewed public tables retain RLS enabled and FORCE disabled with no
policies (no pg_policy dependencies in the frozen catalog). Execution checks the
existing session identity can read complete rows without changing privileges.
The public ensure_rls function OID is accepted only alongside its frozen trigger
function definition hash; other non-extension public functions remain blocked.

## Ordering and failures

The dedicated controller holds an exclusive SESSION advisory lock across the
short IN_PROGRESS commit, frozen precheck, locked repair transaction, commit and
independent verification. The committed repair, migration, audit and
REPAIR_COMMITTED_UNVERIFIED event are one transaction. Failure before that commit
rolls all repair changes back. A live confirmed pre-write abort can record NORMAL;
uncertain outcomes/disconnects cannot automatically reopen writers.

Independent verification uses another connection. Only a successful read-back
permits a separate committed NORMAL/verified event and advisory unlock. Physical
controller connections are closed rather than returning session locks to the
application pool. Disconnect/restart loses the advisory lock, not the ledger.

## Explicit recovery

`nomad_controlled_repair.verify_and_release` requires main Areti authorization,
the current approved release and deliberate verification confirmation. It does
not repeat Repair. It checks the intact snapshot/APPLY audit, complete financial
state and source reconciliation on a separate connection before clearing a
blocking fence. It is not a UI/HTTP endpoint and never runs automatically.
Missing APPLY evidence or failed read-back stays blocked for operator review.
Never manually delete the ledger, reset its state, or blindly repeat the repair.

The existing Repair section reads status using a separate READ ONLY / REPEATABLE
READ connection. It reports the durable fence and hash-checked committed audit
phase without performing Repair or any fence transition. A blocking/unknown
state hides the Repair action. IN_PROGRESS without committed audit is UNCERTAIN:
absence of APPLY alone cannot distinguish a transaction that never started from
one that rolled back. Deployment, refresh and status reads never clear a fence.

## Deployment gate

Full serial QA, real isolated PostgreSQL crash/rollback/concurrency tests, auth
and frozen-trigger/RLS tests must pass before commit/deployment. Production
Repair must not be executed by the deployment process. Render's
NOMAD_APPROVED_RELEASE_SHA must match the reviewed deployed RENDER_GIT_COMMIT.
