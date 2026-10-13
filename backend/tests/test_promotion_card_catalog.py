import asyncio
import pytest
from fastapi import HTTPException
from app.routers import promotion_candidates_report as router


def test_card_projection_supports_warehouses_and_manager():
    assert router._product_card_projection(
        {
            "stocks": [
                {"warehouse": "Авиаторов Зал+Склад", "quantity": 2},
                {"name": "Склад", "value": "invalid"},
            ],
            "properties": [{"name": "Менеджер ОЗ", "value": "Ответственный"}],
        }
    ) == {
        "stocks": [
            {"name": "Авиаторов Зал+Склад", "quantity": 2},
            {"name": "Склад", "quantity": None},
        ],
        "oz_manager": "Ответственный",
    }
    assert router._product_card_projection({}) == {"stocks": [], "oz_manager": None}


def test_single_product_card_request_keeps_leading_zero_and_projects(monkeypatch):
    async def lookup(products, *, project):
        assert products == [{"code": "001", "article": "02"}]
        return {
            "code:001": project({"stocks": {"Склад": 3}, "Менеджер ОЗ": "Менеджер"})
        }

    monkeypatch.setattr(router, "get_catalog_batch_info", lookup)
    monkeypatch.setattr(router, "ensure_headroom", lambda: None)
    result = asyncio.run(
        router.product_catalog(router.ProductCatalogRequest(code="001", article="02"))
    )
    assert result == {
        "stocks": [{"name": "Склад", "quantity": 3}],
        "oz_manager": "Менеджер",
    }


def test_card_failure_is_not_empty_success(monkeypatch):
    async def lookup(*args, **kwargs):
        return {}

    monkeypatch.setattr(router, "get_catalog_batch_info", lookup)
    monkeypatch.setattr(router, "ensure_headroom", lambda: None)
    with pytest.raises(HTTPException) as error:
        asyncio.run(router.product_catalog(router.ProductCatalogRequest(code="001")))
    assert error.value.status_code == 502
