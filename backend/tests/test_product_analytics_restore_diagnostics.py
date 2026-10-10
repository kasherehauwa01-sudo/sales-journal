import asyncio
import logging
from datetime import date

import pytest

from app.routers import product_analytics as router
from app.services import product_analytics_cache as cache


@pytest.fixture(autouse=True)
def reset_cache():
    cache.invalidate_product_analytics_cache()
    yield
    cache.invalidate_product_analytics_cache()


@pytest.mark.parametrize('phase', ['lookup', 'recheck'])
@pytest.mark.parametrize('rows_count', [0, 3])
def test_restore_logs_exact_duration_size_and_row_count(monkeypatch, caplog, phase, rows_count):
    caplog.set_level(logging.INFO, logger=cache.__name__)
    secret = 'PRIVATE-product-filter-cache-key'
    value = ([{'name': secret} for _ in range(rows_count)], date(2025, 1, 1), date(2025, 2, 1), [secret])
    cache._store(secret, value, cache.time.monotonic())
    payload_size = len(cache._cache[secret].value.payload)
    caplog.clear()
    clock = iter([10.0, 37.6])
    monkeypatch.setattr(cache.time, 'perf_counter', lambda: next(clock))

    assert cache._fresh(secret, cache.time.monotonic(), phase=phase) == value
    records = [r for r in caplog.records if r.name == cache.__name__]
    assert [r.cache_event for r in records] == ['hit', 'restored']
    restored = records[1]
    assert restored.restore_ms == pytest.approx(27600)
    assert restored.compressed_size_bytes == payload_size
    assert restored.compressed_size_mb == payload_size / (1024 * 1024)
    assert restored.result_rows == rows_count
    assert restored.cache_phase == phase
    assert 'restore_ms=27600.000' in restored.getMessage()
    assert secret not in caplog.text
    assert secret not in repr([vars(record) for record in records])


def test_cold_then_warm_request_logs_restore_only_on_hit(monkeypatch, caplog):
    caplog.set_level(logging.INFO, logger=cache.__name__)
    calls = 0
    value = ([{'name': 'Private product'}], None, None, None)

    async def loader():
        nonlocal calls
        calls += 1
        return value

    async def run():
        assert await cache.cached_product_dataset('Private filter', loader) == (value, False)
        assert not any(getattr(r, 'cache_event', None) == 'restored' for r in caplog.records)
        assert await cache.cached_product_dataset('Private filter', loader) == (value, True)

    asyncio.run(run())
    assert calls == 1
    assert sum(getattr(r, 'cache_event', None) == 'restored' for r in caplog.records) == 1
    assert 'Private' not in caplog.text


@pytest.mark.parametrize('hit', [False, True])
def test_postprocess_timer_excludes_cache_restore_and_preserves_result(monkeypatch, caplog, hit):
    caplog.set_level(logging.INFO, logger=router.__name__)
    sensitive = 'PRIVATE-filter-product-key'
    request = router.AnalyticsRequest(date_from=date(2026, 1, 1), date_to=date(2026, 9, 30), brands=[sensitive], group_by='brand')
    result = ([{'name': sensitive}, {'name': sensitive}], date(2025, 1, 1), date(2025, 9, 30), [sensitive])
    grouped = [{'name': sensitive, 'revenue': 1.0}]
    clock = [1.0]

    class Db:
        async def connection(self, **kwargs):
            pass

        async def scalar(self, query):
            clock[0] += 5.0
            return 42

    async def cached(key, loader):
        clock[0] += 27.6
        return result, hit

    def group(rows, field):
        assert rows is result[0] and field == 'brand'
        clock[0] += 0.125
        return grouped

    monkeypatch.setattr(router, 'cached_product_dataset', cached)
    monkeypatch.setattr(router, 'group_rows', group)
    monkeypatch.setattr(router.time, 'perf_counter', lambda: clock[0])
    actual = asyncio.run(router._dataset(request, Db()))
    assert actual == (grouped, *result[1:])
    records = [r for r in caplog.records if hasattr(r, 'postprocess_ms')]
    assert len(records) == 1
    record = records[0]
    assert record.postprocess_ms == pytest.approx(125)
    assert record.cache_hit is hit
    assert record.input_rows == 2 and record.result_rows == 1
    assert 'postprocess_ms=125.000' in record.getMessage()
    assert sensitive not in caplog.text
    assert sensitive not in repr([vars(r) for r in caplog.records])
