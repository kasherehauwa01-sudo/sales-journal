"""Bound heavy analytics for the whole response, without blocking the event loop."""
import asyncio
import contextvars
import fcntl
import logging
import os
import resource
import time
import weakref
from contextlib import asynccontextmanager

from fastapi import HTTPException

WAIT_SECONDS = 10.0
MAX_WAITERS = 8
MIN_AVAILABLE_BYTES = 256 * 1024 * 1024
LOCK_PATH = '/tmp/sales-journal-product-analytics.lock'
_states = weakref.WeakKeyDictionary()
_owner = contextvars.ContextVar('product_analytics_owner', default=None)
log = logging.getLogger(__name__)


def memory_snapshot():
    rss = None
    swap = None
    try:
        with open('/proc/self/statm') as source:
            rss = int(source.read().split()[1]) * os.sysconf('SC_PAGE_SIZE')
    except (OSError, ValueError, IndexError):
        pass
    try:
        with open('/proc/self/status') as source:
            for line in source:
                if line.startswith('VmSwap:'):
                    swap = int(line.split()[1]) * 1024
                    break
    except (OSError, ValueError, IndexError):
        pass
    return {'rss_bytes': rss, 'swap_bytes': swap, 'process_peak_rss_bytes': resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024}


def available_memory():
    """Available host/container RAM, never swap; read-only Linux metrics."""
    candidates = []
    try:
        with open('/proc/meminfo') as source:
            for line in source:
                if line.startswith('MemAvailable:'):
                    candidates.append(int(line.split()[1]) * 1024)
                    break
    except (OSError, ValueError, IndexError):
        pass
    for limit_path, usage_path in (
        ('/sys/fs/cgroup/memory.max', '/sys/fs/cgroup/memory.current'),
        ('/sys/fs/cgroup/memory/memory.limit_in_bytes', '/sys/fs/cgroup/memory/memory.usage_in_bytes'),
    ):
        try:
            with open(limit_path) as source:
                limit = source.read().strip()
            if limit == 'max':
                continue
            with open(usage_path) as source:
                usage = int(source.read())
            candidates.append(max(0, int(limit) - usage))
        except (OSError, ValueError):
            pass
    return min(candidates) if candidates else None


def ensure_headroom():
    available = available_memory()
    if available is not None and available < MIN_AVAILABLE_BYTES:
        diagnostics('rejected', reason='memory_headroom', available_bytes=available)
        raise _busy()


def diagnostics(stage, **counts):
    state = _states.get(asyncio.get_running_loop())
    fields = {'analytics_stage': stage, 'heavy_active': state['active'] if state else 0,
              'heavy_waiters': state['waiters'] if state else 0, **memory_snapshot(), **counts}
    log.info('product_analytics runtime %s', fields, extra=fields)


def _busy():
    return HTTPException(503, 'Товарная аналитика занята. Повторите запрос позже.', headers={'Retry-After': '10'})


@asynccontextmanager
async def heavy_operation(stage='dataset'):
    task = asyncio.current_task()
    if _owner.get() is task:
        yield
        return
    loop = asyncio.get_running_loop()
    state = _states.setdefault(loop, {'lock': asyncio.Lock(), 'waiters': 0, 'active': 0})
    if state['waiters'] >= MAX_WAITERS:
        diagnostics('rejected', reason='queue_full')
        raise _busy()
    state['waiters'] += 1
    started = time.perf_counter()
    acquired = False
    descriptor = None
    token = None
    try:
        async with asyncio.timeout(WAIT_SECONDS):
            await state['lock'].acquire()
            acquired = True
            # Shared by Uvicorn workers in the same container/filesystem.
            descriptor = os.open(LOCK_PATH, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
            while True:
                try:
                    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    await asyncio.sleep(0.05)
        ensure_headroom()
        state['waiters'] -= 1
        state['active'] = 1
        token = _owner.set(task)
        diagnostics('admitted', wait_ms=(time.perf_counter() - started) * 1000)
        try:
            yield
        finally:
            diagnostics(stage, elapsed_ms=(time.perf_counter() - started) * 1000)
    except TimeoutError:
        if token is not None:
            raise  # A loader's own TimeoutError is not an admission timeout.
        diagnostics('rejected', reason='wait_timeout')
        raise _busy() from None
    except OSError:
        if token is not None:
            raise
        diagnostics('rejected', reason='lock_unavailable')
        raise _busy() from None
    finally:
        if token is not None:
            _owner.reset(token)
            state['active'] = 0
            diagnostics('released')
        else:
            state['waiters'] -= 1
        if descriptor is not None:
            os.close(descriptor)  # Also releases flock on failure/cancellation.
        if acquired:
            state['lock'].release()


async def run_cpu(function, *args):
    """Keep admission until a thread actually exits, including on cancellation."""
    worker = asyncio.create_task(asyncio.to_thread(function, *args))
    try:
        return await asyncio.shield(worker)
    except asyncio.CancelledError:
        while not worker.done():
            try:
                await asyncio.shield(worker)
            except asyncio.CancelledError:
                continue
            except Exception:
                break
        if worker.done() and not worker.cancelled():
            worker.exception()  # Consume any error without logging its contents.
        raise
