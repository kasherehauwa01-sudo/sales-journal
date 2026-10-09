import asyncio
import logging
import math
from datetime import date

import pytest
from fastapi.encoders import jsonable_encoder

from app.routers import product_analytics as router
from app.services import product_analytics_cache as cache
from app.services.product_analytics import GROUP_FIELDS, classify, filter_subcategories, filter_values, group_rows, summary
from scripts.benchmark_product_analytics_cache import sample_dataset


@pytest.fixture(autouse=True)
def empty_cache():
    cache.invalidate_product_analytics_cache()
    yield
    cache.invalidate_product_analytics_cache()
    assert not cache._locks
    assert not cache._lock_users


def test_27016_rows_fit_unchanged_limit_and_roundtrip_every_field():
    value = sample_dataset()
    calls = 0

    async def loader():
        nonlocal calls
        calls += 1
        return value

    async def run():
        first, cold = await cache.cached_product_dataset('large', loader)
        second, hit = await cache.cached_product_dataset('large', loader)
        assert not cold and hit
        assert second == first == value
        assert len(second[0]) == 27016
        assert isinstance(cache._cache['large'].value, cache.CompressedDataset)
        assert cache._cache['large'].size < cache.CACHE_MAX_ENTRY_BYTES
        assert cache._bounded_size(value, float('inf')) > cache.CACHE_MAX_ENTRY_BYTES
        assert sum(cache.dataset_memory_components(value).values()) == cache._bounded_size(value, float('inf'))

    asyncio.run(run())
    assert calls == 1


def test_exact_types_nested_detail_and_response_results_are_preserved():
    value = sample_dataset(20)
    rows = value[0]
    rows[0]['revenue'] = -0.0
    rows[0]['exact_float'] = math.nextafter(1.0, 2.0)
    rows[0]['nullable'] = None
    rows[1]['properties'] = rows[0]['properties']
    rows[2]['prices'] = {'special': [1, 1.1, date(2026, 2, 1)]}
    rows[3]['revenue'] = -123.45678901234567
    restored = cache._restore(cache._compact(value))
    assert restored == value
    assert math.copysign(1, restored[0][0]['revenue']) == -1
    assert restored[0][0]['exact_float'].hex() == rows[0]['exact_float'].hex()
    assert restored[0][0]['properties'] is restored[0][1]['properties']
    assert isinstance(restored[0][0]['first_sale'], date)
    for field in GROUP_FIELDS:
        before, after = group_rows(rows, field), group_rows(restored[0], field)
        assert after == before
        assert summary(after) == summary(before)
        assert classify(after) == classify(before)
        for section in ('top', 'new', 'growth', 'decline', 'stopped'):
            assert jsonable_encoder(router._sorted(after, section)) == jsonable_encoder(router._sorted(before, section))
    for field in ('brand', 'manufacturer', 'material'):
        assert filter_values(restored[0], field, [rows[0][field]]) == filter_values(rows, field, [rows[0][field]])
    assert filter_subcategories(restored[0], [rows[0]['subcategory']]) == filter_subcategories(rows, [rows[0]['subcategory']])
    assert jsonable_encoder(restored) == jsonable_encoder(value)
    restored[0][0]['properties'][0]['value'] = 'changed'
    assert rows[0]['properties'][0]['value'] == 'Прозрачный'


def test_component_profile_counts_shared_objects_once():
    value = sample_dataset(2)
    value[0].append(value[0][0])
    value[0][1]['stocks'] = value[0][0]['stocks']
    assert sum(cache.dataset_memory_components(value).values()) == cache._bounded_size(value, float('inf'))


def test_compressed_singleflight_and_cleanup_with_many_waiters():
    calls = 0
    started, release = asyncio.Event(), asyncio.Event()
    value = sample_dataset(100)

    async def loader():
        nonlocal calls
        calls += 1
        started.set()
        await release.wait()
        return value

    async def run():
        first = asyncio.create_task(cache.cached_product_dataset('same', loader))
        await started.wait()
        waiters = [asyncio.create_task(cache.cached_product_dataset('same', loader)) for _ in range(6)]
        await asyncio.sleep(0)
        waiters[0].cancel()
        release.set()
        results = await asyncio.gather(first, *waiters, return_exceptions=True)
        assert isinstance(results[1], asyncio.CancelledError)
        assert all(result[0] == value for result in [results[0], *results[2:]])
        assert calls == 1

    asyncio.run(run())


