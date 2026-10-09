import asyncio
import logging

from app.services import product_analytics_cache as cache


def setup_function():
    cache.invalidate_product_analytics_cache()
    cache._locks.clear()


def _load(value):
    async def loader():
        return value
    return loader


def test_cache_logs_miss_store_and_hit_without_full_key(caplog):
    caplog.set_level(logging.INFO, logger=cache.__name__)
    secret_key = "client=Иван Иванов&token=not-for-logs"
    assert asyncio.run(cache.cached_product_dataset(secret_key, _load({"rows": [1]})))[1] is False
    assert asyncio.run(cache.cached_product_dataset(secret_key, _load({"rows": [2]})))[1] is True
    assert "event=miss" in caplog.text
    assert "event=stored" in caplog.text and "saved=true" in caplog.text
    assert "event=hit" in caplog.text
    assert "Иван Иванов" not in caplog.text and "not-for-logs" not in caplog.text


def test_cache_logs_expired_reason(monkeypatch, caplog):
    caplog.set_level(logging.INFO, logger=cache.__name__)
    asyncio.run(cache.cached_product_dataset("expired-key", _load([1])))
    entry = cache._cache["expired-key"]
    entry.created_at -= cache.CACHE_TTL_SECONDS + 1
    asyncio.run(cache.cached_product_dataset("expired-key", _load([2])))
    assert "event=expired" in caplog.text


def test_cache_logs_oversized_result_and_does_not_store(monkeypatch, caplog):
    caplog.set_level(logging.INFO, logger=cache.__name__)
    monkeypatch.setattr(cache, "CACHE_MAX_ENTRY_BYTES", 1)
    asyncio.run(cache.cached_product_dataset("large-key", _load([1, 2, 3])))
    assert "large-key" not in cache._cache
    assert "event=oversized" in caplog.text
    assert "saved=false" in caplog.text and "reason=entry_limit" in caplog.text


def test_cache_logs_eviction_and_current_memory_state(monkeypatch, caplog):
    caplog.set_level(logging.INFO, logger=cache.__name__)
    monkeypatch.setattr(cache, "CACHE_MAX_ENTRIES", 1)
    asyncio.run(cache.cached_product_dataset("first-key", _load([1])))
    asyncio.run(cache.cached_product_dataset("second-key", _load([2])))
    assert list(cache._cache) == ["second-key"]
    assert "event=evicted" in caplog.text
    assert "entries=1" in caplog.text and "total_mb=" in caplog.text
