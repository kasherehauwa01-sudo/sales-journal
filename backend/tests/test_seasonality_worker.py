import asyncio
from contextlib import asynccontextmanager
from datetime import date
from decimal import Decimal
import pytest
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from app.models import (
    Base,
    ImportBatch,
    Sale,
    SaleItem,
    SeasonalityMonth,
    SeasonalityProfile,
    SeasonalityState,
)
from app.services import seasonality_worker as worker
from app.services.seasonality_storage import state, changed, attach
from app.services.seasonality import identity


@asynccontextmanager
async def fixture(url=None, schema=None):
    engine = create_async_engine(
        url or "sqlite+aiosqlite:///:memory:",
        **(
            {"connect_args": {"server_settings": {"search_path": schema}}}
            if schema
            else {}
        )
    )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    async with factory() as db:
        db.add(ImportBatch(id=1, filename="synthetic", file_size=0, status="completed"))
        await db.flush()
        count = 0
        for year in (2024, 2025):
            for month, units in enumerate([10] * 9 + [70, 90, 70], 1):
                for part in (1, 2):
                    count += 1
                    db.add(
                        Sale(
                            id=count,
                            sale_date=date(year, month, part),
                            document_number=str(count),
                            department="Магазин",
                            total_amount=units,
                            fingerprint=str(count),
                            import_id=1,
                        )
                    )
                    await db.flush()
                    db.add(
                        SaleItem(
                            id=count,
                            sale_id=count,
                            code=" 001 " if part == 1 else "001",
                            article="OLD" if year == 2024 else "NEW",
                            name="Товар",
                            quantity=Decimal(units) / 2,
                            actual_price=1,
                        )
                    )
        await state(db)
        await db.commit()
    try:
        yield engine, factory
    finally:
        await engine.dispose()


async def finish(factory, engine, today=date(2026, 10, 12)):
    for _ in range(100):
        result = await worker.run_batch(
            factory, engine, today, scan_size=7, profile_size=1
        )
        if result["phase"] == "idle":
            return result
    raise AssertionError("bounded job did not finish")


def test_restart_and_incremental_exact_no_duplicate_sums():
    async def run():
        async with fixture() as (engine, factory):
            first = await worker.run_batch(
                factory, engine, date(2026, 10, 12), scan_size=7, profile_size=1
            )
            assert first["cursor"] == 7
            async with factory() as db:
                assert (await state(db)).active_generation == 0
            await finish(factory, engine)
            async with factory() as db:
                current = await state(db)
                assert current.active_generation == 1 and current.cursor == 48
                profile = await db.get(SeasonalityProfile, (1, identity("code:001")))
                assert profile.data["pattern"] == "confirmed"
                assert (
                    await db.get(SeasonalityMonth, (1, identity("code:001"), 2025, 11))
                ).units == 90
                db.add(
                    Sale(
                        id=49,
                        sale_date=date(2025, 11, 20),
                        document_number="tail",
                        department="Магазин",
                        total_amount=10,
                        fingerprint="tail",
                        import_id=1,
                    )
                )
                await db.flush()
                db.add(
                    SaleItem(
                        id=49,
                        sale_id=49,
                        code="001",
                        article="LATEST",
                        name="Товар",
                        quantity=10,
                        actual_price=1,
                    )
                )
                await changed(db)
                await db.commit()
            await finish(factory, engine)
            await finish(factory, engine)
            async with factory() as db:
                assert (await state(db)).active_generation == 1
                assert (
                    await db.get(SeasonalityMonth, (1, identity("code:001"), 2025, 11))
                ).units == 100
                rows = [
                    {
                        "key": "code:001",
                        "stock": 100,
                        "status": "Кандидат",
                        "reasons": ["Продажи падают"],
                        "stop_factors": [],
                        "units_30": 1,
                    }
                ]
                await attach(rows, db, {"min_stock": 1}, date(2026, 10, 12))
                assert (
                    rows[0]["seasonality"]["status"] == "Сезон сейчас"
                    and rows[0]["seasonality"]["priority"] == 95
                )

    asyncio.run(run())


