"""Offline report filter regressions; no external requests or working databases."""
import asyncio
from datetime import date, datetime
from decimal import Decimal
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI, HTTPException
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import asyncpg

from app.database import get_db
from app.models import Sale
from app.routers import product_analytics as analytics, product_reports, sales_dynamics_report as dynamics
from app.services.clients_vr import ClientsVrError
from app.services.sales_dynamics_report import calculated_metrics


def data(**updates):
    return analytics.AnalyticsRequest(date_from=date(2026, 9, 1), date_to=date(2026, 9, 30), **updates)


class ClientStream:
    closed = False

    async def partitions(self, size):
        assert size == 512
        yield ["alpha", "beta"]
        yield ["gamma", "delta", "unassigned"]

    async def close(self):
        self.closed = True


class ClientsDb:
    def __init__(self):
        self.stream = ClientStream()
        self.query = None

    async def stream_scalars(self, query):
        self.query = query
        return self.stream

    async def scalars(self, query):
        self.query = query
        return SimpleNamespace(all=lambda: ["alpha", "beta", "gamma", "delta", "unassigned"])


@pytest.mark.parametrize("managers,types,expected", [
    ([], ["Розница"], ["alpha", "gamma", "unassigned"]),
    ([], ["Розница", "Оптовик", "Розница"], ["alpha", "beta", "gamma", "unassigned"]),
    (["M1", "M2"], ["Розница", "Оптовик"], ["alpha", "beta", "gamma"]),
    (["Не заполнено"], ["Розница"], ["unassigned"]),
])
def test_product_analytics_buyer_union_and_manager_intersection(monkeypatch, managers, types, expected):
    calls = []

    async def buyers(value):
        calls.append(value)
        return {"Розница": [" ALPHA ", "gamma", "unassigned"], "Оптовик": ["beta", "Alpha"]}[value]

    async def mapping():
        return {"alpha": "M1", "beta": "M2", "gamma": "M2", "delta": "Other"}

    monkeypatch.setattr(analytics, "get_buyer_type_clients", buyers)
    monkeypatch.setattr(analytics, "get_client_managers", mapping)
    request = data(buyer_types=types, managers=managers, departments=["Авиаторов", "ЦУМ"])
    db = ClientsDb()
    actual = asyncio.run(analytics._manager_clients(request, db, date(2025, 9, 1), date(2025, 9, 30)))
    assert actual == expected
    assert calls == list(dict.fromkeys(types))
    assert db.stream.closed
    compiled = db.query.compile(dialect=asyncpg.dialect())
    assert ["Авиаторов", "ЦУМ"] in compiled.params.values()
    # Both comparison periods receive the same scoped clients in bounded SQL.
    for start, end in [(request.date_from, request.date_to), (date(2025, 9, 1), date(2025, 9, 30))]:
        sql = select(Sale.id).where(*analytics._conditions(request, start, end, actual)).compile(dialect=asyncpg.dialect())
        assert "ANY (" in str(sql)
        assert expected in sql.params.values()
    detail = analytics.DetailRequest(**request.model_dump(), article_key="code:001")
    condition, _, _ = analytics._detail_product(detail)
    db = ClientsDb()
    assert asyncio.run(analytics._detail_manager_clients(detail, db, condition)) == expected
    assert "JOIN sale_items" in str(db.query)


def test_product_analytics_empty_selection_and_source_failures(monkeypatch):
    async def empty(_):
        return []

    monkeypatch.setattr(analytics, "get_buyer_type_clients", empty)
    assert asyncio.run(analytics._manager_clients(data(buyer_types=["Розница"]), object(), date(2025, 9, 1), date(2025, 9, 30))) == []
    assert asyncio.run(analytics._manager_clients(data(), object(), date(2025, 9, 1), date(2025, 9, 30))) is None

    async def failed(value):
        if value == "Розница":
            raise ClientsVrError("Source unavailable")
        return ["alpha"]

    monkeypatch.setattr(analytics, "get_buyer_type_clients", failed)
    request = data(buyer_types=["Оптовик", "Розница"])
    with pytest.raises(HTTPException) as error:
        asyncio.run(analytics._manager_clients(request, object(), date(2025, 9, 1), date(2025, 9, 30)))
    assert error.value.status_code == 502
    detail = analytics.DetailRequest(**request.model_dump(), article_key="code:001")
    condition, _, _ = analytics._detail_product(detail)
    with pytest.raises(HTTPException) as error:
        asyncio.run(analytics._detail_manager_clients(detail, object(), condition))
    assert error.value.status_code == 502


def test_buyer_filters_participate_in_cache_key():
    plain = analytics._dataset_cache_key(data(), 1)
    selected = data(buyer_types=["Розница", "Оптовик"])
    key = analytics._dataset_cache_key(selected, 1)
    assert key != plain
    assert key == analytics._dataset_cache_key(data(buyer_types=["Оптовик", "Розница"]), 1)
    assert key != analytics._dataset_cache_key(data(buyer_types=["Розница"]), 1)


@pytest.mark.parametrize("departments", [["Авиаторов", "ЦУМ"], "Авиаторов"])
def test_dynamics_accepts_many_and_legacy_single_departments(departments):
    query = dynamics._sales_rows(date(2026, 9, 1), date(2026, 9, 30), departments, None, brands=["Brand A", "Brand B"])
    compiled = select(query).compile(dialect=asyncpg.dialect())
    assert departments in compiled.params.values()
    assert ["brand a", "brand b"] in compiled.params.values()
    assert "catalog_lookups.status" in str(compiled)


