# Product sales: large selections and PostgreSQL resource use

## Confirmed failures and scope

The supplied production traceback confirms `asyncpg.InterfaceError: the number of
query arguments cannot exceed 32767`. `_product_condition()` expanded selected
product codes, articles and fallback names into individual `IN` binds. Even with
the earlier client-array fix, a product selection could exceed the protocol limit.

A second supplied error is a failure to resize a `/PostgreSQL.*` shared memory
segment by 4 MiB. That identifies a dynamic shared-memory allocation failure,
consistent with a full/bounded POSIX shared-memory mount such as Docker's
`/dev/shm`. It does not prove that the host has run out of RAM or that swap will
help. Parallel Hash / other parallel execution nodes are plausible consumers;
the owner subsequently identified `POST /api/integrations/calltrack/dashboard-sales`
and supplied its SQL shape; the actual PostgreSQL plan is still unavailable. Its
separate optimization is described in `calltrack_dashboard_memory.md`. No live PostgreSQL
plan, memory consumption or filesystem capacity was measured from Codex.

## Implementation

- All product identity branches use typed `ANY` array binds: at most one bind
  for each of code, article and fallback name. Normalization, duplicate handling
  and the original code-before-article/name choice are preserved. An article-only
  selection retains the previous article matching semantics.
- Client filters remain arrays; product-report departments and catalog brands
  also use arrays. Promotion-candidate client filters no longer expand `IN`.
  Product analytics already uses arrays for client and selected SKU lists.
- Summary first aggregates matching positions to receipt grain. Outer check
  counting no longer needs `DISTINCT sale_id` over all matching lines; client
  counting still uses the same raw nullable client values. Revenue/base/units
  retain return, zero and null semantics. No per-product query fan-out is added.
- Overview metrics use a narrow filtered receipt CTE rather than repeating the
  entire wide Sale projection. Department and check analytics join positions to
  filtered receipt IDs before aggregating quantities. Receipts without positions
  are retained by the outer joins. Per-receipt medians and distributions use the
  same input values as before.
- Product-sales read endpoints and `/analytics` GET endpoints share existing
  report admission across API worker processes via its file lock. Requests wait
  before large JSON parsing; saved-set CRUD and catalog integration endpoints do
  not acquire this new report lock. Existing product analytics shares this lock.
- Only those report transactions use `max_parallel_workers_per_gather=0`,
  `work_mem=4MB`, `hash_mem_multiplier=1`, `statement_timeout=60s`, and
  `lock_timeout=2s`, all via `SET LOCAL`. Rollback happens before admission is
  released, including on query error or cancellation. No `ALTER SYSTEM`, global
  memory increase, PostgreSQL restart or Docker shared-memory change is added.
- The product selector normalizes once per product, with memory-headroom checks
  during large selection processing. Parallel report bursts are queued/bounded
  instead of launching more heavy database operations.
- PostgreSQL resource/disk/time failures return HTTP 503, not fabricated empty
  report data. Logs include SQLSTATE and driver class, never SQL parameters or
  the complete exception string. Busy reports also return 503 with Retry-After.
  The frontend's existing manual-retry behavior remains in place.

## Memory and planner limits

`work_mem` applies per plan node; 4 MiB is NOT a hard cap on the PostgreSQL process
or the report. CTE tuplestores, sort/hash spill, array values, parsing/planning,
backend result lists and XLSX generation still consume resources. Serial plans
avoid this report family's parallel DSM allocations, but do not control other
applications, API families, containers, scheduled workers or database sessions.
A full data/temp filesystem can still cause failures. Large Excel exports still
materialize their existing workbook/result data; this change is not an arbitrary
size export guarantee. The admission policy may trade throughput for stability
on the 2 GiB RAM VPS.

The normalized code/article/name predicates do not automatically use plain column
indexes. Existing date and sale-item sale_id indexes may support early filtering;
the production planner must confirm this. No functional indexes, migrations,
ANALYZE, server tuning or new tables are applied by this fix. Adding an index or
raising memory requires separate review based on the actual plan and write cost.

## Validation

- More than 100,000 codes, articles and fallback names compile to one array bind
  per branch; mixed product/client selection size does not expand parameter count.
- An actual ASGI summary request parses 100,003 products plus a synthetic
  100,003-client retail scope and compiles the real asyncpg SQL with bounded binds.
  This uses a DB double, not a live PostgreSQL execution.
- Disposable in-memory SQLite executes old/new aggregation shapes on mixed
  products, repeated clients, returns, zero units, missing clients/prices, empty
  results, excluded dates/stores and receipts without positions. Summary inputs,
  department aggregates, check rows and overview metrics are equivalent.
  PostgreSQL-only array membership in the separate SQLite brand test is adapted
  to small IN predicates; array behavior is checked with the asyncpg compiler.
- Tests cover transaction-local settings, cleanup on success/error/cancellation,
  admission before body parsing, sequential requests, HTTP 503 and parameter-free
  failure logging. Saved-set writes keep their original DB dependency.
- Targeted suite: **114 passed, 1 skipped**. The skipped test requires a disposable
  PostgreSQL. OpenAPI generation, Python compilation and diff check pass.

