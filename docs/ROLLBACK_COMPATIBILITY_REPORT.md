# ROLLBACK_COMPATIBILITY_REPORT

PRODUCTION DEPLOYMENT = BLOCKED — NO VERIFIED RECOVERY.
No merge, production migration, backup, restart or deployment is authorized by this report.

## Cause and reproduced failure

Known old production code: de0a0c88fdc2ba3bea1558e7fa313139a16e96f3.
Base for this change: main7a47500883234e0be5c57cf3d4e3495f1bdc6bc8 (PR160).
PostgreSQL revision23 ->24 is additive (five automation tables); the old Alembic
script directory ends at23. The original command is `alembic upgrade head && uvicorn ...`.
On database24 the actual log is `Can't locate revision identified by '20261022_24'`;
Alembic exits255, so the shell never starts uvicorn. This is not a failed SQLAlchemy
business query. Actual old read APIs worked when Alembic was bypassed in the prior audit.

That bypass was insufficient as a deployment contract: it had no schema preflight,
versioned launcher, scheduler isolation or checked shutdown/restart procedure.
We chose an explicit schema job plus checked startup (options A+B), not a stamp,
downgrade or a modification of the immutable old application's migration graph.

## Minimal implementation

- `backend/start_backend.py`: default is a READ ONLY transaction with bounded metadata
  queries, one exact supported Alembic revision, required ORM tables/column types,
  primary keys, declared unique key shapes and foreign keys. No create_all or Alembic
  runs in normal startup. Success execs uvicorn as PID1.
- `--rollback`: the same checks against the OLD image's own models, disabled SHADOW/
  automation required; uvicorn lifespan OFF prevents old FTP/scenario schedulers.
  HTTP business APIs remain available. Graceful request drain bound20s; override
  grants Docker30s. This is a deliberate maintenance rollback mode, not full normal
  operation of all old scheduled features.
- `--check-only`: no server, no migration or business scheduler.
- `--migrate`: separate explicit WRITE job, only reviewed23->24 or24 no-op, flags OFF,
  cooperative exclusive PostgreSQL session advisory lock. Checked startups take a
  shared transaction lock. A competing migration job fails before running Alembic.
  Default lock_timeout2s/statement_timeout30s via child PGOPTIONS; deliberate operator
  PGOPTIONS/URL options remain operator-controlled and must be reviewed.
- Dockerfile default CMD is `python -B start_backend.py`, no automatic schema writes.
- Opt-in Compose override files under deploy/ keep migration and rollback explicit.
  Ordinary compose up never starts the schema-maintenance profile.

Historical ORM hints do not equal exact DDL: revision23 includes nullable uploaded_at
and JSONB report fields mapped by generic JSON ORM types. These are accepted known
compatibilities; arbitrary mismatching column types are blocked. The preflight is
not a complete CHECK/index-predicate/data-quality audit or a proof of source completeness.
Additional known24 tables are allowed for an old ORM; an unknown revision25 is not.
The schema lock is cooperative: direct SQL/unwrapped Alembic must not run concurrently.

## Actual isolated verification

PostgreSQL16.14, synthetic46 sales/item/RFM, all current migrations. No production
connection. The exact ac4c... image is absent from Codex.

| Code/start | Schema | Result |
|---|---|---|
| reconstructed de0a0c8 original CMD |23| PASS: sales/detail/items, RFM, old Calltrack integration HTTP200 |
| new default checked start |23| BLOCKED before migration; all table contents/revision unchanged |
| explicit current --migrate |23->24| PASS; repeat24 no-op |
| new checked standard CMD |24| PASS:9 original +3 analytics APIs; disabled feed503 |
| reconstructed old original CMD |24| FAIL reproduced, exit255 before uvicorn |
| reconstructed old checked --rollback |24| PASS: two containers, sales/RFM/client/product APIs, zero table-content/revision changes |
| checked old shutdown/new container start |24| PASS: exit0, data unchanged, APIs work again |
| unknown revision / changed numeric type |fixture| BLOCKED by native regression test |
| competing schema migration connection |fixture| BLOCKED before Alembic subprocess |

Tests:

- test_checked_startup.py + test_churn_pilot.py:15 passed (includes native schema drift,
  migration lock and existing45-day/3-positive-day/scope guards).
