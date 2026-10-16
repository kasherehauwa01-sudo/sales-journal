# Persistent RFM snapshots

## Audit and scope

The checked-out Sales Journal had RFM SQL in `app/routers/rfm_report.py`, but no
`rfm_data.py`, `calltrack_insights.py`, `population_rfm`, `_rfm_snapshot`,
`client-insights` route or insights smoke script. The existing Calltrack contracts
were client-sales and sale detail; both remain unchanged. This change introduces
the requested insights endpoint in Sales Journal. No Calltrack application changes.

`imports.process_import` is shared by uploads (FastAPI BackgroundTasks) and FTP.
Sales commit per row, followed by a committed `completed` or `failed` batch status.
The existing source advisory lock covers import and sales deletion across workers.
Docker starts Uvicorn; snapshot correctness does not depend on worker count.

## Architecture

- `rfm_snapshots`: immutable generation UUID, period, successful calculation time,
  client count, duration, algorithm version, population quintile boundaries.
- `rfm_snapshot_clients`: composite primary key `(snapshot_id, client_key)`, name,
  R/F/M scores, code, segment, tags, recency, frequency, exact monetary sum and
  average check, first/last purchase. JSON **in PostgreSQL** holds supplementary
  metrics: purchase rate, average interval, overdue cycle, period first purchase,
  historical first-purchase age, min/max check, department count.
- `rfm_snapshot_state`: constrained singleton pointing to the active generation.
  The foreign key prevents deletion of an active generation.

Rebuild acquires PostgreSQL transaction advisory locks in fixed order: RFM
`734190222`, then existing source lock `734190220` exclusively. All calculations,
batched inserts, row-count validation and pointer switch use one connection and
one transaction. Readers continue seeing the previous committed generation.
An exception/cancellation rolls back B; A remains active. PostgreSQL releases
locks on transaction/process failure. HTTP lookup reads pointer, version and
client in **one statement**, so concurrent activation cannot mix generations.

Import refresh runs after successful completion and release of the shared source
lock. Failed imports do not refresh; partial failed-import rows retain the old
RFM until a subsequent successful refresh. Refresh failure is logged separately
and cannot turn a committed successful import into a failed one. Overlapping
refresh requests wait and can reuse a generation completed after their request.
Sales deletion queues refresh as a post-response BackgroundTask after commit.
Neither insights nor RFM report HTTP handlers initiate a population rebuild.

The snapshot builder queries `sales` only. Scoring still calls unchanged
`tied_quintiles`, `enrich`, `segment_client` and `SegmentSettings` over the entire
period population. It preserves the existing float inputs for scoring, while
persisting the original Decimal SQL monetary sum and average separately.
Product metrics are absent from snapshot rather than persisted as fictitious zero.

Period endpoints are inclusive. The start matches frontend `Date.setMonth(-12)`:
2026-10-15 → 2025-10-15; 2024-02-29 → 2023-03-01 (JS overflow, not Feb 28).
Recency uses period_to. First purchase still searches all history through period_to,
without applying the period start. Normalized SQL client key remains
`lower(trim(client))`, including Cyrillic case and surrounding spaces.
Default calculation date uses the existing `AUTOLOAD_TIMEZONE` (Europe/Moscow by
default). Browser-local dates can differ around midnight; the report never silently
substitutes a snapshot with a different period or population.

Arbitrary RFM reports, department/manager/buyer filters, custom segmentation
settings and XLSX export retain their original live calculations. Snapshot reuse
for reports is deliberately deferred: product metrics and population filters must
remain equivalent. Their existing potentially expensive product aggregation is
unchanged. Calltrack always uses the persistent active snapshot.

## API

`POST /api/integrations/calltrack/client-insights`, existing integration Bearer auth:

```json
{"name":"Бережнова Елена Сергеевна ИП"}
```