There is no isolated PostgreSQL available for this run. No PostgreSQL integration
execution, EXPLAIN/ANALYZE, disk-full reproduction, real 2 GiB workload benchmark,
image build, production access, migration or deployment was performed. Unit/SQLite
results do not establish PostgreSQL plan performance or production readiness.

## Safe production inspection (read-only, owner-operated)

Run from the VPS shell, not inside psql. Do not paste SQL directly into bash.

```bash
git --no-optional-locks -C /var/www/html/vr/sales rev-parse HEAD
git --no-optional-locks -C /var/www/html/vr/sales status --short --branch
docker inspect --type container --format \
  'image_ref={{.Config.Image}} image_id={{.Image}} compose_files={{index .Config.Labels "com.docker.compose.project.config_files"}}' \
  sales-journal-backend
docker inspect --type container --format 'shm_bytes={{.HostConfig.ShmSize}}' sales-journal-db
docker exec sales-journal-db df -h /dev/shm /var/lib/postgresql/data
docker stats --no-stream --format '{{.Name}} {{.MemUsage}}' sales-journal-db sales-journal-backend

docker exec -i sales-journal-db sh -c \
  'exec psql -X -v ON_ERROR_STOP=1 -P pager=off -U "$POSTGRES_USER" -d "$POSTGRES_DB"' <<'SQL'
BEGIN READ ONLY;
SET LOCAL statement_timeout = '5s';
SET LOCAL lock_timeout = '1s';
SHOW transaction_read_only;
SHOW server_version;
SHOW dynamic_shared_memory_type;
SHOW work_mem;
SHOW hash_mem_multiplier;
SHOW max_parallel_workers_per_gather;
SELECT version_num FROM alembic_version;
SELECT c.relname, c.reltuples::bigint AS estimated_rows
FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
WHERE n.nspname='public' AND c.relname IN ('sales','sale_items');
SELECT tablename, indexname, indexdef FROM pg_indexes
WHERE schemaname='public' AND tablename IN ('sales','sale_items')
ORDER BY tablename,indexname;
ROLLBACK;
SQL
```

These SHOW values are the inspecting session's defaults, not the report's local
settings. Obtain the exact failing query/endpoint for the second DiskFull error.
Inspect its plan with `EXPLAIN (FORMAT JSON, SETTINGS)` in a short read-only
transaction after review. Do NOT run `EXPLAIN ANALYZE` or a large COUNT/report on
production as a diagnostic shortcut. Do not publish plans containing customer
literals. Confirm filtered receipt IDs precede quantity aggregation and the report
plan has no Gather/Parallel Hash under its local settings. A small/synthetic
selection's estimated plan is not evidence for a real 100,000-item selection.

## Update and rollback procedure (not executed)

1. Review these backend changes and compare the actual image/source versions.
   An updated frontend or git checkout does not update Python code inside an old
   running image. Preserve existing VPS/V3 source modifications. Stop on unmatched
   source baselines or uncommitted changes; do not overwrite them automatically.
2. Transfer only the reviewed changes into the verified current backend build
   context, using `git apply --check` before applying a patch. When the private
   build repository uses `app/` at its root, adjust the patch's path prefix and
   verify again. Required runtime files are:
   - `app/routers/product_reports.py`
   - `app/routers/analytics.py`
   - `app/routers/promotion_candidates_report.py`
   - `app/services/report_filters.py`
   - **new** `app/services/report_execution.py`
   Keep existing V3/CHURN/service-auth code, storage and production configuration.
   Do not replace the whole image context with an older checkout.
3. Build through the established private CI path, not a heavy build on this VPS.
   Use a new immutable image/tag and verify its digest and the five runtime file
   hashes. Verify imports, new modules, tests and startup in isolation. This fix
   adds no dependency or schema migration.
4. Record the old image ID/digest and exact active Compose file order. Preserve
   rollback material and the automation-off/SHADOW settings. Review the effective
   backend image and mounts without printing credentials. If startup would run
   migrations, stop; preserve the reviewed read-only checked startup instead.
5. Only after separate deployment approval, select the verified new image using
   the existing release process and the confirmed Compose configuration. Recreate
   only backend with no dependency restart/build/pull; do not start workers,
   alter PostgreSQL/Docker memory or use an older fallback image by guessing tags.
6. Check health and runtime module hashes, then one small report (including its
   comparison/details/Excel) and compare all metrics with a known result. Confirm
   automation remains off. Only after owner approval run the representative large
   selection while observing memory, shared-memory/temp capacity and query time.
   Stop on mismatched totals, resource errors or unexpected jobs. No parallel load
   burst is needed for the first controlled check.
7. For rollback, select the previously recorded immutable backend image with the
   same verified Compose files/startup and recreate only backend through the
   existing rollback procedure. No Alembic downgrade/stamp or data restore is
   needed for this schema-neutral patch. Do not delete reports/history/storage,
   change flags or restart PostgreSQL as part of rollback.

Merge, publication, deployment and workload execution are separate actions; none
were performed while implementing and testing this fix.
