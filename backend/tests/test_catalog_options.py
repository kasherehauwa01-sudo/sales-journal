import asyncio
from urllib.error import HTTPError

import pytest
from fastapi import HTTPException

from app.routers import product_analytics as router
from app.services import vrcatalog


@pytest.fixture(autouse=True)
def clear_directory_cache():
    vrcatalog.directory_cache.clear()
    vrcatalog.directory_locks.clear()
    yield
    vrcatalog.directory_cache.clear()
    vrcatalog.directory_locks.clear()


@pytest.mark.parametrize('field,key', [('brand', 'brand'), ('manufacturer', 'manufacturer'), ('subcategory', 'section')])
@pytest.mark.parametrize('search', ['', 'Чай & кофе'])
def test_all_filters_load_more_than_100_values_and_cache_pages(monkeypatch, field, key, search):
    calls = []
    names = [f'Название {index}' for index in range(205)]

    def request(path, params=None):
        if path == 'integration/product-filters':
            return {'filters': [{'key': key}]}
        assert path == f'integration/product-filters/{key}/options'
        assert params['page_size'] == 100
        assert params['search'] == search
        page = params['page']
        calls.append(page)
        return {'data': {'options': [{'value': name, 'label': 'Не заменять значение'} for name in names[(page-1)*100:page*100]],
                         'pagination': {'page': page, 'total': 205, 'total_pages': 3, 'has_next': page < 3}}}

    monkeypatch.setattr(vrcatalog, '_integration_get', request)
    assert asyncio.run(router.catalog_options(field, search)) == names
    assert asyncio.run(router.catalog_options(field, search)) == names
    assert calls == [1, 2, 3]


@pytest.mark.parametrize('metadata', [{'has_next': False}, {'pages': 1}, {'total_pages': 1}, {'total': 100}])
def test_full_last_page_does_not_request_another_page(monkeypatch, metadata):
    calls = []

    async def options(key, **params):
        calls.append(params['page'])
        return {'items': [str(i) for i in range(100)], **metadata}

    monkeypatch.setattr(router, 'get_product_filter_options', options)
    assert len(asyncio.run(router._all_catalog_option_values('brand', ''))) == 100
    assert calls == [1]


@pytest.mark.parametrize('last_items', [[], ['Последнее название']])
def test_no_metadata_stops_at_empty_or_short_last_page(monkeypatch, last_items):
    calls = []

    async def options(key, **params):
        calls.append(params['page'])
        return [str(i) for i in range(100)] if params['page'] == 1 else last_items

    monkeypatch.setattr(router, 'get_product_filter_options', options)
    assert asyncio.run(router._all_catalog_option_values('brand', '')) == [str(i) for i in range(100)] + last_items
    assert calls == [1, 2]


def test_has_next_continues_short_pages_and_preserves_names(monkeypatch):
    calls = []

    async def options(key, **params):
        page = params['page']
        calls.append(page)
        return {'values': [{'name': 'Бренд А'}, {'label': 'Бренд Б'}] if page == 1 else [{'value': 'Бренд Б'}, {'title': 'Бренд В'}],
                'pagination': {'has_next': page == 1}}

    monkeypatch.setattr(router, 'get_product_filter_options', options)
    assert asyncio.run(router._all_catalog_option_values('brand', '')) == ['Бренд А', 'Бренд Б', 'Бренд В']
    assert calls == [1, 2]


def test_repeated_page_fails_without_infinite_loop(monkeypatch):
    calls = []

    async def options(key, **params):
        calls.append(params['page'])
        return {'items': [str(i) for i in range(100)], 'has_next': True}

    monkeypatch.setattr(router, 'get_product_filter_options', options)
    with pytest.raises(vrcatalog.VrCatalogError, match='повторил страницу'):
        asyncio.run(router._all_catalog_option_values('brand', ''))
    assert calls == [1, 2]


@pytest.mark.parametrize('error', [HTTPError('https://catalog.test', 422, 'Invalid', {}, None), TimeoutError('timed out')])
@pytest.mark.parametrize('failed_page', [1, 2])
def test_api_errors_return_502_without_partial_results(monkeypatch, error, failed_page):
    def request(path, params=None):
        if path == 'integration/product-filters':
            return {'items': []}
        if params['page'] == failed_page:
            raise error
        return {'items': [str(i) for i in range(100)], 'has_next': True}

    monkeypatch.setattr(vrcatalog, '_integration_get', request)
    with pytest.raises(HTTPException) as caught:
        asyncio.run(router.catalog_options('brand'))
    assert caught.value.status_code == 502


def test_search_has_separate_cache_key(monkeypatch):
    calls = []

    def request(path, params=None):
        if path == 'integration/product-filters':
            return []
        calls.append(params['search'])
        return {'items': [params['search'] or 'Все бренды'], 'has_next': False}

    monkeypatch.setattr(vrcatalog, '_integration_get', request)
    assert asyncio.run(router.catalog_options('brand')) == ['Все бренды']
    assert asyncio.run(router.catalog_options('brand', 'Бренд')) == ['Бренд']
    assert asyncio.run(router.catalog_options('brand', 'Бренд')) == ['Бренд']
    assert calls == ['', 'Бренд']


def test_stale_page_cache_is_reused_on_api_error(monkeypatch):
    monkeypatch.setattr(vrcatalog, '_integration_get', lambda *args: {'items': ['Бренд'], 'has_next': False})
    assert asyncio.run(router._all_catalog_option_values('brand', '')) == ['Бренд']
    for key, (_, value) in list(vrcatalog.directory_cache.items()):
        vrcatalog.directory_cache[key] = (vrcatalog.time.monotonic() - vrcatalog.DIRECTORY_CACHE_TTL - 1, value)

    def unavailable(*args):
        raise TimeoutError('timed out')

    monkeypatch.setattr(vrcatalog, '_integration_get', unavailable)
    assert asyncio.run(router._all_catalog_option_values('brand', '')) == ['Бренд']


def test_malformed_pagination_is_reported_as_catalog_error(monkeypatch):
    async def options(key, **params):
        return {'items': ['Бренд'], 'pagination': {'pages': 'invalid'}}

    monkeypatch.setattr(router, 'get_product_filter_options', options)
    with pytest.raises(vrcatalog.VrCatalogError, match='некорректную пагинацию'):
        asyncio.run(router._all_catalog_option_values('brand', ''))


def test_alias_fallback_still_loads_all_pages(monkeypatch):
    calls = []

    async def filters():
        raise vrcatalog.VrCatalogError('metadata unavailable')

    async def options(key, **params):
        calls.append((key, params['page']))
        if key == 'brand':
            raise vrcatalog.VrCatalogError('unknown filter')
        page = params['page']
        return {'items': ['Бренд А'] if page == 1 else ['Бренд Б'], 'has_next': page == 1}

    monkeypatch.setattr(router, 'get_product_filters', filters)
    monkeypatch.setattr(router, 'get_product_filter_options', options)
    assert asyncio.run(router.catalog_options('brand')) == ['Бренд А', 'Бренд Б']
    assert calls == [('brand', 1), ('Бренд', 1), ('Бренд', 2)]
