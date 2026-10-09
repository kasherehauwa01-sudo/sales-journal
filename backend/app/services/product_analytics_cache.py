"""Небольшой single-flight кеш тяжёлых наборов товарной аналитики."""
from __future__ import annotations

import asyncio
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


@dataclass
class CacheEntry:
    created_at: float
    value: Any
    size: int


_cache: OrderedDict[str, CacheEntry] = OrderedDict()
_locks: dict[str, asyncio.Lock] = {}
_cache_bytes = 0
log = logging.getLogger(__name__)


def _log_cache(event: str, *, phase: str, size: int | None = None,
               stored: bool | None = None, reason: str | None = None) -> None:
    # Never include keys or values: both can contain report filters or personal data.
    # _bounded_size stops early for oversized datasets; their size is a lower bound.
    fields = {
        "cache_event": event,
        "cache_phase": phase,
        "dataset_size_mb": size / (1024 * 1024) if size is not None else None,
        "size_is_lower_bound": size is not None and size > CACHE_MAX_ENTRY_BYTES,
        "cache_stored": stored,
        "cache_reason": reason,
        "cache_entries": len(_cache),
        "cache_size_mb": _cache_bytes / (1024 * 1024),
    }
    log.info(
        "product_analytics_cache event=%s phase=%s dataset_size_mb=%s "
        "size_is_lower_bound=%s stored=%s reason=%s entries=%s cache_size_mb=%s",
        *fields.values(), extra=fields,
    )


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


def _fresh(key: str, now: float, *, phase: str = "lookup"):
    global _cache_bytes
    entry = _cache.get(key)
    if not entry:
        _log_cache("miss", phase=phase)
        return None
    if now - entry.created_at >= CACHE_TTL_SECONDS:
        _cache_bytes -= entry.size
        del _cache[key]
        _log_cache("expired", phase=phase)
        return None
    _cache.move_to_end(key)
    _log_cache("hit", phase=phase)
    return entry.value


def _store(key: str, value: Any, now: float) -> None:
    global _cache_bytes
    size = _bounded_size(value)
    if size > CACHE_MAX_ENTRY_BYTES:
        _log_cache("oversized", phase="store", size=size, stored=False,
                   reason="entry_size_limit")
        return
    previous = _cache.pop(key, None)
    if previous:
        _cache_bytes -= previous.size
    while _cache and (_cache_bytes + size > CACHE_MAX_TOTAL_BYTES or len(_cache) >= CACHE_MAX_ENTRIES):
        reason = "total_size_limit" if _cache_bytes + size > CACHE_MAX_TOTAL_BYTES else "entry_count_limit"
        _, removed = _cache.popitem(last=False)
        _cache_bytes -= removed.size
        _log_cache("evicted", phase="store", size=removed.size, stored=False, reason=reason)
    _cache[key] = CacheEntry(now, value, size)
    _cache_bytes += size
    _log_cache("stored", phase="store", size=size, stored=True)


async def cached_product_dataset(key: str, loader: Callable[[], Awaitable[Any]]):
    """Возвращает набор из кеша и объединяет одновременные одинаковые расчёты."""
    cached = _fresh(key, time.monotonic())
    if cached is not None:
        return cached, True
    lock = _locks.setdefault(key, asyncio.Lock())
    try:
        async with lock:
            cached = _fresh(key, time.monotonic(), phase="recheck")
            if cached is not None:
                return cached, True
            value = await loader()
            _store(key, value, time.monotonic())
            return value, False
    finally:
        if not lock.locked() and _locks.get(key) is lock:
            _locks.pop(key, None)
