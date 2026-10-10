"""Synthetic memory test; no DB, HTTP or production access.

Use the same script with PYTHONPATH pointing at baseline or changed backend.
Only --mode bounded imports the new runtime. Run each case in a fresh process.
"""
import argparse
import asyncio
import base64
import gc
import json
import random
import resource
import time
import tracemalloc
from contextlib import asynccontextmanager

from app.services import product_analytics_cache as cache
from scripts.benchmark_product_analytics_cache import sample_dataset


def dataset(count):
    value = sample_dataset(count)
    rng = random.Random(17)
    for row in value[0]:
        row['properties'].append({'name': 'Описание', 'value': base64.b85encode(rng.randbytes(768)).decode()})
    return value


@asynccontextmanager
async def unchanged():
    yield


async def workload(mode, case, rows, wait_seconds):
    if mode == 'bounded':
        from app.services.product_analytics_runtime import heavy_operation
        from app.services import product_analytics_runtime as runtime
        runtime.WAIT_SECONDS = wait_seconds  # This benchmark process only.
        guard = heavy_operation
    else:
        guard = unchanged
    keys = ['a', 'b', 'c'] if case == 'cold' else ['a', 'a', 'a']
    if case == 'warm':
        value = dataset(rows)
        cache._store('a', value, time.monotonic())
        assert cache._cache
        del value
        gc.collect()
    active = peak = 0
    checks = []

    async def loader():
        # Emulate async SQL/Catalog waits so baseline different-key builds overlap.
        value = await asyncio.to_thread(dataset, rows)
        await asyncio.sleep(0.03)
        return value

    async def response(key):
        nonlocal active, peak
        async with guard():
            active += 1
            peak = max(peak, active)
            value, hit = await cache.cached_product_dataset(key, loader)
            assert len(value[0]) == rows
            checks.append((len(value[0]), sum(row['revenue'] for row in value[0]), value[1].isoformat()))
            # Hold the restored data for the response lifetime, as a slow sender/
            # encoder does. New routes hold admission across this whole lifetime.
            await asyncio.sleep(0.03)
            del value
            active -= 1

    gc.collect()
    tracemalloc.start()
    started = time.perf_counter()
    await asyncio.gather(*(response(key) for key in keys))
    elapsed = (time.perf_counter() - started) * 1000
    allocated_peak = tracemalloc.get_traced_memory()[1]
    tracemalloc.stop()
    assert len(set(checks)) == 1
    return {'mode': mode, 'case': case, 'rows': rows, 'responses': len(keys),
            'elapsed_ms_with_tracing': elapsed, 'peak_additional_bytes': allocated_peak,
            'process_peak_rss_kib': resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
            'peak_response_operations': peak, 'checksums': checks,
            'cache_retained_bytes': cache._cache_bytes}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mode', choices=['baseline', 'bounded'], required=True)
    parser.add_argument('--case', choices=['cold', 'warm'], required=True)
    parser.add_argument('--rows', type=int, default=27016)
    parser.add_argument('--wait-seconds', type=float, default=10.0, help='Synthetic process only; use 240 for traced serial cold comparisons')
    args = parser.parse_args()
    print(json.dumps(asyncio.run(workload(args.mode, args.case, args.rows, args.wait_seconds)), indent=2))


if __name__ == '__main__':
    main()
