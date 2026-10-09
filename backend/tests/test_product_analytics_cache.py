import asyncio

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
