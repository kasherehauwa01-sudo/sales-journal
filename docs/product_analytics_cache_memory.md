# Product analytics cache memory

The full product-level result `(rows, old_from, old_to, manager_clients)` is now
stored as an in-process, losslessly compressed pickle stream (zlib level 1).
Every row and field is retained, including properties, stocks, prices, images,
exact float values, dates and the manager-client selection. Reads reconstruct the
same tuple/list/dict structures. The cold request returns the calculated object;
subsequent requests receive restored objects isolated from the stored payload.

The serializer and decoder process 64 KiB chunks. Neither `pickle.dumps` nor a
whole uncompressed serialized buffer is used. Pickle's memo preserves aliases
between nested objects and still consumes temporary memory; this is not a
zero-allocation transformation. The decoder is strictly for payloads generated
by this process. It must never accept user input, files or remote cache contents.

## Why compression

A shared-field/tuple-row prototype reduced the synthetic fixture from 64.24 MiB
to 49.19 MiB (23.42%), which still exceeded the existing 32 MiB entry limit.
Dropping detail fields or limiting SKUs would change report behavior, so neither
was selected. Separate copies of grouped results would consume cache entries
and duplicate data. Instead all groupings share one filtered product dataset;
grouping runs after cache retrieval, using the existing exact algorithm.

Limits remain **32 MiB per entry, 48 MiB total, two entries and 120 seconds TTL**.
Entry accounting includes compressed bytearray allocation capacity and its
wrapper. Compression is abandoned when this capacity exceeds the entry limit;
`oversized` remains observable. Rejected/unsupported encodings do not fail a
successfully calculated report. Generic non-dataset cache values retain the
previous representation and size checks.

## Reproducible measurement

From the repository root, with backend dependencies installed:

```sh
PYTHONPATH=backend python backend/scripts/benchmark_product_analytics_cache.py
```

This is a **synthetic fixture, not a production VPS measurement**: 27,016 SKUs,
current/previous periods, per-SKU dates and image URLs, five catalog labels,
one property, one stock and one price per SKU, and 1,000 manager-client names.
It uses the actual `merge_periods()` output. Rows are sorted in the benchmark
for repeatability; production row ordering is unchanged.

Observed on CPython 3.12.14:

| Original retained component | Bytes | MiB |
| --- | ---: | ---: |
| Tuple/list/row dictionary containers | 22,696,448 | 21.645 |
| Properties | 6,700,203 | 6.390 |
| Stocks | 6,700,617 | 6.390 |
| Prices | 7,348,424 | 7.008 |
| Other row fields, keys and values | 23,827,708 | 22.724 |
| Comparison dates | 64 | <0.001 |
| Manager-client list | 86,636 | 0.083 |
| **Total** | **67,360,100** | **64.240** |
| **Compressed entry** | **1,773,552** | **1.691** |

Retained entry memory decreased **97.37%** and the fixture now fits the existing
entry limit. Shared objects are counted once, assigned to the first component
referencing them. These are Python object sizes, not process RSS or exclusive
memory savings: the independent CatalogVR cache may still reference some objects.
Compression results depend on object order, content and allocator capacity.

`tracemalloc` started after fixture creation showed additional peaks of about
**1.22 MiB for profiling**, **29.13 MiB for packing**, and **78.48 MiB for restoring**.
The last figure includes the full reconstructed result and the unpickler memo.
Instrumented packing/restoring took about **284/581 ms** on this environment;
this is not a VPS latency prediction. The benchmark intentionally retains the
original for equality comparison, so it does not measure production process RSS.

The original set-of-object-IDs traversal needed about 67.2 MiB of temporary
profiling memory on this fixture. Lazy traversal plus exact paged identity
bitmaps reduced that overhead to about 1.22 MiB, without copying data containers.
Unaligned identities have a separate fallback set; alignment does not affect
correctness. A small-fixture test compares sizes with an independent estimator.

## Actual server measurements

On an INFO-level cache store, `memory_components_bytes` logs the actual live
result's components, without row contents, client names or cache keys. Traversal
uses lazy iterators and identity bitmaps, not serialization or copied dictionaries.
`stored.dataset_size_mb` and `cache_size_mb` describe the retained compressed
entry/cache. `hit`, `miss`, `stored`, `oversized` and `expired` are preserved.
Actual production data/credentials are not available in the development session;
no claim is made that the live 27,016-row report has the fixture's composition.

## Reuse, invalidation and remaining risks

- Identical revision, dates and filters reuse the product dataset, including
  across grouping changes, without SQL dataset reconstruction or CatalogVR calls.
  Exact unique-document SQL counts used by report/summary endpoints still run.
- All filters and revision remain in the key. Existing `max(Sale.id)` revision
  behavior is unchanged; existing explicit invalidation calls are retained.
- An invalidation generation prevents a calculation started before invalidation
  from publishing its stale result afterwards. That already-running request may
  still finish with its original snapshot; the next request recalculates.
- Per-key locks retain single-flight protection. Lock user counts prevent lock
  removal while waiters still exist, including cancellation cleanup.
- TTL is unchanged. Cache reuse stops on expiry, revision/filter changes, eviction,
  worker restart or another worker process. The existing separate CatalogVR
  product cache and its limits/TTL are unchanged. No persistent cross-worker cache
  or guarantee that CatalogVR is never queried again is introduced.
- Cache limits bound retained compressed entries, **not total process memory**.
  Cold builds still create complete data; each hit restores complete data.
  Concurrent responses and different-key builds can therefore raise peak RAM.
  CatalogVR cache memory is separate, and Python's allocator may retain freed RAM.
- Profiling, compression and decoding run synchronously and add CPU work on store/
  hit. Benchmark timings are much smaller than the reported 147–255 second build,
  but slower VPS CPU or larger nested payloads can increase them.
- Large, poorly compressible data can still exceed 32 MiB and be rejected. No
  rows are truncated and no approximate computations are substituted.

No PostgreSQL, migrations, Docker Compose, frontend, deployment or other-project
configuration changes are needed. This document records implementation evidence;
production memory and latency still require review after an explicitly authorized
deployment.
