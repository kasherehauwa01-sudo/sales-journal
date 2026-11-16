"""Month boundaries and real transaction/publication regression tests."""

import asyncio
from datetime import date, timedelta
from unittest.mock import AsyncMock
import pytest
import sqlalchemy as sa
from app.services.category_snapshot_facts import (
    month_ranges,
    input_statement,
    batch_statement,
)
from app.services import category_seasonality_snapshot as service
from app.models import (
    CategorySeasonState as State,
    CategorySeasonSnapshot as Snapshot,
    CategorySeasonFact as Fact,
)
from test_category_seasonality_postgres import snapshot_pg, seed, CLOCK
from test_import_control import pg
from test_sales_control import sales_pg


def test_nonoverlapping_months_leap_year_and_partial_cutoff():
    parts = month_ranges(date(2023, 12, 31), date(2024, 3, 15))
    assert parts == [
        (date(2023, 12, 1), date(2024, 1, 1)),
        (date(2024, 1, 1), date(2024, 2, 1)),
        (date(2024, 2, 1), date(2024, 3, 1)),
        (date(2024, 3, 1), date(2024, 3, 15)),
    ]
    for day in (
        date(2023, 12, 31),
        date(2024, 1, 1),
        date(2024, 2, 29),
        date(2024, 3, 1),
    ):
        assert sum(lo <= day < hi for lo, hi in parts) == 1
    assert not any(lo <= date(2024, 3, 15) < hi for lo, hi in parts)
    assert month_ranges(None, date(2026, 1, 1)) == []
    assert len(month_ranges(date(2010, 1, 1), date(2026, 1, 1))) == 192


def test_materialized_input_preserves_source_joins_and_cutoff():
    source = service.facts_statement("cs_pairs_" + "a" * 32)
    result = str(input_statement(source, "cs_input_" + "b" * 32))
    joins = str(source)[
        str(source).index("FROM sale_items i") : str(source).index("GROUP BY")
    ]
    assert joins in result and result.endswith("ORDER BY s.sale_date")
    assert "coalesce(i.code" in result and "coalesce(s.client,'')" in result
    assert "sum(units),coalesce(sum(revenue),0),sum(lines)" in str(
        batch_statement("cs_input_" + "b" * 32)
    )
    for name in ("sales", "cs_input_bad;DROP TABLE sales"):
        with pytest.raises(ValueError):
            input_statement(source, name)


@pytest.mark.parametrize("active,manual", [(False, True), (True, True), (True, False)])
@pytest.mark.parametrize("failure", ["timeout", "cancelled"])
def test_failed_second_month_is_never_published(
    snapshot_pg, monkeypatch, active, manual, failure
):
    _, engine, factory, _ = snapshot_pg
    from app.services import category_snapshot_facts as builder

    original = builder.batch_statement

    async def run():
        async with factory() as db:
            await seed(db)
        prior = (
            await service.rebuild(engine, manual=True, clock=CLOCK) if active else None
        )
        clock = service.next_slot(CLOCK) if not manual else CLOCK + timedelta(days=1)
        made = 0
        first_visible = False

        def statement(name):
            nonlocal made
            made += 1
            if made == 2:
                assert first_visible
                if failure == "cancelled":
                    raise asyncio.CancelledError()
                # The first month's facts already exist in this transaction.
                return sa.text("SELECT pg_sleep(0.05)")
            return original(name)

        monkeypatch.setattr(builder, "batch_statement", statement)
        # Intercept the injected sleep, not setup/first month.
        from sqlalchemy.ext.asyncio import AsyncSession

        execute = AsyncSession.execute

        async def intercepted(db, stmt, *args, **kwargs):
            nonlocal first_visible
            if str(stmt) == "SELECT pg_sleep(0.05)":
                await execute(db, sa.text("SET LOCAL statement_timeout='1ms'"))
            result = await execute(db, stmt, *args, **kwargs)
            if (
                str(stmt).startswith("INSERT INTO category_season_facts")
                and args
                and isinstance(args[0], dict)
            ):
                count = await db.scalar(
                    sa.select(sa.func.count())
                    .select_from(Fact)
                    .where(Fact.snapshot_id == args[0]["sid"])
                )
                first_visible = first_visible or count > 0
            return result

        monkeypatch.setattr(AsyncSession, "execute", intercepted)
        with pytest.raises(
            asyncio.CancelledError if failure == "cancelled" else sa.exc.DBAPIError
        ):
            await service.rebuild(engine, manual=manual, clock=clock)
        assert made == 2
        async with factory() as db:
            state = await db.get(State, 1)
            assert state.active_id == (prior["id"] if prior else None)
            if not active:
                assert state.next_due is None
            elif manual:
                assert state.next_due == service.next_slot(CLOCK)
            else:
                assert state.next_due == service.next_slot(clock)
            attempt = await db.scalar(
                sa.select(Snapshot).where(Snapshot.status == "failed")
            )
            assert (
                await db.scalar(
                    sa.text(
                        "SELECT count(*) FROM pg_class WHERE relpersistence='t' AND relname ~ '^cs_input_[0-9a-f]{32}$'"
                    )
                )
                == 0
            )
            assert attempt.fact_rows == 0
            assert (
                await db.scalar(
                    sa.select(sa.func.count())
                    .select_from(Fact)
                    .where(Fact.snapshot_id == attempt.id)
                )
                == 0
            )
            assert attempt.error_code == (
                "cancelled" if failure == "cancelled" else "facts_months:57014"
            )
        monkeypatch.setattr(builder, "batch_statement", original)
        retried = await service.rebuild(
            engine, manual=True, clock=CLOCK + timedelta(days=1)
        )
        assert retried["status"] == "published"

    asyncio.run(run())


