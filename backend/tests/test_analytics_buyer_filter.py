import asyncio
import os
import uuid
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import asyncpg
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import Base, ImportBatch, Sale, SaleItem
from app.routers import analytics


def test_large_buyer_filter_uses_one_array_parameter_even_when_subquery_is_reused():
    clients = [f" Client {i} " for i in range(40000)]
    clients += ["CLIENT 0", "", "  ", None]
    sub = analytics.filters(select(Sale.id, Sale.total_amount), clients=clients).subquery()
    query = select(sub.c.total_amount).union_all(select(sub.c.total_amount))
    compiled = query.compile(dialect=asyncpg.dialect())
    assert "ANY (" in str(compiled)
    assert len(compiled.params) == 1
    assert len(next(iter(compiled.params.values()))) == 40000
    assert "client 0" in next(iter(compiled.params.values()))


def test_empty_buyer_selection_matches_nothing_and_unset_selection_does_not_filter():
    for clients in ([], ["", "   ", None]):
        query = analytics.filters(select(Sale.id), clients=clients)
        assert "WHERE false" in str(query.compile(dialect=asyncpg.dialect()))
    assert "WHERE" not in str(analytics.filters(select(Sale.id), clients=None))


def test_large_buyer_filter_checks_and_overview_postgres(monkeypatch):
    target = os.environ.get("ANALYTICS_TEST_DATABASE_URL")
    if not target:
        pytest.skip("Requires disposable localhost analytics_test PostgreSQL")
    parsed = make_url(target)
    assert parsed.host in {"localhost", "127.0.0.1"} and parsed.database == "analytics_test"

    async def buyer_clients(buyer_type):
        assert buyer_type == "Розница"
        return ["  ALPHA  "] + [f"unknown-{i}" for i in range(40000)]

    monkeypatch.setattr(analytics, "get_buyer_type_clients", buyer_clients)

    async def run():
        admin = create_async_engine(target)
        schema = "analytics_test_" + uuid.uuid4().hex
        async with admin.begin() as connection:
            await connection.execute(text(f"CREATE SCHEMA {schema}"))
        engine = create_async_engine(target, connect_args={"server_settings": {"search_path": schema}})
        try:
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            async with async_sessionmaker(engine, expire_on_commit=False)() as db:
                batch = ImportBatch(filename="test.html", stored_path=None, file_size=0)
                db.add(batch)
                await db.flush()
                for i, (client, amount, units) in enumerate(((" Alpha ", 1200, 3), ("Beta", 9000, 1))):
                    sale = Sale(sale_date=date(2026, 10, 1), document_number=str(i), client=client,
                                department="Shop", total_amount=amount, base_amount=amount,
                                fingerprint=str(i), import_id=batch.id)
                    db.add(sale)
                    await db.flush()
                    db.add(SaleItem(sale_id=sale.id, name="Item", quantity=units, actual_price=Decimal(amount)/units))
                await db.commit()
                args = dict(buyer_type="Розница", departments=None, db=db,
                            date_from=date(2026, 10, 1), date_to=date(2026, 10, 31))
                result = await analytics.checks(**args)
                assert result["average_check"] == result["median_check"] == 1200
                assert result["average_items"] == result["median_items"] == 3
                assert result["average_unit_price"] == 400
                assert result["amount_distribution"] == [{"bucket": "1 000–2 000", "count": 1}]
                overview = await analytics.overview(**args)
                assert overview["current"]["revenue"] == 1200
                assert overview["current"]["sales_count"] == 1
                assert overview["previous"]["sales_count"] == 0
        finally:
            await engine.dispose()
            async with admin.begin() as connection:
                await connection.execute(text(f"DROP SCHEMA {schema} CASCADE"))
            await admin.dispose()

    asyncio.run(run())
