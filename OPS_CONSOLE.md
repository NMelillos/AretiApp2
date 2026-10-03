# Ops Console v1 (review branch only)

Route: `?ops=1`, after main login and before NOMAD recovery/pandas/db/dashboard
imports. THIRD routing takes precedence and is unchanged. Nothing is deployed,
no environment settings or Supabase grants/roles are created by this change.

## Owner authorization

Main authentication must already have succeeded, `login_user` must exactly match
`OPS_OWNER_USERNAME`, and THIRD authentication must be false. Missing owner
configuration always denies access. There is no Areti fallback.

The existing main login uses a shared password for Areti and the configured
`LOGIN_USERNAME` alias. Username checks alone cannot distinguish the owner from
someone who knows Areti's password. Therefore a distinct owner step-up secret
is required (approved by the operator). No password is generated or stored in
source. `OPS_OWNER_PASSWORD_HASH` must contain:

`pbkdf2_sha256$600000$<32 hexadecimal salt characters>$<64 hexadecimal hash characters>`

The digest is PBKDF2-HMAC-SHA256 with 600,000 iterations, the **decoded** 16-byte
salt, UTF-8 password, and a 32-byte output. Configure the distinct secret outside
this branch through an authorized secret-management process. Main login's
`LOGIN_USERNAME` must permit the owner's existing account/alias; this feature
does not change main or THIRD password acceptance. The owner proof is
session-local and invalidated by username/hash/configuration changes. Password
and authorization proof/result state are also discarded by main sign-out;
this cleanup is the only change to existing authentication behavior.
Password widget state is removed during the submit callback. Five failed step-up attempts
per session per five minutes are permitted; distributed throttling is v2 work.

## Read-only database

Only `OPS_DATABASE_URL` is used. No fallback to `DATABASE_URL`, `POSTGRES_URL`,
application pools, or privileged credentials. Configuration remains absent
until the operator provisions and reviews it separately.

A fresh PostgreSQL connection requires TLS, explicit host/database/user, and no
DSN routing/session overrides. Startup options enforce read-only default,
15-second statement timeout, 3-second lock timeout and `pg_catalog` search path.
Every connection starts a REPEATABLE READ / READ ONLY transaction, verifies
`transaction_read_only=on` and timeouts, and verifies role restrictions before
diagnostics. Only rollback/close are used; never commit.

The role must have no administrative flags, memberships, database/schema CREATE
or user-relation mutation/trigger/reference privileges. These catalog checks
are bounded in returned results and do not read financial datasets. The fresh
connection plus fixed query API prevents temp-table or session-command paths.
Every supported query is a fixed SELECT/SHOW with fixed parameter bindings and
a bounded result. No query input/SQL, raw cursor, commit, mutation method, public
function invocation, or business-module dependency is exposed.

Production role/grant provisioning is deliberately **not** included. SELECT
access must cover the required monetary-column metadata and the one current
`financial_control.fence` record (an ordinary non-RLS table). Existing financial
control/catalog integrity checks restrict its ACLs: do not grant access blindly
or change the frozen NOMAD approval. Review compatible privileges independently
before enabling database diagnostics. Missing permissions fail closed.

[PostgreSQL read-only transaction semantics](https://www.postgresql.org/docs/current/sql-set-transaction.html)
are an additional database guard, not a replacement for restricted privileges
and fixed query dispatch.

## Evidence boundaries

`status`, `health`, `deployment`, `release-identity`, `db-connection-test`,
`financial-schema`, `writer-status`, `repair-status`, `nomad-status`,
`recent-errors`, `help` are the complete allowlist. No normalization into
natural language or alternate commands occurs. Unknown input is never logged.

Deployment diagnostics reuse the existing identity rules in artifact-only mode.
The optional `allow_git=False` parameter prohibits Git subprocess fallback;
default behavior used by existing NOMAD/business paths is unchanged. Missing
`.deployment_identity.json` fails closed. No Ops command invokes subprocess,
shell, eval/exec, SQL input, repairs, recovery, schema migration or deployment.

NOMAD VERIFIED means the durable NORMAL fence, exact operation ID and exact
`independently_verified:<64 hexadecimal hash>` phase match. It does not freshly
verify financial rows, source PDFs, reconciliation, reports or business accuracy.
Only nine schema metadata rows and the latest indexed fence revision are read.
No uploaded PDF, statement content or customer rows are required/displayed.

Health describes this reachable process and its read-only DB query, not external
Render health. External logs/recent errors and memory telemetry are not acquired;
memory-status is intentionally not in the allowlist. No polling, background work
or diagnostics on page load/refresh. Result panels show prior results until a
new explicit command; `elapsed_ms` describes that execution.

Sanitized application-log audit records contain UTC timestamp, a hashed owner
identifier, allowlisted command (or `unknown`), outcome and duration. No SQL,
environment values, passwords, exception text, file paths or financial values
are logged. Durable database audit and distributed throttling are v2 work.
