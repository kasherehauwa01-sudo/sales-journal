"""Optional tiny disposable localhost PostgreSQL tests, never app DATABASE_URL."""

import asyncio
from datetime import date
import os
import uuid
import pytest
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine
from app.services.seasonality_worker import (
    advisory,
    verify,
    SOURCE_LOCK,
    WORKER_LOCK,
    SeasonalityBusy,
)
from app.services.seasonality_storage import state
from test_seasonality_worker import fixture, finish


def url():
    value = os.environ.get("SEASONALITY_TEST_DATABASE_URL")
    if not value:
        pytest.skip("Requires disposable localhost seasonality_test database")
    parsed = make_url(value)
    if (
        parsed.host not in ("localhost", "127.0.0.1")
        or parsed.database != "seasonality_test"
    ):
        pytest.fail("Only disposable localhost seasonality_test allowed")
    return value


def test_postgres_advisory_concurrency_and_cancellation():
    target = url()

    async def run():
        engine = create_async_engine(target)
        try:
            async with advisory(engine, shared=True), advisory(engine, shared=True):
                with pytest.raises(SeasonalityBusy):
                    async with advisory(engine):
                        pass
            async with advisory(engine, key=WORKER_LOCK) as leader:
                await verify(leader, WORKER_LOCK)
                with pytest.raises(SeasonalityBusy):
                    async with advisory(engine, key=WORKER_LOCK):
                        pass
            ready = asyncio.Event()

            async def hold():
                async with advisory(engine, shared=True):
                    ready.set()
                    await asyncio.Event().wait()

            task = asyncio.create_task(hold())
            await ready.wait()
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            async with advisory(engine) as lock:
                await verify(lock, SOURCE_LOCK)
        finally:
            await engine.dispose()

    asyncio.run(run())


def test_postgres_bounded_aggregation_matches_sqlite_and_publishes_atomically():
    target = url()
    schema = "seasonality_test_" + uuid.uuid4().hex

    async def run():
        base = create_async_engine(target)
        try:
            async with base.begin() as conn:
                await conn.execute(text(f"CREATE SCHEMA {schema}"))
            async with fixture(target, schema) as (engine, factory):
                await finish(factory, engine)
                async with factory() as db:
                    current = await state(db)
                    assert (
                        current.active_generation == 1
                        and current.coverage == (1 << 24) - 1
                    )
                    profile = await db.scalar(
                        text(
                            "SELECT data FROM seasonality_profiles WHERE product_key='code:001'"
                        )
                    )
                    assert (
                        profile["pattern"] == "confirmed"
                        and profile["start_month"] == 10
                    )
            async with base.begin() as conn:
                row = (
                    await conn.execute(
                        text(
                            "SELECT enabled,algorithm_version FROM seasonality_state WHERE id=1"
                        )
                    )
                ).one()
                assert (
                    row.enabled is False and row.algorithm_version == 0
                )  # Migrated control remains opt-in.
        finally:
            async with base.begin() as conn:
                await conn.execute(text(f"DROP SCHEMA IF EXISTS {schema} CASCADE"))
            await base.dispose()

    asyncio.run(run())