Response: `status`, normalized `client_key`, `rfm`, `snapshot`, `unique_products`,
`top_categories`, `sale_quantities`. Monetary/quantity Decimals serialize as strings.
Snapshot metadata: `id`, `calculated_at`, `period_from`, `period_to`, `age_seconds`,
`stale` (period_to precedes the current business date; diagnostic, never a rebuild).

Missing snapshot: 503 with `detail.code=rfm_snapshot_unavailable`, no heavy SQL.
Missing client: 200 `status=client_not_in_snapshot`, `rfm=null`, metadata preserved.
Product analytics still query only this name and the explicit snapshot period.

Unique products counts distinct `coalesce(code,article,name)` across the client's
selected sales. Sale quantities sum item quantities per sale, including zero for
sales without items. Top 10 categories sum quantities using matched local
CatalogVR `catalog_category` values. Unsynced/unmatched items do not invent a
category. This new endpoint makes no remote CatalogVR/ClientsVR calls. Grouped
item projections and batched local catalog lookup avoid SQL per sale.
The original RFM report's unique_products remains its existing sum of distinct
products per sale; it has not been reinterpreted.

SQL count: 1 snapshot lookup + 1 client-specific grouped item query + at most
ceil(grouped item rows / 1000) local catalog batches (memoized identities can reduce
this). The benchmark and seeded integration test both execute **3 statements**;
without item rows, 2; without snapshot, 1. No population scoring from this endpoint.
Logs record hit/miss/unavailable and durations without client names or tokens.

## Migrations and performance

- `20261015_19`: three new tables; no source scan, sales changes or initial rebuild.
- `20261015_20`: concurrent index on `sales(lower(trim(client)), sale_date)`.
  Separate autocommit block avoids holding a write-blocking index build lock.
  Interrupted concurrent creation can leave an INVALID index; inspect and drop
  only that invalid new index concurrently before retrying this migration.

EXPLAIN ANALYZE on isolated PostgreSQL 16, synthetic 120,000 sales, 480,000 items,
12,000 clients (not production measurements):

| Measurement | Result |
| --- | ---: |
| Full snapshot rebuild, including persistence | 2,173.88 ms |
| Snapshot lookup median / minimum, 20 samples | 0.94 / 0.82 ms |
| Snapshot SQL EXPLAIN execution | 0.094 ms |
| Complete insights service, 3 SQL statements | 8.70 ms |
| Client product SQL before normalized index | 17.274 ms |
| Client product SQL after normalized index | 0.173 ms |

Before: parallel sequential scan of sales (119,992 nonmatching rows).
After: bitmap index lookup returns 8 sales, then 8 indexed item lookups return
32 item rows. No aggregate over all 480,000 items. Snapshot uses its composite PK.
The builder's captured SQL contains no `sale_items` reference. A new pool and an
independent Python process both read the same generation with population building
explicitly forbidden in the test. Complete plans and captured endpoint SQL are in
[rfm_snapshot_benchmark.json](rfm_snapshot_benchmark.json).

Production volume/distribution and cold-cache timings were not available. No
production EXPLAIN, deployment, timeout increase or external service was used.

Reproduce from `backend/`, using a dedicated PostgreSQL database named `rfm_test`:

```bash
export RFM_TEST_DATABASE_URL='postgresql+asyncpg://postgres@127.0.0.1:55432/rfm_test'
PYTHONPATH=. pytest -q
python -m scripts.benchmark_rfm_snapshot
```

The benchmark creates and removes its own schema and refuses any other DB name.
PostgreSQL tests require that explicit URL and otherwise skip. Tests cover tied
quintiles/segments, inclusive dates/year/leap boundaries, historical first purchase,
legacy score equivalence, transactions/failure/parallel builds, worker restart,
missing states, auth/no token leakage, product totals/Decimal, custom report
period/department filtering, export, successful-vs-failed import triggering.

## Final verification and changed files