def test_delete_and_calendar_roll_rebuild_without_publishing_partial_results():
    async def run():
        async with fixture() as (engine, factory):
            await finish(factory, engine)
            async with factory() as db:
                await db.execute(delete(SaleItem).where(SaleItem.id == 1))
                await changed(db, rebuild=True)
                await db.commit()
            result = await worker.run_batch(
                factory, engine, date(2026, 10, 12), scan_size=7
            )
            assert result["active_generation"] == 1
            async with factory() as db:
                rows = [{"key": "code:001"}]
                metadata = await attach(rows, db, {"min_stock": 1}, date(2026, 10, 12))
                assert (
                    not metadata["ready"]
                    and rows[0]["seasonality"]["status"] == "Недостаточно данных"
                )
            await finish(factory, engine)
            async with factory() as db:
                assert (await state(db)).active_generation == 2
            await finish(factory, engine, date(2027, 1, 10))
            async with factory() as db:
                current = await state(db)
                assert current.active_generation == 3 and current.first_year == 2025
                assert (
                    await db.get(SeasonalityProfile, (3, identity("code:001")))
                ).data["pattern"] == "insufficient"

    asyncio.run(run())


def test_transaction_failure_and_retry_preserves_cursor(monkeypatch):
    async def run():
        async with fixture() as (engine, factory):
            original = worker.scan_batch

            async def fail(db, current, size):
                await original(db, current, size)
                raise RuntimeError("synthetic failure after writes")

            monkeypatch.setattr(worker, "scan_batch", fail)
            with pytest.raises(RuntimeError):
                await worker.run_batch(factory, engine, date(2026, 10, 12), scan_size=7)
            async with factory() as db:
                assert (await state(db)).cursor == 0
                assert (
                    await db.scalar(select(func.count()).select_from(SeasonalityMonth))
                    == 0
                )
            monkeypatch.setattr(worker, "scan_batch", original)
            await finish(factory, engine)
            async with factory() as db:
                assert (
                    await db.get(SeasonalityMonth, (1, identity("code:001"), 2025, 11))
                ).units == 90

    asyncio.run(run())


def test_memory_and_pending_import_prevent_history_query(monkeypatch):
    async def run():
        async with fixture() as (engine, factory):
            async with factory() as db:
                (await db.get(ImportBatch, 1)).status = "processing"
                await db.commit()
            with pytest.raises(worker.SeasonalityBusy):
                await worker.run_batch(factory, engine, date(2026, 10, 12))
            async with factory() as db:
                assert (await state(db)).cursor == 0

    asyncio.run(run())


def test_bounded_manual_run_can_pause_and_resume():
    from app.seasonality import run

    async def operation():
        async with fixture() as (engine, factory):
            result = await run(
                factory, engine, max_batches=1, scan_size=7, profile_size=1, pause=0.01
            )
            assert result["cursor"] == 7
            async with factory() as db:
                current = await state(db)
                current.stop_requested = True
                await db.commit()
            assert (await run(factory, engine, max_batches=2, scan_size=7, pause=0.01))[
                "cursor"
            ] == 7
            async with factory() as db:
                current = await state(db)
                current.stop_requested = False
                await db.commit()
            await finish(factory, engine)

    asyncio.run(operation())


def test_duplicate_articles_different_codes_and_unicode_empty_code():
    async def run():
        async with fixture() as (engine, factory):
            async with factory() as db:
                for index, code, name in ((49, "0002", "Другой"), (50, "\t", " Имя ")):
                    db.add(
                        SaleItem(
                            id=index,
                            sale_id=1,
                            code=code,
                            article="DUP" if index == 49 else None,
                            name=name,
                            quantity=1,
                            actual_price=1,
                        )
                    )
                await changed(db)
                await db.commit()
            await finish(factory, engine)
            async with factory() as db:
                profiles = (
                    await db.scalars(
                        select(SeasonalityProfile).where(
                            SeasonalityProfile.generation == 1
                        )
                    )
                ).all()
                keys = {p.product_key for p in profiles}
                assert keys == {"code:001", "code:0002", "unknown:имя"}

    asyncio.run(run())


