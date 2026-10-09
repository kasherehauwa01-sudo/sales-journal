"""Небольшой single-flight кеш тяжёлых наборов товарной аналитики."""
from __future__ import annotations

import asyncio
import logging
import io
import pickle
import sys
import time
import zlib
from collections import OrderedDict
from itertools import chain
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
_lock_users: dict[str, int] = {}
_cache_bytes = 0
log = logging.getLogger(__name__)
_generation = 0


@dataclass(slots=True)
class CompressedDataset:
    payload: bytearray


class _EntryTooLarge(Exception):
    pass


class _CompressedWriter:
    def __init__(self):
        self.payload = bytearray()
        self.compressor = zlib.compressobj(level=1)

    def _append(self, chunk):
        # Count allocation capacity, not just logical compressed byte length.
        if sys.getsizeof(self.payload) + len(chunk) + sys.getsizeof(CompressedDataset(self.payload)) > CACHE_MAX_ENTRY_BYTES:
            raise _EntryTooLarge
        self.payload.extend(chunk)
        if sys.getsizeof(self.payload) + sys.getsizeof(CompressedDataset(self.payload)) > CACHE_MAX_ENTRY_BYTES:
            raise _EntryTooLarge

    def write(self, data):
        view = memoryview(data)
        for offset in range(0, len(view), 65536):
            self._append(self.compressor.compress(view[offset:offset + 65536]))
        return len(data)

    def finish(self):
        self._append(self.compressor.flush())
        return CompressedDataset(self.payload)


class _CompressedReader(io.RawIOBase):
    def __init__(self, payload):
        super().__init__()
        self.payload = memoryview(payload)
        self.position = 0
        self.decompressor = zlib.decompressobj()

    def readable(self):
        return True

    def readinto(self, buffer):
        while not self.decompressor.eof:
            chunk = self.decompressor.unconsumed_tail
            if not chunk:
                chunk = self.payload[self.position:self.position + 65536]
                self.position += len(chunk)
                if not chunk:
                    raise ValueError('Truncated internal analytics cache')
            data = self.decompressor.decompress(chunk, len(buffer))
            if data:
                buffer[:len(data)] = data
                return len(data)
        return 0


def _is_dataset(value):
    return (isinstance(value, tuple) and len(value) == 4
            and isinstance(value[0], list) and all(isinstance(row, dict) for row in value[0]))


def _compact(value):
    if not _is_dataset(value):
        return value
    writer = _CompressedWriter()
    # No pickle.dumps or full uncompressed serialized buffer. Pickle preserves
    # dates, exact floats, dict order and shared nested CatalogVR objects.
    pickle.Pickler(writer, protocol=pickle.HIGHEST_PROTOCOL).dump(value)
    return writer.finish()


def _restore(value):
    if not isinstance(value, CompressedDataset):
        return value
    # This payload is generated only in this process, never read from a client,
    # disk or remote cache. Do not expose this unpickler to external input.
    with io.BufferedReader(_CompressedReader(value.payload)) as reader:
        return pickle.Unpickler(reader).load()


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


class _SeenObjects:
    """Exact identity tracking without one Python int/set slot per object.

    Aligned identities use paged bitmaps (512 bytes per 32 KiB address range).
    Unaligned identities use a separate set, so no alignment assumption affects
    correctness. Particularly useful for hundreds of thousands of small values.
    """
    def __init__(self):
        self.pages = {}
        self.unaligned = set()

    def __contains__(self, identity):
        if identity & 7:
            return identity in self.unaligned
        page = self.pages.get(identity >> 15)
        slot = (identity & 32767) >> 3
        return page is not None and bool(page[slot >> 3] & (1 << (slot & 7)))

    def add(self, identity):
        if identity & 7:
            self.unaligned.add(identity)
            return
        index = identity >> 15
        page = self.pages.get(index)
        if page is None:
            page = self.pages[index] = bytearray(512)
        slot = (identity & 32767) >> 3
        page[slot >> 3] |= 1 << (slot & 7)


def _measure(roots, limit, seen):
    """Traverse lazily: auxiliary iterators scale with depth, not row count."""
    stack = [iter(roots)]
    total = 0
    while stack and total <= limit:
        try:
            item = next(stack[-1])
        except StopIteration:
            stack.pop()
            continue
        identity = id(item)
        if identity in seen:
            continue
        seen.add(identity)
        total += sys.getsizeof(item)
        if isinstance(item, dict):
            stack.append(chain(item.keys(), item.values()))
        elif isinstance(item, (list, tuple, set, frozenset)):
            stack.append(iter(item))
        elif isinstance(item, CompressedDataset):
            stack.append(iter((item.payload,)))
    return total


def _bounded_size(value: Any, limit: int = CACHE_MAX_ENTRY_BYTES + 1) -> int:
    """Оценивает память без создания большой сериализованной копии."""
    return _measure((value,), limit, _SeenObjects())


def dataset_memory_components(value):
    """Unique retained bytes; shared objects counted once, no data copies.

    Component assignment follows this order. A shared object belongs to the
    first component referencing it. Sizes describe Python objects, not RSS.
    """
    if not _is_dataset(value):
        return {}
    rows, old_from, old_to, managers = value
    seen = _SeenObjects()
    seen.add(id(value)); seen.add(id(rows))
    containers = sys.getsizeof(value) + sys.getsizeof(rows)
    for row in rows:
        if id(row) not in seen:
            containers += sys.getsizeof(row)
            seen.add(id(row))
    result = {'row_containers': containers}
    for field in ('properties', 'stocks', 'prices'):
        result[field] = _measure((row.get(field) for row in rows), float('inf'), seen)
    result['row_values'] = _measure(
        (item for row in rows for pair in row.items() for item in pair), float('inf'), seen)
    result['dates'] = _measure((old_from, old_to), float('inf'), seen)
    result['manager_clients'] = _measure((managers,), float('inf'), seen)
    return result


def invalidate_product_analytics_cache() -> None:
    """Сбрасывает локальный кеш после изменения продаж."""
    global _cache_bytes, _generation
    _generation += 1
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
    return _restore(entry.value)


def _store(key: str, value: Any, now: float) -> None:
    global _cache_bytes
    if _is_dataset(value) and log.isEnabledFor(logging.INFO):
        components = dataset_memory_components(value)
        log.info("product_analytics_cache memory_components_bytes=%s", components,
                 extra={"memory_components_bytes": components})
    try:
        value = _compact(value)
    except _EntryTooLarge:
        _log_cache("oversized", phase="store", size=CACHE_MAX_ENTRY_BYTES + 1,
                   stored=False, reason="entry_size_limit")
        return
    except (pickle.PickleError, TypeError, AttributeError) as exc:
        log.warning("product_analytics_cache encoding failed: %s", type(exc).__name__)
        _log_cache("not_stored", phase="store", stored=False, reason="encoding_error")
        return
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
    _lock_users[key] = _lock_users.get(key, 0) + 1
    try:
        async with lock:
            cached = _fresh(key, time.monotonic(), phase="recheck")
            if cached is not None:
                return cached, True
            generation = _generation
            value = await loader()
            if generation == _generation:
                _store(key, value, time.monotonic())
            return value, False
    finally:
        _lock_users[key] -= 1
        if _lock_users[key] == 0 and _locks.get(key) is lock:
            _lock_users.pop(key, None)
            _locks.pop(key, None)
