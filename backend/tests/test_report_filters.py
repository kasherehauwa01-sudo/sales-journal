"""Autonomous regression checks: no PostgreSQL, ClientsVR or working data."""
import asyncio
from datetime import date

import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.dialects.postgresql import asyncpg

from app.models import Sale
from app.routers.product_reports import ReportRequest, _conditions
from app.routers.sales_dynamics_report import _sales_rows
from app.services import sales_client_filters as filters
from app.services.report_filters import client_names_condition


def test_retail_40000_names_use_one_postgresql_bind():
    names = [f"Client {i}" for i in range(40000)] + [" CLIENT 0 ", "", None]
    scope = select(Sale.id).where(client_names_condition(Sale.client, names)).subquery()
    compiled = select(scope).union_all(select(scope)).compile(dialect=asyncpg.dialect())
    assert "ANY (" in str(compiled)
    assert len(compiled.params) == 1
    assert len(next(iter(compiled.params.values()))) == 40000


def test_multi_selection_is_union_within_and_intersection_between(monkeypatch):
    async def managers(value):
        return {"M1": ["Alpha", "Beta"], "M2": ["Gamma"]}[value]

    async def buyers(db, value):
        return {"Розница": [" beta ", "Gamma"], "Оптовик": ["Delta"]}[value]

    monkeypatch.setattr(filters, "get_manager_clients", managers)
    monkeypatch.setattr(filters, "get_sales_buyer_type_clients", buyers)
    result = asyncio.run(filters.get_sales_filter_clients(
        object(), ["M1", "M2", "M1"], ["Розница", "Оптовик"]
    ))
    assert result == ["Beta", "Gamma"]


def test_empty_selected_type_does_not_remove_the_filter(monkeypatch):
    async def buyers(db, value):
        return []

    monkeypatch.setattr(filters, "get_sales_buyer_type_clients", buyers)
    assert asyncio.run(filters.get_sales_filter_clients(object(), None, ["Розница"])) == []
    assert asyncio.run(filters.get_sales_filter_clients(object(), [], [])) is None


def test_failed_selected_endpoint_does_not_become_partial_scope(monkeypatch):
    from app.services.clients_vr import ClientsVrError

    async def buyers(db, value):
        if value == "Розница":
            raise ClientsVrError("unavailable")
        return ["Alpha"]

    monkeypatch.setattr(filters, "get_sales_buyer_type_clients", buyers)
    with pytest.raises(ClientsVrError):
        asyncio.run(filters.get_sales_filter_clients(object(), None, ["Оптовик", "Розница"]))


def test_product_report_accepts_legacy_and_multiple_values(monkeypatch):
    from app.routers import product_reports

    async def names(value):
        return [f"client-{value}"]

    monkeypatch.setattr(product_reports, "get_manager_clients", names)
    monkeypatch.setattr(product_reports, "get_buyer_type_clients", names)
    for manager, buyer in (("M1", "Розница"), (["M1", "M2"], ["Розница", "Оптовик"])):
        data = ReportRequest(date_from="2026-09-01", date_to="2026-09-30", manager=manager, buyer_type=buyer, products=[{"code": "A", "name": "Item"}])
        conditions = asyncio.run(_conditions(data))
        compiled = select(Sale.id).where(*conditions).compile(dialect=asyncpg.dialect())
        # No expanded IN parameters for the ClientsVR client names.
        assert str(compiled).count("ANY (") == 2


def test_brand_totals_exclude_other_positions_and_ambiguous_catalog_matches():
    # Ephemeral in-memory SQLite exercises the actual EXISTS and correlated SUM,
    # not a mocked result. PostgreSQL-specific ANY is covered separately above.
    engine = create_engine("sqlite://")
    with engine.begin() as db:
        for statement in (
            "CREATE TABLE sales(id INTEGER, sale_date DATE, total_amount NUMERIC)",
            "CREATE TABLE sale_items(id INTEGER, sale_id INTEGER, code TEXT, article TEXT, quantity NUMERIC, actual_price NUMERIC)",
            "CREATE TABLE catalog_lookups(identity TEXT, product_id INTEGER, status TEXT, code TEXT, article TEXT)",
            "CREATE TABLE catalog_products(product_id INTEGER, brand TEXT)",
            "INSERT INTO sales VALUES (1,'2026-09-01',1000),(2,'2026-09-01',900)",
            "INSERT INTO sale_items VALUES (1,1,'A','a',2,100),(2,1,'B','b',4,200),(3,2,'C','c',1,900)",
            "INSERT INTO catalog_products VALUES (1,'Alpha'),(2,'Beta'),(3,'Alpha')",
            "INSERT INTO catalog_lookups VALUES ('1',1,'matched','a','a'),('2',2,'matched','b','b'),('3',3,'ambiguous','c','c')",
        ):
            db.execute(text(statement))
        query = _sales_rows(date(2026, 9, 1), date(2026, 9, 30), None, None, brands=["Alpha"])
        rows = db.execute(select(query)).mappings().all()
        assert len(rows) == 1
        assert rows[0]["id"] == 1
        assert rows[0]["total_amount"] == 200
        assert rows[0]["items_count"] == 2
        unfiltered = db.execute(select(_sales_rows(date(2026, 9, 1), date(2026, 9, 30), None, None))).mappings().all()
        assert {row["id"]: row["total_amount"] for row in unfiltered} == {1: 1000, 2: 900}
    engine.dispose()