- test_rollback_runtime.py: opt-in reconstructed-old Docker regression,1 passed;
  validates original exit255, two checked replicas, business endpoints, graceful
  stop/recreate and byte-equivalent JSON snapshots of EVERY application table.
- Stand additionally exercised actual legacy POST client-sales/client-insights and
  new analytics overview/checks/clients; client suggestions/filters and product search.

A strict5s stop in one concurrent stand attempt produced137 (Docker events confirmed
TERM then deadline KILL, not an OOM event). The reviewed rollback override now uses
30s and uvicorn drain20s; final rollback test exited0. An independent ordinary-new
idle stop with15s exited0 in0.72s. These are idle/synthetic tests, not proof that
interrupting active imports or long writes is safe. Quiesce jobs/requests first.

Reconstructed old image ID:
`sha256:45a7c5d3781470413181c82a75ac5157d205a74b63267b8a336a4582b2bd485b`.
It is NOT the exact production image
`sha256:ac4c578650e52e4c8a7fc7c46f7535dc6aff236f97fe06132e029e4a3d3cc5e0`.
This distinction remains a required production gate.

## Future owner-approved migration and rollback procedure

Prerequisites: separately approved operation, verified recovery for BOTH databases,
actual server Git/dirty-file/schema/config inventory, immutable candidate and old
image IDs, private connection settings and adequate disk/RAM/WAL headroom. Do not
clean server files, build/prune on VPS or create new backups under this task.

1. Verify source, image IDs and reviewed launcher SHA256. Never select an unverified
   mutable old tag. Deploy a reviewed readable launcher file on the host (container
   uid10001 must be able to read it), mount it read-only; do not edit old container files.
2. Stop/quiesce analytics producers, consumers, external schedulers, imports and
   in-flight writes using the separately approved maintenance mechanism. Confirm
   persisted and effective flags remain OFF. A schema lock alone does not stop apps.
3. After explicit migration permission, run ONLY the opt-in schema job, e.g. reviewed
   Compose + deploy/migrate.explicit.override.yml and schema-maintenance profile;
   `run --rm --no-deps schema-migrate` uses a pinned candidate image and explicit24.
   Database must already be running/healthy and revision23/24 exactly. A fresh empty
   dev bootstrap is a different explicit Alembic operation, described in README.
4. Verify revision24, new object definitions and unapproved defaults; start candidate
   normally only after separate deployment permission. Health200 alone is insufficient.
5. For application rollback, retain schema24 and all new data. Set private owner-reviewed
   SALES_ROLLBACK_IMAGE to the tested immutable old ID and SALES_ROLLBACK_LAUNCHER to
   the reviewed absolute host script path; use deploy/rollback.checked.override.yml
   in a reviewed combined Compose configuration. It overrides CMD, image, flags,
   restart policy and grace; it must not rebuild old code or silently start workers.
   The override uses `build: !reset null` and `pull_policy: never`. Plain `build: null`
   was shown to retain the inherited build context during Compose merge and is unsafe.
   Validate the combined config on the installed Compose: unsupported !reset syntax,
   any remaining build field or unexpected pull behavior is a STOP, not permission
   to silently replace the override or upgrade server tooling.
6. Actual old image must FIRST pass the isolated test and server-specific API checks.
   This repository has no verified access to that exact production artifact.
   If preflight fails: STOP, retain state, inspect schema/model mismatch privately.
   No stamp/downgrade/drop/automatic repair. Never retry the original old Alembic CMD on24.
7. Verify core reads, tasks/manager state in Calltrack, flags, no source releases and no
   unexpected writes. Restarting the checked container repeats preflight, not migration.
   Restoring normal scheduler operation or automation requires a new explicit decision.

## Limits and recovery

A launcher fixes the application startup contract, not lost data. It does not validate
production backups, restore/PITR, RPO/RTO, exact production dependencies/mTLS/proxy,
large-table timing, arbitrary schema drift or external worker shutdown. It also does
not roll back manager work or reset budgets/cursors. Leave all new tables/history in
place; never stamp23 over24 or use downgrade to force old code to start.

The prior DEPLOYMENT_READINESS_REPORT/RUNBOOK conclusions remain: existing recovery
must be independently verified, production server facts remain owner-supplied, and no
live CHURN_RISK or migrations are allowed before separate authorization.