def test_invalidation_during_load_cannot_repopulate_old_result():
    started, release = asyncio.Event(), asyncio.Event()
    calls = 0

    async def loader():
        nonlocal calls
        calls += 1
        if calls == 1:
            started.set()
            await release.wait()
        return sample_dataset(calls)

    async def run():
        first = asyncio.create_task(cache.cached_product_dataset('same', loader))
        await started.wait()
        cache.invalidate_product_analytics_cache()
        release.set()
        await first
        assert not cache._cache
        result, hit = await cache.cached_product_dataset('same', loader)
        assert not hit and len(result[0]) == 2

    asyncio.run(run())


def test_compressed_entry_expiry_invalidation_and_diagnostics(monkeypatch, caplog):
    caplog.set_level(logging.INFO, logger=cache.__name__)
    clock = [100.0]
    monkeypatch.setattr(cache.time, 'monotonic', lambda: clock[0])
    calls = 0

    async def loader():
        nonlocal calls
        calls += 1
        return sample_dataset(10)

    async def run():
        await cache.cached_product_dataset('private-customer', loader)
        assert (await cache.cached_product_dataset('private-customer', loader))[1]
        clock[0] += cache.CACHE_TTL_SECONDS
        assert not (await cache.cached_product_dataset('private-customer', loader))[1]
        cache.invalidate_product_analytics_cache()
        assert not (await cache.cached_product_dataset('private-customer', loader))[1]

    asyncio.run(run())
    assert calls == 3
    assert {'hit', 'miss', 'stored', 'expired'} <= {getattr(r, 'cache_event', None) for r in caplog.records}
    assert any(hasattr(r, 'memory_components_bytes') for r in caplog.records)
    assert 'private-customer' not in caplog.text
    assert 'Товар' not in caplog.text


def test_compressed_limit_rejection_keeps_existing_cache(monkeypatch, caplog):
    caplog.set_level(logging.INFO, logger=cache.__name__)
    cache._store('existing', 1, cache.time.monotonic())
    monkeypatch.setattr(cache, 'CACHE_MAX_ENTRY_BYTES', 100)
    value = sample_dataset(100)
    cache._store('too-large', value, cache.time.monotonic())
    assert list(cache._cache) == ['existing']
    assert any(getattr(r, 'cache_event', None) == 'oversized' for r in caplog.records)


def test_dataset_reuses_catalog_across_groupings_and_invalidates_on_revision_and_filters(monkeypatch):
    source = sample_dataset(12)[0]
    catalog_calls = []
    period_calls = []

    class Db:
        revision = 10

        async def scalar(self, query):
            return self.revision

    db = Db()
    request = router.AnalyticsRequest(date_from=date(2026, 1, 1), date_to=date(2026, 9, 30), managers=['Менеджер'])

    async def managers(data, db, old_from, old_to):
        return ['клиент']

    async def period(data, db, start, end, clients):
        assert clients == ['клиент']
        period_calls.append((start, end))
        return [dict(row, revenue=row['revenue'] if start == request.date_from else row['previous_revenue']) for row in source]

    async def catalog(rows):
        catalog_calls.append(len(rows))
        return {row['key']: dict(row, section=row['subcategory']) for row in source}

    monkeypatch.setattr(router, '_manager_clients', managers)
    monkeypatch.setattr(router, '_period_rows', period)
    monkeypatch.setattr(router, '_catalog', catalog)

    async def run():
        # Compute the reference using the unmodified calculation pipeline.
        references = {}
        for grouping in GROUP_FIELDS:
            data = request.model_copy(update={'group_by': grouping})
            references[grouping] = await router._build_dataset(data, db)
        catalog_calls.clear()
        period_calls.clear()
        for _ in range(2):
            for grouping in GROUP_FIELDS:
                data = request.model_copy(update={'group_by': grouping})
                assert await router._dataset(data, db) == references[grouping]
        assert len(catalog_calls) == 1
        assert len(period_calls) == 2
        db.revision += 1
        await router._dataset(request, db)
        assert len(catalog_calls) == 2
        filtered = request.model_copy(update={'brands': [source[0]['brand']]})
        rows, *_ = await router._dataset(filtered, db)
        assert rows and all(row['brand'] == source[0]['brand'] for row in rows)
        assert len(catalog_calls) == 3
        cache.invalidate_product_analytics_cache()
        await router._dataset(filtered, db)
        assert len(catalog_calls) == 4
        with pytest.raises(router.HTTPException) as error:
            await router._dataset(request.model_copy(update={'group_by': 'invalid'}), db)
        assert error.value.status_code == 422

    asyncio.run(run())