def test_dynamics_http_propagates_all_filters_to_both_comparisons(monkeypatch):
    calls = []

    async def clients(db, manager, buyer):
        assert manager == ["M1", "M2"]
        assert buyer == ["Розница", "Оптовик"]
        return ["alpha"]

    async def metrics(db, start, end, departments, clients, client, brands=None):
        calls.append(("metrics", start))
        assert departments == ["Авиаторов", "ЦУМ"]
        assert clients == ["alpha"]
        assert brands == ["Brand A", "Brand B"]
        return calculated_metrics(100, 1, 2)

    async def chart(db, start, end, group, departments, clients, client, brands=None, **kwargs):
        calls.append(("chart", start))
        assert departments == ["Авиаторов", "ЦУМ"]
        assert brands == ["Brand A", "Brand B"]
        return {group: [] for group in ("day", "week", "month")} if kwargs.get("all_groupings") else []

    monkeypatch.setattr(dynamics, "_client_filter", clients)
    monkeypatch.setattr(dynamics, "_metrics", metrics)
    monkeypatch.setattr(dynamics, "_chart", chart)
    app = FastAPI()
    app.include_router(dynamics.router)
    async def database():
        return object()

    app.dependency_overrides[get_db] = database

    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            return await client.get("/reports/sales-dynamics", params=[
                ("date_from", "2026-09-01"), ("date_to", "2026-09-30"), ("include_both", "true"), ("include_groupings", "true"),
                ("departments", "Авиаторов"), ("departments", "ЦУМ"),
                ("manager", "M1"), ("manager", "M2"),
                ("buyer_type", "Розница"), ("buyer_type", "Оптовик"),
                ("brands", "Brand A"), ("brands", "Brand B"),
            ])

    response = asyncio.run(run())
    assert response.status_code == 200, response.text
    assert set(response.json()["comparisons"]) == {"previous_period", "previous_year"}
    assert len(calls) == 6  # current + previous period + previous year


class Rows(list):
    def all(self):
        return self


class ProductDb:
    """Compile the real endpoint queries using asyncpg; fail on expanded bind overflow."""
    def __init__(self):
        self.executions = 0

    def check(self, query):
        sql = query.compile(dialect=asyncpg.dialect(), compile_kwargs={"render_postcompile": True})
        assert len(sql.params) < 100  # retail's 40,000 names must remain one bind
        assert "ANY (" in str(sql)
        assert any(isinstance(value, list) and "retail customer" in value and len(value) == 40001 for value in sql.params.values())
        self.executions += 1

    async def scalar(self, query):
        self.check(query)
        return 1

    async def execute(self, query):
        self.check(query)
        columns = list(query.selected_columns.keys())
        values = dict(
            article="A1", code="001", name="Item", units=Decimal(2), revenue=Decimal(200),
            checks=1, clients=1, average_price=Decimal(100), discount_amount=Decimal(40),
            average_discount=Decimal("16.67"), last_sale=date(2026, 9, 2),
            period=datetime(2026, 9, 2), id=1, sale_date=date(2026, 9, 2),
            document_number="test-1", client="Retail customer", department="Авиаторов",
            quantity=Decimal(2), amount=Decimal(200), actual_price=Decimal(100), discount=Decimal(0),
        )
        if len(columns) == 5 and "department" not in columns:
            return SimpleNamespace(one=lambda: (Decimal(200), Decimal(2), 1, 1, Decimal(240)))
        if columns == ["department", "sum", "sum_1", "count", "count_1"]:
            rows = [("Авиаторов", Decimal(200), Decimal(2), 1, 1)]
        elif columns[0] == "client" and len(columns) == 4:
            rows = [("Retail customer", Decimal(200), Decimal(2), 1)]
        else:
            mapping = {column: values[column] for column in columns}
            rows = [SimpleNamespace(**mapping, _mapping=mapping)]
        return Rows(rows)


def test_product_sales_retail_endpoints_do_not_expand_40000_clients(monkeypatch):
    async def buyers(value):
        assert value == "Розница"
        return ["Retail customer"] + [f"other-{i}" for i in range(40000)]

    async def mapping():
        return {"retail customer": "M1"}

    monkeypatch.setattr(product_reports, "get_buyer_type_clients", buyers)
    monkeypatch.setattr(product_reports, "get_client_managers", mapping)
    app = FastAPI()
    app.include_router(product_reports.router)
    db = ProductDb()
    async def database():
        return db

    app.dependency_overrides[get_db] = database
    payload = dict(date_from="2026-09-01", date_to="2026-09-30", buyer_type=["Розница"],
                   products=[dict(code="001", article="A1", name="Item")])

    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            for endpoint in ["summary", "products", "dynamics", "departments", "managers", "details", "export"]:
                body = {**payload, "product": payload["products"][0]} if endpoint == "details" else payload
                response = await client.post(f"/reports/product-sales/{endpoint}", json=body)
                assert response.status_code == 200, (endpoint, response.text)
                if endpoint == "summary":
                    assert response.json()["current"]["revenue"] == 200
                if endpoint == "export":
                    assert response.content.startswith(b"PK")

    asyncio.run(run())
    assert db.executions >= 12


def test_old_backend_rejects_array_filters_before_loading_sales():
    from pydantic import BaseModel

    class OldRequest(BaseModel):
        manager: str | None = None
        buyer_type: str | None = None

    app = FastAPI()

    @app.post("/reports/product-sales/summary")
    async def old_report(payload: OldRequest):
        raise AssertionError("Old backend must reject this before executing a report")

    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            return await client.post("/reports/product-sales/summary", json={"manager": [], "buyer_type": ["Розница"]})

    response = asyncio.run(run())
    assert response.status_code == 422
    assert {tuple(issue["loc"]) for issue in response.json()["detail"]} == {("body", "manager"), ("body", "buyer_type")}
    assert all(issue["type"] == "string_type" for issue in response.json()["detail"])
