import asyncio
import fcntl
import json
import logging
import threading
import time
from datetime import date
from types import SimpleNamespace

import pytest
from fastapi import FastAPI

from app.routers import product_analytics as router
from app.services import product_analytics_cache as cache
from app.services import product_analytics_runtime as runtime
from app.services import vrcatalog


@pytest.fixture(autouse=True)
def clean(monkeypatch, tmp_path):
    monkeypatch.setattr(runtime, 'LOCK_PATH', str(tmp_path / 'analytics.lock'))
    cache.invalidate_product_analytics_cache()
    vrcatalog.catalog_info_cache.clear()
    vrcatalog._prune_catalog_info_cache(time.monotonic())
    yield
    cache.invalidate_product_analytics_cache()
    vrcatalog.catalog_info_cache.clear()
    vrcatalog._prune_catalog_info_cache(time.monotonic())


@pytest.mark.parametrize('keys', [('same', 'same'), ('a', 'b')])
def test_builds_serialized_for_same_and_different_keys(keys):
    active = 0
    peak = 0
    calls = 0

    async def loader():
        nonlocal active, peak, calls
        calls += 1
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.01)
        active -= 1
        return ([{'value': 1}], None, None, None)

    async def run():
        return await asyncio.gather(*(cache.cached_product_dataset(key, loader) for key in keys))

    results = asyncio.run(run())
    assert peak == 1
    assert calls == (1 if keys[0] == keys[1] else 2)
    assert results[0][0] == results[1][0]


def test_restores_serialized_and_event_loop_responsive(monkeypatch):
    value = ([{'value': 1}], None, None, None)
    cache._store('a', value, time.monotonic())
    cache._store('b', value, time.monotonic())
    original = cache._restore
    active = 0
    peak = 0
    ticks = 0

    def restore(packed):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        time.sleep(0.03)
        result = original(packed)
        active -= 1
        return result

    monkeypatch.setattr(cache, '_restore', restore)

    async def loader():
        raise AssertionError('Must hit cache')

    async def heartbeat():
        nonlocal ticks
        for _ in range(15):
            await asyncio.sleep(0.005)
            ticks += 1

    async def run():
        await asyncio.gather(cache.cached_product_dataset('a', loader), cache.cached_product_dataset('b', loader), heartbeat())

    asyncio.run(run())
    assert peak == 1 and ticks == 15


def test_queue_limit_timeout_and_cancelled_waiter_cleanup(monkeypatch):
    monkeypatch.setattr(runtime, 'MAX_WAITERS', 1)
    monkeypatch.setattr(runtime, 'WAIT_SECONDS', 0.04)

    async def run():
        async with runtime.heavy_operation():
            waiter = asyncio.create_task(_acquire())
            await asyncio.sleep(0)
            with pytest.raises(runtime.HTTPException) as full:
                await asyncio.create_task(_acquire())
            assert full.value.status_code == 503
            waiter.cancel()
            with pytest.raises(asyncio.CancelledError):
                await waiter
            with pytest.raises(runtime.HTTPException) as timed:
                await asyncio.create_task(_acquire())
            assert timed.value.status_code == 503
        async with runtime.heavy_operation():
            pass
        state = runtime._states[asyncio.get_running_loop()]
        assert state['active'] == state['waiters'] == 0

    asyncio.run(run())


async def _acquire():
    async with runtime.heavy_operation():
        pass


def test_file_lock_excludes_other_workers(monkeypatch):
    monkeypatch.setattr(runtime, 'WAIT_SECONDS', 0.02)
    with open(runtime.LOCK_PATH, 'w') as held:
        fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(runtime.HTTPException) as error:
            asyncio.run(_acquire())
        assert error.value.status_code == 503
    asyncio.run(_acquire())


def test_cancelled_thread_keeps_slot_until_it_really_finishes():
    started, release = threading.Event(), threading.Event()
    second_started = asyncio.Event()

    def work():
        started.set()
        release.wait(2)

    async def first():
        async with runtime.heavy_operation():
            await runtime.run_cpu(work)

    async def second():
        async with runtime.heavy_operation():
            second_started.set()

    async def run():
        task = asyncio.create_task(first())
        while not started.is_set():
            await asyncio.sleep(0.001)
        task.cancel()
        next_task = asyncio.create_task(second())
        await asyncio.sleep(0.02)
        assert not second_started.is_set()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        await next_task

    asyncio.run(run())


def test_invalidation_during_threaded_store_never_publishes_stale_data(monkeypatch):
    original = cache._compact
    started, release = threading.Event(), threading.Event()

    def compact(value):
        started.set()
        release.wait(2)
        return original(value)

    monkeypatch.setattr(cache, '_compact', compact)

    async def loader():
        return ([{'value': 1}], None, None, None)

    async def run():
        task = asyncio.create_task(cache.cached_product_dataset('a', loader))
        while not started.is_set():
            await asyncio.sleep(0.001)
        cache.invalidate_product_analytics_cache()
        release.set()
        await task
        assert not cache._cache

    asyncio.run(run())