def test_cache_keys_preserve_all_filters_but_share_grouping():
    request = router.AnalyticsRequest(date_from=date(2026, 1, 1), date_to=date(2026, 9, 30), brands=['А', 'Б'])
    base = router._dataset_cache_key(request, 10)
    assert base == router._dataset_cache_key(request.model_copy(update={'group_by': 'brand', 'brands': ['Б', 'А']}), 10)
    for changes in ({'brands': ['А']}, {'manufacturers': ['Завод']}, {'subcategories': ['Раздел']},
                    {'article': '123'}, {'search': 'чай'}, {'departments': ['Отдел']},
                    {'managers': ['Иванов']}, {'date_to': date(2026, 9, 29)},
                    {'compare_from': date(2025, 1, 1), 'compare_to': date(2025, 9, 30)}):
        assert router._dataset_cache_key(request.model_copy(update=changes), 10) != base
    assert router._dataset_cache_key(request, 11) != base


def test_streaming_codec_handles_large_fields_and_empty_datasets():
    value = sample_dataset(1)
    value[0][0]['properties'] = {'text': 'Очень длинная строка ' * 100000}
    assert cache._restore(cache._compact(value)) == value
    empty = ([], date(2025, 1, 1), date(2025, 2, 1), None)
    assert cache._restore(cache._compact(empty)) == empty


def test_encoding_failure_does_not_fail_successful_report():
    value = sample_dataset(1)
    value[0][0]['unexpected'] = lambda: None
    cache._store('unsupported', value, cache.time.monotonic())
    assert not cache._cache


def test_identity_bitmap_is_exact_and_size_matches_reference():
    seen = cache._SeenObjects()
    identities = [0, 8, 16, 32760, 32768, 32776, 1, 9, 32769]
    for identity in identities:
        assert identity not in seen
        seen.add(identity)
        assert identity in seen
    assert 24 not in seen and 32761 not in seen

    import sys
    value = sample_dataset(10)
    visited = set()

    def reference(obj):
        if id(obj) in visited:
            return 0
        visited.add(id(obj))
        size = sys.getsizeof(obj)
        if isinstance(obj, dict):
            return size + sum(reference(key) + reference(val) for key, val in obj.items())
        if isinstance(obj, (list, tuple, set, frozenset)):
            return size + sum(reference(item) for item in obj)
        return size

    assert cache._bounded_size(value, float('inf')) == reference(value)


def test_report_summary_and_export_match_uncached_pipeline(monkeypatch):
    from io import BytesIO
    from openpyxl import load_workbook

    source = sample_dataset(30)[0]
    # Include returned goods, a new SKU and a stopped SKU in API comparisons.
    source[0]['revenue'] = -20.125
    source[1]['previous_revenue'] = 0
    source[2]['revenue'] = 0
    catalog_calls = []

    class Db:
        async def scalar(self, query):
            return 42 if 'max(' in str(query) else 17

    db = Db()
    request = router.AnalyticsRequest(date_from=date(2026, 1, 1), date_to=date(2026, 9, 30),
                                      brands=[source[0]['brand'], source[1]['brand'], source[2]['brand']])

    async def managers(*args):
        return None

    async def period(data, db, start, end, clients):
        return [dict(row, revenue=row['revenue'] if start == request.date_from else row['previous_revenue']) for row in source]

    async def catalog(rows):
        catalog_calls.append(1)
        return {row['key']: dict(row, section=row['subcategory']) for row in source}

    async def uncached(data, db):
        return await router._build_dataset(data, db)

    monkeypatch.setattr(router, '_manager_clients', managers)
    monkeypatch.setattr(router, '_period_rows', period)
    monkeypatch.setattr(router, '_catalog', catalog)
    optimized = router._dataset

    def spreadsheet(response):
        book = load_workbook(BytesIO(response.body), read_only=True)
        try:
            return {sheet.title: list(sheet.values) for sheet in book}
        finally:
            book.close()

    async def responses():
        result = []
        for grouping in sorted(GROUP_FIELDS):
            data = request.model_copy(update={'group_by': grouping})
            result.append(await router.report_summary(data, db))
            for section in ('top', 'new', 'growth', 'decline', 'stopped'):
                result.append(await router.full_report(data, section=section, sort_by='revenue', limit=20, db=db))
            result.append(spreadsheet(await router.export(data, db)))
        return result

    async def run():
        monkeypatch.setattr(router, '_dataset', uncached)
        reference = await responses()
        catalog_calls.clear()
        monkeypatch.setattr(router, '_dataset', optimized)
        actual = await responses()
        assert jsonable_encoder(actual) == jsonable_encoder(reference)
        assert len(catalog_calls) == 1

    asyncio.run(run())