Full backend suite: **569 passed, 9 skipped, 1 warning in 22.85 s**.
No failing tests. The 9 existing optional PostgreSQL tests need separate
`CATALOG_SYNC_TEST_DATABASE_URL`, catalog performance and
`SEASONALITY_TEST_DATABASE_URL` fixtures; they were not configured. All new RFM
PostgreSQL tests ran. The warning is an existing Starlette/AnyIO deprecated alias.
The first sandboxed full-suite attempt was interrupted after a SQLite test stalled;
the complete suite ran successfully with normal local socket/thread access.

Both new migrations were applied successfully on PostgreSQL. CLI build/status
were checked in separate processes on an empty test DB (a valid zero-client
snapshot). Source compilation and `git diff --check` passed.

Modified existing files:

- `backend/app/models/__init__.py`: register snapshot models.
- `backend/app/routers/rfm_report.py`: use the extracted equivalent RFM query.
- `backend/app/routers/calltrack_integration.py`: authenticated insights route.
- `backend/app/routers/sales.py`: post-commit background refresh after deletion.
- `backend/app/schemas/calltrack.py`: explicit insights request/response schemas.
- `backend/app/services/imports.py`: refresh after successful import/source unlock.

New files:

- `backend/app/models/rfm.py`: generation, client row, singleton pointer.
- `backend/app/services/rfm_data.py`: shared optional-product RFM inputs.
- `backend/app/services/rfm_snapshot.py`: transactional builder/lookup/metadata.
- `backend/app/services/calltrack_insights.py`: snapshot and scoped product data.
- `backend/alembic/versions/20261015_19_rfm_snapshots.py`: additive tables.
- `backend/alembic/versions/20261015_20_rfm_client_lookup_index.py`: justified concurrent index.
- `backend/scripts/rebuild_rfm_snapshot.py`: explicit build/status CLI.
- `backend/scripts/check_calltrack_insights.py`: authenticated smoke CLI.
- `backend/scripts/benchmark_rfm_snapshot.py`: reproducible isolated benchmark.
- `backend/tests/test_rfm_snapshot.py`: period and PostgreSQL integration tests.
- `docs/rfm_snapshots.md` and `docs/rfm_snapshot_benchmark.json`: report/runbook and evidence.

No production deployment or Calltrack application changes were performed.

## Operational limits

Old generations are retained for rollback; no automatic deletion policy is added.
Disk use grows per successful rebuild. Remove only inactive generations under the
RFM transaction lock after the desired retention period; never delete active data.

Rebuild excludes imports/deletions while it reads and persists a stable source.
It is serialized across workers, not distributed to a separate queue. Background
execution uses the project's existing in-process mechanism; a container restart
can interrupt a pending/running refresh. A survives; rerun the CLI after recovery.
There is no new daily scheduler: without sales updates, metadata becomes stale
on the next business date; CLI can explicitly refresh it. Outside-project direct
SQL writes must respect the source lock and request refresh themselves.

## Production deployment commands (not executed)

Run from the existing production Sales Journal repository root with its normal
`.env` and Docker Compose v2 configuration. Keep this shell open through rollback
because it holds the backup directory variable. Do not print or source secret
values just for these commands. No frontend rebuild is needed.

### 1. Backup and record state

```bash
set -euo pipefail
umask 077
rfm_backup_dir="backups/rfm-$(date +%Y%m%d-%H%M%S)"
mkdir -p "$rfm_backup_dir"
docker compose ps
docker compose exec -T backend alembic current
docker compose exec -T db sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc' > "$rfm_backup_dir/sales.dump"
test -s "$rfm_backup_dir/sales.dump"
docker compose exec -T db pg_restore --list < "$rfm_backup_dir/sales.dump" > "$rfm_backup_dir/restore-list.txt"
rfm_previous_image="$(docker inspect --format '{{.Image}}' sales-journal-backend)"
docker image tag "$rfm_previous_image" sales-journal-backend:pre-rfm
cat > "$rfm_backup_dir/rollback-compose.yml" <<'YAML'
services:
  backend:
    image: sales-journal-backend:pre-rfm
    command: ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]
YAML
```

### 2. Build the new migration image, then apply migrations