async def request(app, path, send):
    sent = False

    async def receive():
        nonlocal sent
        if not sent:
            sent = True
            return {'type': 'http.request', 'body': b'', 'more_body': False}
        await asyncio.sleep(10)
        return {'type': 'http.disconnect'}

    scope = {'type': 'http', 'asgi': {'version': '3.0'}, 'http_version': '1.1',
             'method': 'GET' if path == '/health' else 'POST', 'scheme': 'http',
             'path': path, 'raw_path': path.encode(), 'query_string': b'', 'headers': [],
             'server': ('test', 80), 'client': ('test', 1), 'root_path': ''}
    await app(scope, receive, send)


def test_route_holds_slot_through_sending_response_and_health_stays_available():
    app = FastAPI()
    routes = router.APIRouter(route_class=router.HeavyAnalyticsRoute)
    calls = 0
    in_send, release = asyncio.Event(), asyncio.Event()

    @routes.post('/heavy')
    async def heavy():
        nonlocal calls
        calls += 1
        return {'value': 1, 'date': date(2026, 1, 1)}

    @app.get('/health')
    async def health():
        return {'status': 'ok'}

    app.include_router(routes)
    bodies = []

    async def slow_send(message):
        if message['type'] == 'http.response.body':
            in_send.set()
            await release.wait()
            bodies.append(json.loads(message['body']))

    async def fast_send(message):
        if message['type'] == 'http.response.body':
            bodies.append(json.loads(message['body']))

    async def run():
        first = asyncio.create_task(request(app, '/heavy', slow_send))
        await in_send.wait()
        second = asyncio.create_task(request(app, '/heavy', fast_send))
        await asyncio.sleep(0.01)
        assert calls == 1
        await asyncio.wait_for(request(app, '/health', fast_send), 0.5)
        assert {'status': 'ok'} in bodies
        release.set()
        await asyncio.gather(first, second)
        assert calls == 2
        assert bodies.count({'value': 1, 'date': '2026-01-01'}) == 2

    asyncio.run(run())


def test_period_rows_stream_exact_aggregates_and_close_cursor():
    row = SimpleNamespace(article='A', code='1', name='Товар', revenue=1.123456789,
                          units=-2.0, checks=3, first_sale=date(2026, 1, 1), last_sale=date(2026, 9, 30))

    class Result:
        closed = False

        def __aiter__(self):
            async def rows():
                yield row
            return rows()

        async def close(self):
            self.closed = True

    result = Result()

    class Db:
        async def stream(self, query):
            assert query.get_execution_options()['yield_per'] == 512
            assert 'count(DISTINCT sales.id)' in str(query)
            return result

    data = router.AnalyticsRequest(date_from=date(2026, 1, 1), date_to=date(2026, 9, 30))
    rows = asyncio.run(router._period_rows(data, Db(), data.date_from, data.date_to, None))
    assert result.closed
    assert rows[0]['revenue'] == row.revenue and rows[0]['units'] == -2.0
    assert rows[0]['checks'] == 3 and rows[0]['first_sale'] == row.first_sale


def test_catalog_workers_bounded_and_partial_errors_preserve_all_successful_fields(monkeypatch, caplog):
    active = peak = 0
    lock = threading.Lock()
    calls = []

    def batch(payload):
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
            calls.append(len(payload['products']))
        try:
            time.sleep(0.005)
            if payload['products'][0]['code'] == '250':
                raise TimeoutError('PRIVATE-token-filter')
            return {'items': [dict(product, properties={'Цвет': 'Белый'}, stocks=[{'quantity': 7}], prices=[{'value': 1.123456789}]) for product in payload['products']]}
        finally:
            with lock:
                active -= 1

    monkeypatch.setattr(vrcatalog, '_integration_batch', batch)
    result = asyncio.run(vrcatalog.get_catalog_batch_info({'code': str(i)} for i in range(1200)))
    assert peak <= 2 and max(calls) <= 250 and len(calls) == 5
    assert len(result) == 950
    assert result['code:1199']['prices'][0]['value'] == 1.123456789
    assert 'code:250' not in vrcatalog.catalog_info_cache
    assert 'PRIVATE' not in caplog.text


def test_catalog_byte_budget_bounds_retention_without_truncating_result(monkeypatch):
    monkeypatch.setattr(vrcatalog, 'CATALOG_INFO_CACHE_MAX_BYTES', 4096)
    monkeypatch.setattr(vrcatalog, '_integration_batch', lambda payload: {'items': [dict(item, properties={'text': 'x' * 1000}) for item in payload['products']]})
    result = asyncio.run(vrcatalog.get_catalog_batch_info([{'code': str(i)} for i in range(50)]))
    assert len(result) == 50
    assert vrcatalog._catalog_info_bytes <= 4096
    assert len(vrcatalog.catalog_info_cache) < 50