def test_disabled_worker_does_not_start_and_run_cancellation_is_atomic(monkeypatch):
    import types
    from app.seasonality import worker as background

    async def run():
        async with fixture() as (engine, factory):
            stop = asyncio.Event()

            async def forbidden(*args, **kwargs):
                pytest.fail("disabled worker started")

            monkeypatch.setattr("app.seasonality.run", forbidden)
            task = asyncio.create_task(
                background(
                    factory,
                    engine,
                    stop=stop,
                    options=types.SimpleNamespace(
                        max_batches=1,
                        scan_size=7,
                        profile_size=1,
                        pause=0.01,
                        poll=0.01,
                    ),
                )
            )
            await asyncio.sleep(0.03)
            stop.set()
            await task
            async with factory() as db:
                assert (await state(db)).cursor == 0
            original = worker.scan_batch
            entered = asyncio.Event()

            async def cancel_after_writes(db, current, size):
                await original(db, current, size)
                entered.set()
                await asyncio.Event().wait()

            monkeypatch.setattr(worker, "scan_batch", cancel_after_writes)
            task = asyncio.create_task(
                worker.run_batch(factory, engine, date(2026, 10, 12), scan_size=7)
            )
            await entered.wait()
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            async with factory() as db:
                assert (await state(db)).cursor == 0

    asyncio.run(run())


def test_article_names_and_local_ambiguity_are_not_randomly_merged():
    from sqlalchemy import update
    from app.models import CatalogLookup
    from app.services.catalog_attributes import identity as catalog_identity

    async def run():
        async with fixture() as (engine, factory):
            async with factory() as db:
                await db.execute(update(SaleItem).values(code=None, article="DUP"))
                await db.commit()
            await finish(factory, engine)
            async with factory() as db:
                profile = await db.get(SeasonalityProfile, (1, identity("article:dup")))
                assert profile.data["pattern"] == "confirmed"
                await db.execute(
                    update(SaleItem).where(SaleItem.id == 1).values(name="Другой товар")
                )
                await changed(db, rebuild=True)
                await db.commit()
            await finish(factory, engine)
            async with factory() as db:
                profile = await db.get(SeasonalityProfile, (2, identity("article:dup")))
                assert (
                    profile.data["pattern"] == "insufficient"
                    and profile.name_min != profile.name_max
                )
                await db.execute(update(SaleItem).values(name="Товар"))
                db.add(
                    CatalogLookup(
                        identity=catalog_identity(None, "DUP"),
                        article="DUP",
                        status="ambiguous",
                        cycle=0,
                    )
                )
                await changed(db, rebuild=True)
                await db.commit()
            await finish(factory, engine)
            async with factory() as db:
                assert (
                    await db.get(SeasonalityProfile, (3, identity("article:dup")))
                ).data["pattern"] == "insufficient"

    asyncio.run(run())


def test_import_wrapper_and_delete_invalidate_seasonality_without_changing_sales_rules(
    monkeypatch,
):
    from app.services import imports as service
    from app.routers.sales import delete_sales, DeleteSales

    async def operation():
        async with fixture() as (engine, factory):
            monkeypatch.setattr(service, "engine", engine)
            monkeypatch.setattr(service, "SessionLocal", factory)

            async def failed(*args):
                raise RuntimeError("synthetic outside-parser failure")

            monkeypatch.setattr(service, "_process_import_with_source", failed)
            with pytest.raises(RuntimeError):
                await service.process_import(1)
            async with factory() as db:
                assert (await state(db)).source_revision == 1
            async with factory() as db:
                result = await delete_sales(DeleteSales(ids=[1]), db)
                assert result["deleted"] == 1
                current = await state(db)
                assert current.rebuild_required and current.source_revision == 2

    asyncio.run(operation())


def test_unknown_or_insufficient_memory_does_not_advance(monkeypatch):
    from fastapi import HTTPException

    async def operation():
        async with fixture() as (engine, factory):
            monkeypatch.setattr(worker, "available_memory", lambda: None)
            with pytest.raises(worker.SeasonalityBusy):
                await worker.run_batch(factory, engine, date(2026, 10, 12))
            monkeypatch.setattr(worker, "available_memory", lambda: 1024 ** 3)

            def reject():
                raise HTTPException(503, "memory_headroom")

            monkeypatch.setattr(worker, "ensure_headroom", reject)
            with pytest.raises(HTTPException) as error:
                await worker.run_batch(factory, engine, date(2026, 10, 12))
            assert error.value.status_code == 503
            async with factory() as db:
                assert (await state(db)).cursor == 0

    asyncio.run(operation())