A new image must be built before running its new migration files. This does not
replace the running backend. Migrations are additive; clients-sales/detail and
old reports continue working during this step.

```bash
docker compose build backend
docker compose run --rm --no-deps backend alembic upgrade head
docker compose run --rm --no-deps backend alembic current
docker compose exec -T db sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -v ON_ERROR_STOP=1' <<'SQL'
SELECT indexrelid::regclass, indisvalid, indisready FROM pg_index
WHERE indexrelid = 'ix_sales_rfm_normalized_client_date'::regclass;
SQL
```

If concurrent creation failed and the above index is invalid, inspect the failure,
then remove **only that invalid new index** with `DROP INDEX CONCURRENTLY
ix_sales_rfm_normalized_client_date;` outside a transaction and retry upgrade.
Do not drop a valid production index speculatively.

### 3. Recreate backend from the new image

```bash
docker compose up -d --no-deps --no-build --wait backend
```

Until initial build completes, the new insights endpoint intentionally returns 503;
existing RFM reports remain available.

### 4. Initial build

```bash
docker compose exec -T backend python -m scripts.rebuild_rfm_snapshot
```

Prints version, period, client count, duration and successful calculation time.
Expect a nonzero population on a populated production database. Do not use
`--as-of` for the normal current snapshot; that option is for diagnostics/backfills.

### 5. Verify active generation

```bash
docker compose exec -T backend python -m scripts.rebuild_rfm_snapshot --status
docker compose exec -T db sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -v ON_ERROR_STOP=1' <<'SQL'
SELECT sn.id, sn.period_from, sn.period_to, sn.calculated_at, sn.client_count,
       sn.duration_ms, (SELECT count(*) FROM rfm_snapshot_clients c WHERE c.snapshot_id=sn.id) AS stored_clients
FROM rfm_snapshot_state st JOIN rfm_snapshots sn ON sn.id=st.active_snapshot_id
WHERE st.id=1;
SQL
```

### 6. Authenticated smoke test, then a backend restart

The script reads the configured token inside the container; never pass it on the
command line. It prints only status, metadata, RFM code and product count.

```bash
docker compose exec -T backend python -m scripts.check_calltrack_insights 'Бережнова Елена Сергеевна ИП'
docker compose restart backend
docker compose up -d --no-deps --no-build --wait backend
docker compose exec -T backend python -m scripts.check_calltrack_insights 'Бережнова Елена Сергеевна ИП'
```

The version must be identical across the restart. Also submit the standard RFM
report and an arbitrary period/filter/export through the existing UI.

### 7. Health and logs

```bash
docker compose exec -T backend python -c "import urllib.request; print(urllib.request.urlopen('http://localhost:8000/api/health').read().decode())"
docker compose logs --since=10m backend
```

Inspect `RFM rebuild success`, lookup hit/miss and timings. Never enable HTTP
Authorization header/body logging for troubleshooting.

### 8. Rollback

Rebuild failure alone requires no DB rollback: previous snapshot stays active.
Fix its cause and rerun the CLI. For application rollback use the saved image and
**skip its startup migration command**: the old Alembic files cannot resolve the
new revision IDs. Additive snapshot tables/index can remain safely in place.

```bash
docker compose -f docker-compose.yml -f "$rfm_backup_dir/rollback-compose.yml" up -d --no-deps --no-build --force-recreate --wait backend
docker compose exec -T backend python -c "import urllib.request; print(urllib.request.urlopen('http://localhost:8000/api/health').read().decode())"
```

This preserves all sales and new snapshot tables. Returning to the new application
uses the normal compose file and new image. Do not blindly downgrade schema or
restore the database over sales imported after the backup. The custom-format
backup is for a separately reviewed disaster recovery if data restoration becomes
necessary, not a routine rollback step. A previous snapshot can also be selected
transactionally under `pg_advisory_xact_lock(734190222)` by updating the singleton
pointer to a verified existing generation; no sales changes are needed.
