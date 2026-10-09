import asyncio
import logging

import pytest

from app.services import product_analytics_cache as cache


def setup_function():
    cache.invalidate_product_analytics_cache()
    cache._locks.clear()


def test_cache_reuses_completed_calculation():
    calls = 0

    async def loader():
        nonlocal calls
        calls += 1
        return {"rows": [1, 2, 3]}

    async def run():
        first = await cache.cached_product_dataset("same", loader)
        second = await cache.cached_product_dataset("same", loader)
        return first, second

    (first, first_hit), (second, second_hit) = asyncio.run(run())
    assert first == second
    assert first_hit is False
    assert second_hit is True
    assert calls == 1


def test_cache_singleflight_combines_concurrent_calculations():
    calls = 0
    started = asyncio.Event()
    release = asyncio.Event()

    async def loader():
        nonlocal calls
        calls += 1
        started.set()
        await release.wait()
        return ["result"]

    async def run():
        first = asyncio.create_task(cache.cached_product_dataset("same", loader))
        await started.wait()
        second = asyncio.create_task(cache.cached_product_dataset("same", loader))
        release.set()
        return await asyncio.gather(first, second)

    results = asyncio.run(run())
    assert calls == 1
    assert results[0][0] == results[1][0] == ["result"]
    assert sorted(hit for _, hit in results) == [False, True]


def test_oversized_value_is_not_cached(monkeypatch):
    calls = 0
    monkeypatch.setattr(cache, "CACHE_MAX_ENTRY_BYTES", 1)

    async def loader():
        nonlocal calls
        calls += 1
        return {"large": "value"}

    async def run():
        await cache.cached_product_dataset("large", loader)
        await cache.cached_product_dataset("large", loader)

    asyncio.run(run())
    assert calls == 2
    assert not cache._cache


def test_invalidation_forces_new_calculation():
    calls = 0

    async def loader():
        nonlocal calls
        calls += 1
        return calls

    async def run():
        await cache.cached_product_dataset("same", loader)
        cache.invalidate_product_analytics_cache()
        return await cache.cached_product_dataset("same", loader)

    (value, hit) = asyncio.run(run())
    assert (value, hit, calls) == (2, False, 2)


def cache_records(caplog):
    return [record for record in caplog.records if record.name == cache.__name__]


def test_logs_cold_miss_store_and_hit_without_sensitive_data(caplog):
    caplog.set_level(logging.INFO, logger=cache.__name__)
    key = "private-filter-customer@example.test"
    value = {"report": "PRIVATE CUSTOMER REPORT"}

    async def loader():
        return value

    async def run():
        assert await cache.cached_product_dataset(key, loader) == (value, False)
        assert await cache.cached_product_dataset(key, loader) == (value, True)

    asyncio.run(run())
    records = cache_records(caplog)
    assert [(r.cache_event, r.cache_phase) for r in records] == [
        ("miss", "lookup"), ("miss", "recheck"), ("stored", "store"), ("hit", "lookup")
    ]
    stored = records[2]
    assert stored.cache_stored is True
    assert stored.cache_reason is None
    assert stored.dataset_size_mb == cache._bounded_size(value) / (1024 * 1024)
    assert stored.cache_entries == 1
    assert stored.cache_size_mb == stored.dataset_size_mb
    assert stored.size_is_lower_bound is False
    assert key not in repr([vars(r) for r in records])
    assert value["report"] not in repr([vars(r) for r in records])


def test_expired_entry_logs_expiration_and_reloads(monkeypatch, caplog):
    caplog.set_level(logging.INFO, logger=cache.__name__)
    clock = [100.0]
    monkeypatch.setattr(cache.time, "monotonic", lambda: clock[0])
    calls = 0

    async def loader():
        nonlocal calls
        calls += 1
        return [calls]

    async def run():
        await cache.cached_product_dataset("expires", loader)
        clock[0] += cache.CACHE_TTL_SECONDS
        return await cache.cached_product_dataset("expires", loader)

    assert asyncio.run(run()) == ([2], False)
    expired = [r for r in cache_records(caplog) if r.cache_event == "expired"]
    assert len(expired) == 1
    assert expired[0].cache_phase == "lookup"
    assert expired[0].cache_entries == 0
    assert expired[0].cache_size_mb == 0


def test_oversized_logs_rejection_and_keeps_existing_cache(monkeypatch, caplog):
    caplog.set_level(logging.INFO, logger=cache.__name__)
    value = "PRIVATE OVERSIZED REPORT"
    cache._store("existing", [1], cache.time.monotonic())
    previous_bytes = cache._cache_bytes
    monkeypatch.setattr(cache, "CACHE_MAX_ENTRY_BYTES", 1)

    async def loader():
        return value

    assert asyncio.run(cache.cached_product_dataset("private-large-key", loader)) == (value, False)
    rejected = [r for r in cache_records(caplog) if r.cache_event == "oversized"]
    assert len(rejected) == 1
    record = rejected[0]
    assert record.cache_stored is False
    assert record.cache_reason == "entry_size_limit"
    assert record.dataset_size_mb > 1 / (1024 * 1024)
    assert record.size_is_lower_bound is True
    assert record.cache_entries == 1
    assert record.cache_size_mb == previous_bytes / (1024 * 1024)
    assert list(cache._cache) == ["existing"]
    assert value not in caplog.text
    assert "private-large-key" not in caplog.text


@pytest.mark.parametrize("limit,reason", [
    ("CACHE_MAX_ENTRIES", "entry_count_limit"),
    ("CACHE_MAX_TOTAL_BYTES", "total_size_limit"),
])
def test_eviction_logs_limit_and_evicted_entry_misses(monkeypatch, caplog, limit, reason):
    caplog.set_level(logging.INFO, logger=cache.__name__)
    value = [1]
    size = cache._bounded_size(value)
    monkeypatch.setattr(cache, limit, 1 if limit == "CACHE_MAX_ENTRIES" else size)

    async def loader():
        return value

    async def run():
        await cache.cached_product_dataset("private-first-key", loader)
        await cache.cached_product_dataset("private-second-key", loader)
        assert list(cache._cache) == ["private-second-key"]
        return await cache.cached_product_dataset("private-first-key", loader)

    assert asyncio.run(run()) == (value, False)
    records = cache_records(caplog)
    evictions = [r for r in records if r.cache_event == "evicted"]
    assert len(evictions) == 2
    for record in evictions:
        assert record.cache_reason == reason
        assert record.cache_entries == 0
        assert record.cache_size_mb == 0
        assert record.dataset_size_mb == size / (1024 * 1024)
    assert [r.cache_event for r in records if r.cache_phase == "lookup"] == ["miss"] * 3
    assert cache._cache_bytes == size
    assert "private-first-key" not in caplog.text
    assert "private-second-key" not in caplog.text
