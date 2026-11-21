import asyncio
from datetime import date
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, select, func
from sqlalchemy.dialects.postgresql import asyncpg

from app.models import Sale
from app.services import report_folders as folders, report_filters
from app.routers import product_reports, product_analytics, promotion_candidates_report
from app.routers.sales_dynamics_report import _sales_rows


@pytest.fixture(autouse=True)
def clear_cache():
    folders._cache.clear()
    yield
    folders._cache.clear()


def test_directory_and_paginated_products_use_real_folder_api(monkeypatch):
    calls = []
    def fetch(path, params):
        calls.append((path, params))
        start = (params['page'] - 1) * 100
        rows = [{'code': f' C{i} ', 'article': f' A{i} '} for i in range(start, min(start + 100, 101))]
        return {'items': rows, 'pagination': {'totalItems': 101}}
    monkeypatch.setattr(folders.vrcatalog, '_integration_get', fetch)
    result = asyncio.run(folders.resolve_folders(['Посуда|Стекло', 'Посуда', 'Посуда']))
    assert len(result) == 101
    assert ('c100', 'a100') in result
    assert [call[1]['page'] for call in calls] == [1, 2]
    assert calls[0][0] == 'products/search'
    assert calls[0][1]['folder'] == ('Посуда', 'Посуда|Стекло')
    assert calls[0][1]['inStockOnly'] == calls[0][1]['excludeYyy'] == 'false'
    assert calls[0][1]['sort'] == 'id'
    assert asyncio.run(folders.resolve_folders(['Посуда', 'Посуда|Стекло'])) == result
    assert len(calls) == 2


def test_empty_selection_empty_folder_and_failure_are_distinct(monkeypatch):
    def fetch(*args):
        return {'items': [], 'pagination': {'totalItems': 0}}
    monkeypatch.setattr(folders.vrcatalog, '_integration_get', fetch)
    assert asyncio.run(folders.resolve_folders([])) is None
    assert asyncio.run(folders.resolve_folders(['Empty'])) == ()
    assert folders.folder_item_condition(()) is False
    def fail(*args):
        raise OSError('offline')
    monkeypatch.setattr(folders.vrcatalog, '_integration_get', fail)
    with pytest.raises(HTTPException) as error:
        asyncio.run(folders.resolve_folders(['Unavailable']))
    assert error.value.status_code == 502
    assert ('Unavailable',) not in folders._cache


@pytest.mark.parametrize('payload', [[], {'items': [], 'pagination': {'totalItems': 10}}, {'items': [{}], 'pagination': {'totalItems': 1}}])
def test_invalid_or_incomplete_pages_never_produce_partial_reports(monkeypatch, payload):
    monkeypatch.setattr(folders.vrcatalog, '_integration_get', lambda *args: payload)
    with pytest.raises(HTTPException) as error:
        asyncio.run(folders.resolve_folders(['Folder']))
    assert error.value.status_code == 502
    assert not folders._cache


def test_folder_filter_uses_one_bounded_pair_array():
    pairs = tuple((f'code-{i}', 'article') for i in range(40000))
    condition = folders.folder_item_condition(pairs)
    compiled = condition.compile(dialect=asyncpg.dialect())
    arrays = [value for value in compiled.params.values() if isinstance(value, list)]
    assert len(arrays) == 1 and len(arrays[0]) == 40000
    assert 'ANY (' in str(compiled)
    assert len(compiled.params) < 20


def test_folder_totals_exclude_other_items_and_do_not_mix_code_article_pairs(monkeypatch):
    monkeypatch.setattr(report_filters, 'array_membership', lambda column, values, **kwargs: column.in_(values))
    engine = create_engine('sqlite://')
    with engine.begin() as db:
        db.connection.driver_connection.create_function('concat', -1, lambda *values: ''.join(str(v) for v in values))
        for statement in [
            'CREATE TABLE sales(id INTEGER, sale_date DATE, total_amount NUMERIC)',
            'CREATE TABLE sale_items(id INTEGER, sale_id INTEGER, code TEXT, article TEXT, quantity NUMERIC, actual_price NUMERIC)',
            "INSERT INTO sales VALUES (1,'2026-09-01',1000),(2,'2026-09-01',300)",
            "INSERT INTO sale_items VALUES (1,1,' A ',' X ',2,100),(2,1,'B','Y',4,200),(3,2,'A','Y',3,100)",
        ]:
            db.exec_driver_sql(statement)
        pairs = (('a', 'x'), ('b', 'x'))
        rows = _sales_rows(date(2026, 9, 1), date(2026, 9, 30), None, None, folders=pairs)
        result = db.execute(select(rows)).all()
        assert len(result) == 1
        assert result[0].total_amount == 200
        assert result[0].items_count == 2
        assert db.execute(select(Sale.id).where(*report_filters.brand_sale_conditions([], ()))).all() == []
        all_rows = _sales_rows(date(2026, 9, 1), date(2026, 9, 30), None, None)
        assert db.scalar(select(func.sum(all_rows.c.total_amount))) == 1300


def test_post_reports_accept_folders_and_product_report_does_not_require_manual_products(monkeypatch):
    loader = AsyncMock(return_value=(('c', 'a'),))
    monkeypatch.setattr(product_reports, 'resolve_folders', loader)
    for cls in [product_reports.ReportRequest, product_analytics.AnalyticsRequest, promotion_candidates_report.Request]:
        data = cls(date_from='2026-09-01', date_to='2026-09-30', folders=['Посуда'])
        assert data.model_dump()['folders'] == ['Посуда']
    data = product_reports.ReportRequest(date_from='2026-09-01', date_to='2026-09-30', folders=['Посуда'])
    conditions = asyncio.run(product_reports._conditions(data))
    assert not any(condition is False for condition in conditions)
    loader.assert_awaited_once_with(['Посуда'])


def test_folder_query_is_documented_and_rfm_unchanged():
    from app.main import app
    paths = app.openapi()['paths']
    for path in ['/api/reports/sales-dynamics', '/api/reports/store-analytics', '/api/reports/category-analytics', '/api/reports/analytics-signals', '/api/reports/horeca/{token}']:
        assert 'folders' in [p['name'] for p in paths[path]['get']['parameters']]
    assert 'folders' not in [p['name'] for p in paths['/api/reports/rfm']['get']['parameters']]


def test_catalog_request_encodes_repeated_folder_parameters(monkeypatch):
    from urllib.parse import parse_qs, urlparse
    captured = []
    class Response:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def read(self): return b'[]'
    def open_url(request, **kwargs):
        captured.append(parse_qs(urlparse(request.full_url).query))
        return Response()
    monkeypatch.setattr(folders.vrcatalog, 'urlopen', open_url)
    folders.vrcatalog._integration_get('products/search', {'folder': ['A|B', 'C&D']})
    assert captured[0]['folder'] == ['A|B', 'C&D']


def test_parallel_report_requests_share_one_catalog_load(monkeypatch):
    loader = AsyncMock(return_value=(("c", "a"),))
    monkeypatch.setattr(folders, "_load_folders", loader)
    async def run():
        values = await asyncio.gather(*(folders.resolve_folders(["Folder"]) for _ in range(6)))
        assert all(value == (("c", "a"),) for value in values)
    asyncio.run(run())
    loader.assert_awaited_once()
