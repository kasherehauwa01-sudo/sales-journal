# Product analytics cache restore performance

## Change and evidence

Keep the existing 64 KiB compressed input blocks, zlib decompressor and pickle
unpickler; change only `io.BufferedReader`'s output buffer from its implicit
8 KiB default to **256 KiB**. Cache payload format, compression, exact calculations,
API, row order, diagnostic events, limits, entry count and TTL remain unchanged.
Existing payloads remain readable; the cold calculation is not changed.

The original reader can only decompress as much output as `readinto()` requests.
The small default buffer repeatedly splits the output of a 64 KiB compressed
input block. Each call then retrieves/passes `unconsumed_tail` again, produces
another bytes object and copies output into the buffer. A larger bounded output
buffer reduces Python/zlib transitions and tail handling, while preserving
streaming and avoiding a complete uncompressed serialized buffer.

On this fixture, the default buffer made **1,454 decompressor calls** and passed
**32.129 MiB of unconsumed tails**. The selected variant made **365 calls** with
**zero unconsumed tails**. This identifies an avoidable local reader overhead.
It does not prove that all of the reported **11,161.207 ms** on the VPS came from
that overhead. The server has substantial swap usage; paging, allocation and
actual product-detail structure were not profiled in production.

## Comparable fixture and method

Run locally from the repository root with backend dependencies installed:

```sh
PYTHONPATH=backend python backend/scripts/benchmark_product_analytics_restore.py
```

The benchmark never connects to PostgreSQL, CatalogVR or the production server.
It uses 27,016 rows built by the real `merge_periods()`: current/previous metrics,
exact floats, dates, names, images, five grouping/filter labels, properties,
stocks, prices and 1,000 manager clients. Each SKU has an additional deterministic
high-entropy nested description. Its compressed payload is **22.771 MiB**, close
to the reported 21.8 MiB; the previous 1.7 MiB repetitive fixture was not a good
comparison for this question. This is a synthetic composition, not a copy of
production data, and does not simulate production swap pressure.

CPython 3.12.14, Linux. Every variant runs in its own fresh subprocess on identical
source data. GC remains enabled; `gc.collect()` runs before each pass. Five time
samples use `perf_counter()` **without tracemalloc**. Their median is reported.
A separate pass measures peak additional Python allocations with tracemalloc,
started after source and compressed payload creation. Every pass compares the
entire reconstructed result with the original, outside the timed interval.
The selected variant is also checked against the production `_restore()`.
The final recorded run had no concurrent test/benchmark process.

Counters are added only in the comparison reader; production has no counters.
Raw timings, Linux process peak RSS (KiB), payload sizes and counters are recorded
in `product_analytics_restore_benchmark.json`. RSS includes source fixture,
payload, restore result, setup and interpreter/allocator overhead. It is not the
same metric as the additional tracemalloc peak, and should not be treated as VPS
memory usage. Source construction is outside the timed/profiled region.

| Variant | Median restore, ms | Additional allocation peak, MiB | Decompress calls | Repassed tails, MiB |
| --- | ---: | ---: | ---: | ---: |
| baseline_8k | 354.857 | 109.938 | 1454 | 32.129 |
| buffer_64k | 376.010 | 109.993 | 889 | 14.555 |
| buffer_256k | 258.147 | 110.213 | 365 | 0.000 |
| buffer_1m | 253.655 | 110.963 | 365 | 0.000 |
| input_16k | 373.830 | 109.938 | 2545 | 5.983 |
| input_256k_buffer_256k | 247.352 | 110.213 | 183 | 7.952 |
| whole_decompress | 178.123 | 144.874 | 0 | 0.000 |

The selected variant reduced median restore time from **354.857 to 258.147 ms**
(**27.25% less time**, approximately 1.37× throughput). Additional allocation
peak increased from **109.938 to 110.213 MiB**: **0.275 MiB / 0.25%**. Linux process
peak RSS was **386.91 / 387.29 MiB**. These peaks include the reconstructed data;
the result itself cannot be omitted without changing behavior.

An earlier randomized seven-round comparison confirmed the effect: default
buffer median **338.496 ms**, selected buffer **242.717 ms**. Increasing compressed
input to 256 KiB was similar (**243.783 ms**) in that randomized run and repassed
about 7.95 MiB of tails. Its small extra speed gain in the final run was not
consistent enough to justify changing the input algorithm as well. A 1 MiB output
buffer gave a similar speed at a higher temporary peak. Full `zlib.decompress`
plus `pickle.loads` was faster, but added **34.94 MiB** to the original allocation
peak and retained a whole serialized buffer during unpickling, so it was rejected
for the memory-constrained VPS.

The baseline regression tests also cover the smaller original fixture. New
reader tests cover compressible/incompressible data around 8/64/256 KiB and
1 MiB boundaries, EOF, truncated/corrupt streams, large individual fields,
float bit patterns (including signed zero), dates, shared nested aliases, exact
row ordering and compatibility with the original reader/payload format.

## Limits and remaining risks

The observed CPU improvement is local evidence, not a prediction that the
production 11.16 s will decrease by the same percentage. No heavy production test
or deployment was performed. Compare existing `restore_ms` under comparable load
after an independently authorized rollout. `postprocess_ms`, `hit`, `stored` and
`oversized` instrumentation is unchanged.

The 256 KiB buffer bounds the new buffering allocation. Restoration still builds
the entire object graph and pickle memo, so swapping and concurrent responses
can dominate latency/RAM. Compression ratios and detail structures alter reader
call counts. Cache limits still bound retained entries rather than total process
memory; they are not increased here.

All new and relevant existing cache/analytics/diagnostic tests pass. Three
pre-existing VRCatalog tree tests fail on both the baseline main tree and this
change. Therefore the full-suite acceptance criterion is not literally met;
there are no newly failing tests. Fixing those unrelated tree/client definitions
would require a separate change.
