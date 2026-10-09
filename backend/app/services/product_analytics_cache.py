"""Ограниченный single-flight кеш тяжёлых наборов товарной аналитики."""
from __future__ import annotations

import asyncio
import hashlib
import logging
import sys
import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

CACHE_TTL_SECONDS = 120
CACHE_MAX_ENTRIES = 2
CACHE_MAX_ENTRY_BYTES = 32 * 1024 * 1024
CACHE_MAX_TOTAL_BYTES = 48 * 1024 * 1024
_MIB = 1024 * 1024

logger = logging.getLogger(__name__)


@dataclass
class CacheEntry:
    created_at: float
    value: Any
    size: int


_cache: OrderedDict[str, CacheEntry] = OrderedDict()
_locks: dict[str, asyncio.Lock] = {}
_cache_bytes = 0


def _key_id(key: str) -> str:
    """Возвращает безопасный идентификатор, не раскрывая параметры фильтров."""
    return hashlib.sha256(key.encode()).hexdigest()[:12]


def _cache_state() -> tuple[int, float]:
    return len(_cache), _cache_bytes / _MIB


def _log(event: str, key: str, **fields: Any) -> None:
    entries, total_mb = _cache_state()
    details = " ".join(f"{name}={value}" for name, value in fields.items())
    logger.info(
        "product_analytics_cache event=%s key_id=%s entries=%s total_mb=%.3f%s%s",
        event,
        _key_id(key),
        entries,
        total_mb,
        " " if details else "",
        details,
    )


def _bounded_size(value: Any, limit: int | None = None) -> int:
    """Оценивает память без создания большой сериализованной копии."""
    if limit is None:
        limit = CACHE_MAX_ENTRY_BYTES + 1
    seen: set[int] = set()
    stack = [value]
    total = 0
    while stack and total <= limit:
        item = stack.pop()
        identity = id(item)
        if identity in seen:
            continue
        seen.add(identity)
        total += sys.getsizeof(item)
        if isinstance(item, dict):
            stack.extend(item.keys())
            stack.extend(item.values())
        elif isinstance(item, (list, tuple, set, frozenset)):
            stack.extend(item)
    return total


def invalidate_product_analytics_cache() -> None:
    """Сбрасывает локальный кеш после изменения продаж."""
    global _cache_bytes
    _cache.clear()
    _cache_bytes = 0


def _fresh(key: str, now: float) -> tuple[Any, str]:
    global _cache_bytes
    entry = _cache.get(key)
    if not entry:
        _log("miss", key)
        return None, "miss"
    if now - entry.created_at >= CACHE_TTL_SECONDS:
        _cache_bytes -= entry.size
        del _cache[key]
        _log("expired", key, entry_mb=f"{entry.size / _MIB:.3f}")
        return None, "expired"
    _cache.move_to_end(key)
    _log("hit", key, entry_mb=f"{entry.size / _MIB:.3f}")
    return entry.value, "hit"


def _store(key: str, value: Any, now: float) -> bool:
    global _cache_bytes
    size = _bounded_size(value)
    size_mb = size / _MIB
    if size > CACHE_MAX_ENTRY_BYTES:
        _log("oversized", key, result_mb=f"{size_mb:.3f}", saved="false", reason="entry_limit")
        return False
    previous = _cache.pop(key, None)
    if previous:
        _cache_bytes -= previous.size
    while _cache and (
        _cache_bytes + size > CACHE_MAX_TOTAL_BYTES
        or len(_cache) >= CACHE_MAX_ENTRIES
    ):
        evicted_key, removed = _cache.popitem(last=False)
        _cache_bytes -= removed.size
        _log("evicted", evicted_key, entry_mb=f"{removed.size / _MIB:.3f}", reason="cache_limit")
    _cache[key] = CacheEntry(now, value, size)
    _cache_bytes += size
    _log("stored", key, result_mb=f"{size_mb:.3f}", saved="true")
    return True


async def cached_product_dataset(key: str, loader: Callable[[], Awaitable[Any]]):
    """Возвращает набор из кеша и объединяет одновременные расчёты."""
    cached, status = _fresh(key, time.monotonic())
    if status == "hit":
        return cached, True
    lock = _locks.setdefault(key, asyncio.Lock())
    try:
        async with lock:
            cached, status = _fresh(key, time.monotonic())
            if status == "hit":
                return cached, True
            value = await loader()
            _store(key, value, time.monotonic())
            return value, False
    finally:
        if not lock.locked() and _locks.get(key) is lock:
            _locks.pop(key, None)