def test_complete_builder_matches_legacy_with_nulls_and_calendar_edges(
    snapshot_pg, monkeypatch
):
    from app.models import Sale, SaleItem
    from app.services import category_snapshot_facts as builder
    from uuid import uuid4
    from decimal import Decimal

    _, engine, factory, _ = snapshot_pg
    candidate = builder.build_facts

    async def legacy(db, pairs, sid, end, source, ensure_headroom, set_stage):
        await db.execute(source, dict(sid=sid, end=end))

    async def run():
        async with factory() as db:
            await seed(db)
            for day, client, code, article, quantity, price in [
                (date(2024, 2, 29), None, None, None, Decimal("1.250"), None),
                (date(2025, 1, 1), "", "", "", Decimal("-2.125"), Decimal("0")),
                (
                    date(2025, 12, 31),
                    "  КЛИЕНТ A  ",
                    "001",
                    "ДУБЛЬ",
                    Decimal("0"),
                    None,
                ),
                (
                    date(2026, 11, 1),
                    "Клиент B",
                    "001",
                    "ДУБЛЬ",
                    Decimal("123"),
                    Decimal("999"),
                ),
            ]:
                row = Sale(
                    sale_date=day,
                    document_number="synthetic",
                    department="Store",
                    client=client,
                    total_amount=0,
                    fingerprint=str(uuid4()),
                    import_id=300,
                )
                db.add(row)
                await db.flush()
                db.add(
                    SaleItem(
                        sale_id=row.id,
                        code=code,
                        article=article,
                        name="synthetic",
                        quantity=quantity,
                        actual_price=price,
                    )
                )
            await db.commit()
        monkeypatch.setattr(builder, "build_facts", legacy)
        before = await service.rebuild(engine, manual=True, clock=CLOCK)
        monkeypatch.setattr(builder, "build_facts", candidate)
        after = await service.rebuild(engine, manual=True, clock=CLOCK)
        assert before["status"] == after["status"] == "published"
        projection = """SELECT c.key,cl.name,f.store,f.brand,f.manufacturer,f.year,f.month,f.units,f.revenue,f.lines
            FROM category_season_facts f JOIN category_season_categories c ON c.snapshot_id=f.snapshot_id AND c.id=f.category_id
            JOIN category_season_clients cl ON cl.snapshot_id=f.snapshot_id AND cl.id=f.client_id WHERE f.snapshot_id=:sid"""
        async with factory() as db:
            for left, right in [
                (before["id"], after["id"]),
                (after["id"], before["id"]),
            ]:
                sql = (
                    "SELECT count(*) FROM (("
                    + projection.replace(":sid", ":left")
                    + ") EXCEPT ALL ("
                    + projection.replace(":sid", ":right")
                    + ")) d"
                )
                assert await db.scalar(sa.text(sql), dict(left=left, right=right)) == 0
            expected = (
                await db.execute(
                    sa.select(
                        sa.func.count(),
                        sa.func.sum(SaleItem.quantity),
                        sa.func.coalesce(
                            sa.func.sum(SaleItem.quantity * SaleItem.actual_price), 0
                        ),
                    )
                    .select_from(SaleItem)
                    .join(Sale)
                    .where(Sale.sale_date < date(2026, 11, 1))
                )
            ).one()
            actual = (
                await db.execute(
                    sa.select(
                        sa.func.sum(Fact.lines),
                        sa.func.sum(Fact.units),
                        sa.func.sum(Fact.revenue),
                    ).where(Fact.snapshot_id == after["id"])
                )
            ).one()
            assert tuple(actual) == tuple(expected)
            assert (
                await db.scalar(
                    sa.select(sa.func.count())
                    .select_from(Fact)
                    .where(
                        Fact.snapshot_id == after["id"],
                        Fact.year == 2026,
                        Fact.month == 11,
                    )
                )
                == 0
            )

    asyncio.run(run())
