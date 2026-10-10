"""Optional tiny PostgreSQL checks. Only localhost + explicit test database allowed."""

import asyncio
import os
import uuid
from contextlib import asynccontextmanager
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from fastapi import HTTPException
import pytest
from app.models import Base, CatalogSyncControl, CatalogSyncState
from app.services import (
    catalog_sync as sync,
    catalog_sync_control as control,
    catalog_sync_worker as worker,
)

URL = os.environ.get("CATALOG_SYNC_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not URL, reason="Requires disposable localhost PostgreSQL"
)


@asynccontextmanager
async def database():
    url = make_url(URL)
    assert (
        url.host in {"localhost", "127.0.0.1"} and url.database == "catalog_sync_test"
    )
    schema = "catalog_test_" + uuid.uuid4().hex
    admin = create_async_engine(url)
    async with admin.begin() as connection:
        await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = create_async_engine(
        url,
        pool_size=4,
        max_overflow=0,
        connect_args={"server_settings": {"search_path": schema}},
    )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        async with factory() as db:
            state = await sync._state(db)
            state.cycle, state.cursor, state.upper_id = 1, 77202, 4623292
            state.completed = False
            state.status = "paused"
            db.add(CatalogSyncControl(id=1, heartbeat_at=sync.now(), status="paused"))
            await db.commit()
        yield engine, factory
    finally:
        await engine.dispose()
        async with admin.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        await admin.dispose()


def test_concurrent_start_commands_accept_exactly_one():
    async def run():
        async with database() as (_, factory):

            async def start():
                async with factory() as db:
                    try:
                        await control.start(db)
                        return 202
                    except HTTPException as exc:
                        return exc.status_code

            assert sorted(await asyncio.gather(start(), start(), start())) == [
                202,
                409,
                409,
            ]
            async with factory() as db:
                state = await db.get(CatalogSyncState, 1)
                assert state.cursor == 77202 and state.upper_id == 4623292
                assert (await control.control_row(db)).requested

    asyncio.run(run())


def test_cli_advisory_lock_blocks_web_start_and_releases_cleanly():
    async def run():
        async with database() as (engine, factory):
            async with sync.sync_lock(engine):
                async with factory() as db:
                    with pytest.raises(HTTPException) as exc:
                        await control.start(db)
                    assert exc.value.status_code == 409
                with pytest.raises(sync.SyncBusy):
                    async with sync.sync_lock(engine):
                        pass
            async with factory() as db:
                await control.start(db)
            async with sync.sync_lock(engine):
                pass  # Transaction-scoped web probe left no pooled session lock.

    asyncio.run(run())


def test_only_one_worker_can_boot_and_resume_preserves_checkpoint():
    async def run():
        async with database() as (engine, factory):
            async with sync.sync_lock(engine, worker.WORKER_LOCK_ID):
                with pytest.raises(sync.SyncBusy):
                    async with sync.sync_lock(engine, worker.WORKER_LOCK_ID):
                        await worker.boot(factory)
                await worker.boot(factory)
            async with factory() as db:
                await control.start(db)
            # Simulated restart: cancel durable execution intent, retain data.
            async with sync.sync_lock(engine, worker.WORKER_LOCK_ID):
                await worker.boot(factory)
            async with factory() as db:
                row = await control.control_row(db)
                state = await db.get(CatalogSyncState, 1)
                assert not row.requested and row.status == "paused"
                assert (state.cycle, state.cursor, state.upper_id) == (
                    1,
                    77202,
                    4623292,
                )
                await control.start(db)
                assert (await control.control_row(db)).requested

    asyncio.run(run())


