# Product analytics: bounded memory pressure

Baseline: `main` at `96692baacbfab1b53a7c7e4d5d0d74b461e2358a` (PR #116 reverted).
No production database, server or configuration was accessed. PR #116's larger
decompression buffer is not restored; pickle/zlib format and the 8 KiB reader
remain unchanged.

## Sources of pressure and changes

* Per-key single-flight prevented duplicate builds only for identical keys.
  Different keys could build simultaneously, and cache hits could restore
  several full Python object graphs concurrently. A restored result also lived
  through grouping, response encoding and sending after the cache lock ended.
* A single admission slot now covers POST analytics responses through sending,
  including builds, restores and compression. The existing per-key single-flight
  and invalidation generation checks remain. A nonblocking Linux `flock` also
  excludes other workers sharing the same lock filesystem. CPU-heavy work runs
  in threads; waiting never sleeps synchronously on the event loop.
* Admission waits at most **10 seconds**, allows at most **8 queued requests per
  worker**, then returns HTTP **503**, the usual JSON `detail`, and `Retry-After: 10`.
  GET filter/name/manager directories and health/other routers do not enter this
  queue. Batch-info callers outside this router share the slot too, so catalog
  heavy work cannot run alongside analytics enrichment.
* SQL aggregation is unchanged. Aggregated period rows and distinct manager
  clients are streamed with `yield_per=512`, eliminating materialized database
  result lists beside converted report rows. Product identities are passed to
  VRCatalog as an iterator; the second catalog result map and concatenated period
  list disappear. Period/catalog references are released before later processing.
* Previously `gather` created a task for every batch and retained every response
  until all finished, despite an HTTP semaphore of two. Now ordered windows of
  at most **two tasks**, **250 products each**, are consumed and discarded before
  the next window. This keeps the existing batch size/concurrency, alias overwrite
  order, fields/images and successful-batch negative caching. Failed batches are
  retried on subsequent reports; their exception contents are not logged.
* The previously count-only catalog card cache receives a **24 MiB conservative
  charged-byte ceiling**, including separate charges for aliases and metadata.
  TTL (1800 seconds) and count ceiling (30,000) are unchanged. Large cards remain
  available in the current report even when not retained. A 16 MiB trial caused
  unnecessary refetches of the existing 11,587-product small-card fixture; 24 MiB
  preserves that reuse test while bounding a previously unbounded byte footprint.
* Cache preparation runs off-loop; cache publication and mutation remain on-loop.
  Invalidation during compression cannot publish stale results. Cancellation waits
  for already-started native threads and drains catalog workers before freeing
  the admission slot. Another request cannot overlap abandoned work.

The compressed dataset cache is still **32 MiB per entry, 48 MiB total, two
entries, 120 seconds TTL**. Numeric/date values, grouping/filter algorithms,
report row counts and successful response JSON are unchanged.

## Diagnostics and low-memory rejection

Logs contain stage timings, row/key counts, shallow intermediate container sizes,
process RSS/swap and cumulative process peak RSS, active/queued operation counts,
compression/profile timings and restore RSS delta. Existing `hit`, `miss`,
`stored`, `oversized`, `expired`, `restore_ms` and `postprocess_ms` remain.
No cache keys, product/client contents or filter values are added to diagnostics.

Before admission, restoration, storing, merging and each SQL chunk/catalog window,
known available RAM below **256 MiB** causes the same controlled 503. Available RAM
is the minimum of Linux MemAvailable and readable cgroup memory headroom; swap is
not counted. This is a conservative stop condition, not an allocation reservation.
When metrics are unavailable, concurrency protection remains but this check is
skipped. Logged process peak RSS is cumulative since process start, not a resettable
stage-local maximum; RSS deltas can include unrelated process activity.

## Reproducible synthetic comparison

Python 3.12, managed Linux environment (8 GiB cgroup), separate fresh process per
case, no SQL/HTTP. 27,016 exact merged analytics rows with properties, stocks,
prices, dates and deterministic description text; one retained compressed entry
is about **22.77 MiB**, close to the reported 21.8 MiB. Three simultaneous logical
requests use different cold keys or the same warm key. `tracemalloc` is enabled.
The warm seed is created before tracing, but process peak RSS includes it.

| Scenario | Baseline peak RSS | Bounded peak RSS | RSS reduction | Baseline additional traced peak | Bounded additional traced peak |
| --- | ---: | ---: | ---: | ---: | ---: |
| Three cold datasets | 841.04 MiB | 408.32 MiB | 51.5% | 335.50 MiB | 190.17 MiB |
| Three warm responses | 676.19 MiB | 359.59 MiB | 46.8% | 304.85 MiB | 110.04 MiB |

Peak simultaneous logical operations: **3 → 1**. All three results retain
27,016 rows, revenue checksum 364945636.0 and comparison date 2025-01-01.
Deep equality (including nested fields, order, numeric values and dates) is
covered separately by the existing lossless-cache regression tests. Raw results:
`product_analytics_memory_pressure_benchmark.json`.

Elapsed time **with tracing**, completing all three responses: cold 56.454 →
53.391 seconds; warm 3.251 → 3.128 seconds. These are workload totals, not
single-restore latency or production speed estimates. Cold synthetic admission
uses `--wait-seconds 240` in that subprocess only so all three traced requests
finish; application admission stays 10 seconds and would reject excess work.

Example (use an isolated baseline checkout, never production):

```bash
git worktree add /tmp/sales-journal-memory-baseline 96692ba
# Run the new script against the old backend; run each line as a fresh process.
PYTHONPATH=/tmp/sales-journal-memory-baseline/backend python backend/scripts/benchmark_product_analytics_memory.py --mode baseline --case cold
PYTHONPATH=backend python backend/scripts/benchmark_product_analytics_memory.py --mode bounded --case cold --wait-seconds 240
PYTHONPATH=/tmp/sales-journal-memory-baseline/backend python backend/scripts/benchmark_product_analytics_memory.py --mode baseline --case warm
PYTHONPATH=backend python backend/scripts/benchmark_product_analytics_memory.py --mode bounded --case warm
```

The comparison measures dataset/response concurrency; additional SQL streaming
and catalog response-retention savings are not included in these numbers.

## Validation and remaining risks

Baseline suite: **255 passed, 3 failed**. Changed suite: **273 passed, the same
3 failed**. Existing failures are catalog-tree wrapper/cache tests caused by the
pre-existing second `get_catalog_tree` definition; unrelated duplicate definitions
are unchanged. Added tests cover same/different keys, serialized restoration,
invalidation during compression, cancellation/native work cleanup, queue timeout,
file locking, health responsiveness through sending, streaming aggregates/managers,
low RAM, partial catalog errors, byte eviction and colliding alias ordering.

This does not prove absence of OOM on the 1.9 GiB VPS: a single unusually large
dataset, other routes/processes, allocator retention, PostgreSQL memory or filled
swap can still exhaust the host. An individual API response/card and pickle graph
are not capped/truncated. The headroom check can race other allocations. The lock
coordinates workers on the same filesystem, not separate containers/hosts; cache
copies remain per worker. CPU threads can contend for the GIL, though slow native
work and waits no longer execute directly on the event loop. A slow response
sender holds admission longer and may cause other analytics requests to receive
503. Smaller retained catalog footprint can cause more refetches; processing
ordered pairs can wait for the slower batch. No deployment, DB schema, cache
limit increase, Nginx timeout, swap or production configuration change is included.
