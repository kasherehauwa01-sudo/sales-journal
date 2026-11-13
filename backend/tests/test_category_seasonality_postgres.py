"""Isolated native PG: exact matching, filters, coverage and SQL-only aggregation."""

import asyncio
from datetime import date, datetime, timezone
from decimal import Decimal
import json
import os
import time
from pathlib import Path
from unittest.mock import AsyncMock
import sqlalchemy as sa
from app.models import CatalogProduct, CatalogLookup, ImportBatch, Sale, SaleItem
from app.models.import_control import ImportReceipt
from app.services import category_seasonality as service
from app.services.catalog_attributes import identity
from app.services.report_execution import REPORT_SETTINGS
from app.routers.category_seasonality_report import Request
from test_sales_control import sales_pg
from test_import_control import pg


async def seed(db):
    db.add(
        ImportBatch(
            id=300,
            filename="synthetic.html",
            file_size=100,
            status="completed",
            error_rows=0,
        )
    )
    await db.flush()
    db.add(
        ImportReceipt(
            import_id=300,
            filename="synthetic.html",
            status="success",
            period_method="DECLARED_REPORT_PERIOD",
            period_start=date(2023, 1, 1),
            period_end=date(2025, 12, 31),
            departments=["A"],
            sha256="a" * 64,
        )
    )
    now = datetime.now(timezone.utc)
    for n, (parent, section, brand, maker) in enumerate(
        [
            (1, "Апрель", "Бренд A", "Завод A"),
            (1, "Июль", "Бренд B", "Завод B"),
            (2, "Апрель", "Бренд A", "Завод B"),
            (None, "Неизвестный раздел", "Бренд A", "Завод A"),
        ],
        1,
    ):
        db.add(
            CatalogProduct(
                product_id=n,
                code=f"00{n}",
                article="ДУБЛЬ",
                category_id=parent,
                category="Посуда" if parent == 1 else "Декор" if parent == 2 else None,
                subcategory=section,
                legacy_category=section,
                brand=brand,
                manufacturer=maker,
                horeca=False,
                source_updated_at=now,
                synced_at=now,
            )
        )
        await db.flush()
        # Casefold vs SQL lower, Unicode whitespace, blank and leading zeros.
        code = f"00{n}" if n != 4 else "\u00a0Straße\u00a0"
        db.add(
            CatalogLookup(
                identity=identity(code, "ДУБЛЬ"),
                code=code.strip().casefold(),
                article="дубль",
                product_id=n,
                status="matched",
                matched_by="code",
            )
        )
        for y in [2023, 2024, 2025]:
            for m in range(1, 13):
                s = Sale(
                    sale_date=date(y, m, 15),
                    document_number=f"{n}-{y}-{m}",
                    client=" Клиент ",
                    department="A",
                    total_amount=10,
                    fingerprint=f"{n}-{y}-{m}",
                    import_id=300,
                )
                db.add(s)
                await db.flush()
                db.add(
                    SaleItem(
                        sale_id=s.id,
                        code=code,
                        article="ДУБЛЬ",
                        name="Не выгружать SKU",
                        quantity=50 if m == (7 if n == 2 else 4) else 10,
                        actual_price=2,
                    )
                )
    await db.commit()


async def configure(db):
    for sql in REPORT_SETTINGS:
        await db.execute(sa.text(sql))


