import asyncio

import pytest
from fastapi import HTTPException

from app.routers import category_analytics_report
from app.services.category_analytics import NOT_FOUND, UNCATEGORIZED
from app.services.vrcatalog import VrCatalogError


def test_catalog_map_distinguishes_empty_category_and_missing_product(monkeypatch):
    async def category_map(_products):
        return {"code:found": {"code": "found", "category": None}}

    monkeypatch.setattr(category_analytics_report, "get_catalog_category_map", category_map)
    mapping, _ = asyncio.run(category_analytics_report._catalog_map([
        {"code": " FOUND ", "article": "A"},
        {"code": "missing", "article": "B"},
    ]))
    assert mapping == [("found", "a", UNCATEGORIZED), ("missing", "b", NOT_FOUND)]


def test_catalog_map_failure_is_a_controlled_integration_error(monkeypatch):
    async def category_map(_products):
        raise VrCatalogError("internal service URL and details")

    monkeypatch.setattr(category_analytics_report, "get_catalog_category_map", category_map)
    with pytest.raises(HTTPException) as caught:
        asyncio.run(category_analytics_report._catalog_map([{"code": "1"}]))
    assert caught.value.status_code == 502
    assert caught.value.detail == "Не удалось получить категории товаров из CatalogVR. Попробуйте повторить позже."
    assert "internal" not in caught.value.detail


def test_catalog_map_splits_large_input_into_sequential_batches(monkeypatch):
    calls=[]
    async def category_map(products):
        calls.append(len(products));return {}

    monkeypatch.setattr(category_analytics_report, "CATALOG_CATEGORY_MAP_LIMIT", 2)
    monkeypatch.setattr(category_analytics_report, "get_catalog_category_map", category_map)
    asyncio.run(category_analytics_report._catalog_map([{"code": str(index)} for index in range(5)]))
    assert calls == [2, 2, 1]