def test_runtime_diagnostics_have_sizes_and_no_sensitive_values(caplog):
    caplog.set_level(logging.INFO, logger=runtime.__name__)

    async def loader():
        return ([{'name': 'PRIVATE-product'}], None, None, ['PRIVATE-customer'])

    asyncio.run(cache.cached_product_dataset('PRIVATE-filter-key', loader))
    records = [r for r in caplog.records if hasattr(r, 'heavy_active')]
    assert records and max(r.heavy_active for r in records) == 1
    assert all(r.process_peak_rss_bytes > 0 for r in records)
    assert 'PRIVATE' not in caplog.text


def test_catalog_cancellation_waits_for_both_workers_before_releasing_slot(monkeypatch):
    entered = 0
    lock = threading.Lock()
    release = threading.Event()

    def batch(payload):
        nonlocal entered
        with lock:
            entered += 1
        release.wait(2)
        return {'items': payload['products']}

    monkeypatch.setattr(vrcatalog, '_integration_batch', batch)

    async def run():
        task = asyncio.create_task(vrcatalog.get_catalog_batch_info([{'code': str(i)} for i in range(1000)]))
        while entered < 2:
            await asyncio.sleep(0.001)
        task.cancel()
        next_task = asyncio.create_task(_acquire())
        await asyncio.sleep(0.02)
        assert not next_task.done()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        await next_task
        assert entered == 2

    asyncio.run(run())


def test_streamed_manager_selection_matches_original(monkeypatch):
    from app.services.product_analytics import clients_for_managers
    values = ['клиент а', 'клиент б', '', 'без менеджера']
    mapping = {'клиент а': 'Иванов', 'клиент б': 'Петров'}

    async def get_managers():
        return mapping

    monkeypatch.setattr(router, 'get_client_managers', get_managers)

    class Result:
        closed = False

        async def partitions(self, size):
            yield values[:2]
            yield values[2:]

        async def close(self):
            self.closed = True

    result = Result()

    class Db:
        async def stream_scalars(self, query):
            assert query.get_execution_options()['yield_per'] == 512
            return result

    data = router.AnalyticsRequest(date_from=date(2026, 1, 1), date_to=date(2026, 9, 30), managers=['Иванов', 'Не заполнено'])
    actual = asyncio.run(router._manager_clients(data, Db(), date(2025, 1, 1), date(2025, 9, 30)))
    assert actual == clients_for_managers(values, mapping, data.managers)
    assert result.closed


def test_low_memory_is_controlled_503_and_releases_slot(monkeypatch):
    monkeypatch.setattr(runtime, 'available_memory', lambda: 10 * 1024 * 1024)
    with pytest.raises(runtime.HTTPException) as error:
        asyncio.run(_acquire())
    assert error.value.status_code == 503
    assert error.value.headers['Retry-After'] == '10'
    monkeypatch.setattr(runtime, 'available_memory', lambda: 1024 * 1024 * 1024)
    asyncio.run(_acquire())


def test_memory_pressure_mid_catalog_does_not_return_partial_report(monkeypatch):
    reads = 0

    def available():
        nonlocal reads
        reads += 1
        return 1024 * 1024 * 1024 if reads < 4 else 1

    monkeypatch.setattr(runtime, 'available_memory', available)
    monkeypatch.setattr(vrcatalog, '_integration_batch', lambda payload: {'items': payload['products']})
    with pytest.raises(runtime.HTTPException) as error:
        asyncio.run(vrcatalog.get_catalog_batch_info([{'code': str(i)} for i in range(2000)]))
    assert error.value.status_code == 503


def test_route_overload_has_standard_503_and_retry_after(monkeypatch):
    app = FastAPI()
    routes = router.APIRouter(route_class=router.HeavyAnalyticsRoute)

    @routes.post('/heavy')
    async def heavy():
        raise AssertionError('Queue must reject before endpoint execution')

    app.include_router(routes)
    monkeypatch.setattr(runtime, 'MAX_WAITERS', 0)
    messages = []

    async def send(message):
        messages.append(message)

    asyncio.run(request(app, '/heavy', send))
    start = next(message for message in messages if message['type'] == 'http.response.start')
    assert start['status'] == 503
    assert (b'retry-after', b'10') in start['headers']
    body = next(message['body'] for message in messages if message['type'] == 'http.response.body')
    assert set(json.loads(body)) == {'detail'}


def test_catalog_merge_keeps_offset_order_for_colliding_aliases(monkeypatch):
    def batch(payload):
        first = payload['products'][0]['code']
        if first == '0':
            time.sleep(0.02)  # Second batch deliberately finishes first.
        items = [dict(item, name='first' if first == '0' else 'last') for item in payload['products']]
        items.append({'code': 'collision', 'article': 'shared', 'name': 'first' if first == '0' else 'last'})
        return {'items': items}

    monkeypatch.setattr(vrcatalog, '_integration_batch', batch)
    result = asyncio.run(vrcatalog.get_catalog_batch_info([{'code': str(i)} for i in range(500)]))
    assert result['code:collision']['name'] == 'last'
    assert result['article:shared']['name'] == 'last'
