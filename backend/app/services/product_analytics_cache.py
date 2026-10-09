"""Небольшой single-flight кеш тяжёлых наборов товарной аналитики."""
from __future__ import annotations

import asyncio
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


@dataclass
class CacheEntry:
    created_at: float
    value: Any
    size: int


_cache: OrderedDict[str, CacheEntry] = OrderedDict()
_locks: dict[str, asyncio.Lock] = {}
_cache_bytes = 0


def _bounded_size(value: Any, limit: int = CACHE_MAX_ENTRY_BYTES + 1) -> int:
    """Оценивает память без создания большой сериализованной копии."""
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
            stack.extend(item.keys()); stack.extend(item.values())
        elif isinstance(item, (list, tuple, set, frozenset)):
            stack.extend(item)
    return total


def invalidate_product_analytics_cache() -> None:
    """Сбрасывает локальный кеш после изменения продаж."""
    global _cache_bytes
    _cache.clear()
    _cache_bytes = 0


def _fresh(key: str, now: float):
    global _cache_bytes
    entry = _cache.get(key)
    if not entry:
        return None
    if now - entry.created_at >= CACHE_TTL_SECONDS:
        _cache_bytes -= entry.size
        del _cache[key]
        return None
    _cache.move_to_end(key)
    return entry.value


def _store(key: str, value: Any, now: float) -> None:
    global _cache_bytes
    size = _bounded_size(value)
    if size > CACHE_MAX_ENTRY_BYTES:
        return
    previous = _cache.pop(key, None)
    if previous:
        _cache_bytes -= previous.size
    while _cache and (_cache_bytes + size > CACHE_MAX_TOTAL_BYTES or len(_cache) >= CACHE_MAX_ENTRIES):
        _, removed = _cache.popitem(last=False)
        _cache_bytes -= removed.size
    _cache[key] = CacheEntry(now, value, size)
    _cache_bytes += size


async def cached_product_dataset(key: str, loader: Callable[[], Awaitable[Any]]):
    """Возвращает набор из кеша и объединяет одновременные одинаковые расчёты."""
    cached = _fresh(key, time.monotonic())
    if cached is not None:
        return cached, True
    lock = _locks.setdefault(key, asyncio.Lock())
    try:
        async with lock:
            cached = _fresh(key, time.monotonic())
            if cached is not None:
                return cached, True
            value = await loader()
            _store(key, value, time.monotonic())
            return value, False
    finally:
        if not lock.locked() and _locks.get(key) is lock:
            _locks.pop(key, None)
