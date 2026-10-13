import asyncio
from datetime import date
from decimal import Decimal
from sqlalchemy import select, func
from app.models import SeasonalityMonth, SeasonalityStoreMonth, SeasonalityProfile
from app.services.seasonality import identity
from app.routers import seasonality_report as router
from test_seasonality_worker import fixture, finish


def test_read_only_history_filters_pages_and_store_totals(monkeypatch):
    monkeypatch.setattr(router, "today", lambda: date(2026, 10, 12))

    async def run():
        async with fixture() as (engine, factory):
            await finish(factory, engine)
            async with factory() as db:
                digest = identity("code:001")
                before = (await db.get(SeasonalityProfile, (1, digest))).data.copy()
                listing = await router.products(
                    search="001", after=None, limit=50, db=db
                )
                assert listing["metadata"]["ready"]
                assert listing["items"][0]["product_key"] == "code:001"
                full = await router.history(digest, limit=5, db=db)
                assert len(full["months"]) == 5 and full["months"][0]["year"] == 2024
                next_page = await router.history(
                    digest,
                    after_year=full["next_cursor"]["year"],
                    after_month=full["next_cursor"]["month"],
                    limit=5,
                    db=db,
                )
                assert len(next_page["months"]) == 5
                assert {(m["year"], m["month"]) for m in full["months"]}.isdisjoint(
                    {(m["year"], m["month"]) for m in next_page["months"]}
                )
                scoped = await router.history(
                    digest,
                    date_from=date(2025, 10, 13),
                    date_to=date(2025, 12, 1),
                    store="Магазин",
                    limit=120,
                    db=db,
                )
                assert len(scoped["months"]) == 3
                assert scoped["months"][1]["units"] == "90.000"
                assert full["profile"] == scoped["profile"] == before
                compared = await router.history(digest, years=[2025], limit=120, db=db)
                assert {m["year"] for m in compared["months"]} == {2025}
                assert (await db.get(SeasonalityProfile, (1, digest))).data == before
                all_units = await db.scalar(
                    select(func.sum(SeasonalityMonth.units)).where(
                        SeasonalityMonth.generation == 1
                    )
                )
                store_units = await db.scalar(
                    select(func.sum(SeasonalityStoreMonth.units)).where(
                        SeasonalityStoreMonth.generation == 1
                    )
                )
                assert all_units == store_units
                assert (
                    await db.scalar(
                        select(func.count()).select_from(SeasonalityStoreMonth)
                    )
                    == 24
                )

    asyncio.run(run())


def test_stale_profile_is_not_presented_as_current(monkeypatch):
    from app.services.seasonality_storage import changed

    monkeypatch.setattr(router, "today", lambda: date(2026, 10, 12))

    async def run():
        async with fixture() as (engine, factory):
            await finish(factory, engine)
            async with factory() as db:
                await changed(db, rebuild=True)
                await db.commit()
                result = await router.history(identity("code:001"), limit=120, db=db)
                assert not result["metadata"]["ready"]
                assert result["profile"] is None
                assert result["seasonality"]["status"] == "Недостаточно данных"
                assert result["months"]  # old aggregates explicitly marked stale

    asyncio.run(run())


def test_store_month_checkpoint_rolls_back_and_resume_does_not_duplicate(monkeypatch):
    from app.services import seasonality_worker as worker
    from app.services.seasonality_storage import state
    import pytest

    async def run():
        async with fixture() as (engine, factory):
            original = worker.scan_batch

            async def fail(db, current, size):
                await original(db, current, size)
                raise RuntimeError("synthetic")

            monkeypatch.setattr(worker, "scan_batch", fail)
            with pytest.raises(RuntimeError):
                await worker.run_batch(
                    factory, engine, date(2026, 10, 12), scan_size=7, profile_size=1
                )
            async with factory() as db:
                assert (await state(db)).cursor == 0
                assert (
                    await db.scalar(
                        select(func.count()).select_from(SeasonalityStoreMonth)
                    )
                    == 0
                )
            monkeypatch.setattr(worker, "scan_batch", original)
            await finish(factory, engine)
            await finish(factory, engine)
            async with factory() as db:
                assert (
                    await db.scalar(
                        select(func.count()).select_from(SeasonalityStoreMonth)
                    )
                    == 24
                )
                assert (
                    await db.get(
                        SeasonalityStoreMonth,
                        (1, identity("code:001"), 2025, 11, "Магазин"),
                    )
                ).units == Decimal("90")

    asyncio.run(run())