def test_additive_migration_does_not_reset_existing_state():
    import importlib.util
    from pathlib import Path
    from alembic.operations import Operations
    from alembic.migration import MigrationContext

    async def run():
        async with database() as (engine, factory):
            path = (
                Path(__file__).parents[1]
                / "alembic/versions/20261010_16_catalog_sync_control.py"
            )
            spec = importlib.util.spec_from_file_location("control_revision", path)
            migration = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(migration)
            async with engine.begin() as connection:
                await connection.execute(text("DROP TABLE catalog_sync_control"))

                def upgrade(connection):
                    with Operations.context(MigrationContext.configure(connection)):
                        migration.upgrade()

                await connection.run_sync(upgrade)
            async with factory() as db:
                state = await db.get(CatalogSyncState, 1)
                assert (state.cycle, state.cursor, state.upper_id) == (
                    1,
                    77202,
                    4623292,
                )
                row = await control.control_row(db)
                assert row.status == "paused" and not row.requested
            async with engine.begin() as connection:

                def downgrade(connection):
                    with Operations.context(MigrationContext.configure(connection)):
                        migration.downgrade()

                await connection.run_sync(downgrade)
            async with factory() as db:
                assert (await db.get(CatalogSyncState, 1)).cursor == 77202

    asyncio.run(run())


def test_guard_detects_lost_session_lock_before_data_commit():
    async def run():
        async with database() as (engine, _):
            async with sync.sync_lock(engine) as connection:
                await worker.verify_lock(connection, sync.SYNC_LOCK_ID)
                await connection.execute(
                    text("SELECT pg_advisory_unlock(:key)"), {"key": sync.SYNC_LOCK_ID}
                )
                await connection.commit()
                from app.services.catalog_lookup_client import LookupError

                with pytest.raises(LookupError, match="lock_lost"):
                    await worker.verify_lock(connection, sync.SYNC_LOCK_ID)

    asyncio.run(run())


def test_real_worker_continues_old_cycle_with_four_connection_pool(monkeypatch):
    from datetime import date
    from app.models import ImportBatch, Sale, SaleItem
    from test_catalog_directory import results
    from app.config import settings

    monkeypatch.setattr(settings, "catalog_sync_batch_size", 2)
    monkeypatch.setattr(settings, "catalog_sync_pause_seconds", 0.01)
    monkeypatch.setattr(settings, "catalog_sync_poll_seconds", 0.01)
    monkeypatch.setattr(worker, "available_memory", lambda: 1024 * 1024 * 1024)

    async def run():
        async with database() as (engine, factory):
            async with factory() as db:
                row = await control.control_row(db)
                row.requested = True  # Must NOT execute this old persisted intent.
                row.heartbeat_at = None
                db.add(
                    ImportBatch(
                        id=1, filename="fixture", file_size=0, status="completed"
                    )
                )
                await db.flush()
                db.add(
                    Sale(
                        id=1,
                        sale_date=date(2026, 10, 10),
                        document_number="1",
                        department="test",
                        total_amount=1,
                        fingerprint="1",
                        import_id=1,
                    )
                )
                await db.flush()
                for index in range(77202, 77206):
                    db.add(
                        SaleItem(
                            id=index,
                            sale_id=1,
                            code=f"000{index}",
                            article="A",
                            name="fixture",
                            quantity=1,
                            actual_price=12.25,
                        )
                    )
                await db.commit()
            calls = []

            async def client(items):
                calls.extend(items)
                return results(items)

            shutdown = asyncio.Event()
            task = asyncio.create_task(
                worker.serve(engine, factory, shutdown, client=client)
            )
            try:
                async with asyncio.timeout(5):
                    while True:
                        async with factory() as db:
                            row = await control.control_row(db)
                            if not row.requested and control.worker_ready(row):
                                break
                        await asyncio.sleep(0.01)
                    assert calls == []
                    async with factory() as db:
                        await control.start(db)
                    while True:
                        async with factory() as db:
                            if (await control.control_row(db)).status == "completed":
                                break
                        await asyncio.sleep(0.01)
            finally:
                shutdown.set()
                await task
            assert [item["code"] for item in calls] == [
                "00077203",
                "00077204",
                "00077205",
            ]
            async with factory() as db:
                state = await db.get(CatalogSyncState, 1)
                assert (state.cycle, state.cursor, state.upper_id, state.completed) == (
                    1,
                    77205,
                    4623292,
                    True,
                )
                assert (await control.control_row(db)).heartbeat_at is None

    asyncio.run(run())
