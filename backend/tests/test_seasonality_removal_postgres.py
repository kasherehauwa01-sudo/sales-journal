"""Owned local PostgreSQL schemas: historical tables survive, shared revision works."""

import asyncio
import sqlalchemy as sa
from app.models import Base, SalesSourceState
from app.services.sales_revision import changed
import start_backend
from test_sales_control import sales_pg
from test_import_control import pg


def test_existing_schema28_and_historical_rows_survive_source_invalidation(sales_pg):
    engine, _, factory, _ = sales_pg
    with engine.begin() as db:
        db.execute(
            sa.text(
                "INSERT INTO seasonality_months(generation,identity,year,month,incomplete_source,units,positive_lines,first_sale,last_sale) VALUES(7,:identity,2025,1,false,123.456,1,'2025-01-01','2025-01-01')"
            ),
            {"identity": "a" * 64},
        )

    async def run():
        async with factory() as db:
            state = await db.get(SalesSourceState, 1)
            if state is not None:
                await db.delete(state)
                await db.flush()
            db.add(
                SalesSourceState(
                    id=1,
                    source_revision=91,
                    enabled=False,
                    rebuild_required=False,
                    generation=7,
                )
            )
            await db.commit()
            await changed(db)
            await db.commit()
            state = await db.get(SalesSourceState, 1)
            assert (
                state.source_revision == 92
                and state.enabled is False
                and state.rebuild_required is False
            )
            value = await db.scalar(sa.text("SELECT units FROM seasonality_months"))
            assert str(value) == "123.456"

    asyncio.run(run())
    start_backend.check_schema(engine, Base.metadata, "20261112_28")
    assert {
        "seasonality_months",
        "seasonality_store_months",
        "seasonality_profiles",
    } <= set(sa.inspect(engine).get_table_names())