def test_full_report_parent_child_overlap_filters_and_real_metadata(
    sales_pg, monkeypatch
):
    _, _, factory, _ = sales_pg
    loader = AsyncMock(return_value=None)
    monkeypatch.setattr(service, "get_sales_filter_clients", loader)

    async def run():
        async with factory() as db:
            await seed(db)
        async with factory() as db:
            await configure(db)
            result = await service.build(
                db,
                Request(
                    years=[2023, 2024, 2025],
                    categories=[
                        "category:1",
                        service.branch(1, "Посуда", "Апрель", None)["key"],
                    ],
                ),
                date(2026, 11, 13),
            )
            assert len(result["rows"]) == 2
            assert [r["units"]["peaks"][0]["month"] for r in result["rows"]] == [4, 7]
            assert all(r["units"]["peaks"][0]["repeating"] for r in result["rows"])
            assert result["rows"][0]["units"]["annual"][0]["values"][3] == 50
            await db.rollback()
        async with factory() as db:
            await configure(db)
            result = await service.build(
                db,
                Request(
                    years=[2023, 2024, 2025],
                    stores=["A"],
                    categories=["category:1"],
                    brands=["Бренд A", "Бренд B"],
                    manufacturers=["Завод B"],
                    manager=["М1", "М2"],
                    buyer_type=["Опт"],
                ),
                date(2026, 11, 13),
            )
            assert [r["name"] for r in result["rows"]] == ["Июль"]
            assert loader.call_args.args[1:] == (["М1", "М2"], ["Опт"])
            await db.rollback()
        async with factory() as db:
            await configure(db)
            result = await service.build(
                db, Request(years=[2023, 2024, 2025]), date(2026, 11, 13)
            )
            assert len(result["rows"]) == 4
            assert any(r["parent"] == service.UNKNOWN for r in result["rows"])
            assert (
                sum(r["units"]["annual"][0]["values"][3] for r in result["rows"]) == 160
            )
            await db.rollback()
        async with factory() as db:
            await configure(db)
            await db.execute(
                sa.text(
                    "UPDATE import_control_receipts SET period_method='OBSERVED_SALES'"
                )
            )
            result = await service.build(
                db, Request(years=[2023, 2024, 2025]), date(2026, 11, 13)
            )
            assert all(r["units"]["indices"] is None for r in result["rows"])
            assert all(r["units"]["annual"][0]["values"][3] > 0 for r in result["rows"])
            await db.rollback()

    asyncio.run(run())


import pytest


@pytest.mark.skipif(
    os.environ.get("CATEGORY_SEASON_BENCHMARK") != "1",
    reason="Explicit local synthetic benchmark only",
)
def test_local_three_year_benchmark(sales_pg, monkeypatch):
    from benchmarks.benchmark_category_seasonality import run

    _, engine, factory, _ = sales_pg
    monkeypatch.setattr(
        service,
        "get_sales_filter_clients",
        AsyncMock(return_value=["клиент 1", "клиент 2"]),
    )
    # Wide scope must not be narrowed by the mocked manager directory.
    original = service.get_sales_filter_clients

    async def clients(db, manager, buyer):
        return await original(db, manager, buyer) if manager or buyer else None

    monkeypatch.setattr(service, "get_sales_filter_clients", clients)
    result = asyncio.run(run(engine, factory))
    assert len(result) == 2


def test_http_options_explicit_report_and_transaction_contract(sales_pg, monkeypatch):
    from fastapi import FastAPI
    import httpx
    from app.database import get_db
    from app.routers.category_seasonality_report import router

    _, _, factory, _ = sales_pg
    monkeypatch.setattr(
        service, "get_sales_filter_clients", AsyncMock(return_value=None)
    )
    app = FastAPI()
    app.include_router(router, prefix="/api")

    async def database():
        async with factory() as db:
            yield db

    app.dependency_overrides[get_db] = database

    async def run():
        async with factory() as db:
            await seed(db)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            r = await client.get("/api/reports/category-seasonality/options")
            assert r.status_code == 200 and r.json()["years"] == [2025, 2024, 2023]
            r = await client.post(
                "/api/reports/category-seasonality",
                json=dict(years=[2023, 2024, 2025], categories=["category:1"]),
            )
            assert r.status_code == 200 and len(r.json()["rows"]) == 2
            assert r.json()["category_labels"] == ["Посуда"]
            assert "Не выгружать SKU" not in r.text
            assert (
                await client.post(
                    "/api/reports/category-seasonality", json=dict(years=[2026])
                )
            ).status_code == 422
            assert (
                await client.post(
                    "/api/reports/category-seasonality",
                    json=dict(years=[2025], use_seasonality=True),
                )
            ).status_code == 422

    asyncio.run(run())
