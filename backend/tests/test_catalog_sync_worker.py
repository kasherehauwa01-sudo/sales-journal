import asyncio
from contextlib import asynccontextmanager
from datetime import timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from sqlalchemy import event, select
from app.models import (
    CatalogLookup,
    CatalogProduct,
    CatalogSyncControl,
    CatalogSyncState,
    SaleItem,
)
from app.services import (
    catalog_sync as sync,
    catalog_sync_control as control,
    catalog_sync_worker as worker,
)
from app.services.catalog_lookup_client import LookupError
from app.config import settings
from app.routers import catalog_sync as routes
from app.services.settings_auth import COOKIE_NAME
from app.services.settings_auth_crypto import create_session
from test_catalog_directory import database, results


@asynccontextmanager
async def fixture(rows=(), cursor=0, upper=None, cycle=1, completed=False):
    async with database(rows) as (engine, factory):

        @event.listens_for(engine.sync_engine, "connect")
        def probe(connection, _):
            connection.create_function("pg_try_advisory_xact_lock", 1, lambda _: 1)

        # Existing pooled SQLite connection also needs the PostgreSQL probe stub.
        async with engine.connect() as connection:
            await connection.run_sync(
                lambda c: c.connection.create_function(
                    "pg_try_advisory_xact_lock", 1, lambda _: 1
                )
            )
        async with factory() as db:
            state = await sync._state(db)
            state.cycle, state.cursor, state.upper_id = (
                cycle,
                cursor,
                len(rows) if upper is None else upper,
            )
            state.completed = completed
            state.status = "paused"
            state.started_at = sync.now() - timedelta(hours=1)
            db.add(
                CatalogSyncControl(
                    id=1,
                    requested=True,
                    status="queued",
                    mode="discovery",
                    heartbeat_at=sync.now(),
                    run_started_at=sync.now(),
                )
            )
            await db.commit()
        yield engine, factory


@pytest.fixture(autouse=True)
def quiet(monkeypatch):
    @asynccontextmanager
    async def lock(engine, lock_id=sync.SYNC_LOCK_ID):
        yield

    async def verify(*args):
        return None

    monkeypatch.setattr(worker, "verify_lock", verify)
    monkeypatch.setattr(worker, "sync_lock", lock)
    monkeypatch.setattr(worker, "available_memory", lambda: 1024 * 1024 * 1024)
    monkeypatch.setattr(settings, "catalog_sync_batch_size", 2)
    monkeypatch.setattr(settings, "catalog_sync_pause_seconds", 0.001)
    monkeypatch.setattr(settings, "catalog_sync_poll_seconds", 0.001)
    monkeypatch.setattr(settings, "catalog_sync_retry_seconds", 0.001)
    monkeypatch.setattr(settings, "catalog_sync_retry_limit", 1)


async def command(factory, requested=True, mode=None):
    async with factory() as db:
        row = await control.control_row(db)
        row.requested = requested
        row.status = "queued" if requested else "stopping"
        if mode:
            row.mode = mode
        await db.commit()


def test_paused_resume_stops_after_batch_and_continues_without_reset():
    async def run():
        calls = []
        async with fixture(
            [("0001", "01"), ("0002", "02"), ("0003", "03"), ("0004", "04")], cursor=1
        ) as (engine, factory):

            async def client(items):
                calls.append(items)
                if len(calls) == 1:
                    await command(factory, False)  # Pause during in-flight HTTP.
                return results(items)

            await worker.run_request(engine, factory, asyncio.Event(), client)
            async with factory() as db:
                state = await db.get(CatalogSyncState, 1)
                assert (state.cycle, state.cursor, state.upper_id) == (1, 3, 4)
                assert (await control.control_row(db)).status == "paused"
                assert (await db.get(CatalogProduct, 1)).code == "0003"
            await command(factory)
            await worker.run_request(engine, factory, asyncio.Event(), client)
            async with factory() as db:
                state = await db.get(CatalogSyncState, 1)
                assert (state.cycle, state.cursor, state.completed) == (1, 4, True)
                assert (await control.control_row(db)).status == "completed"
            assert [[item["code"] for item in batch] for batch in calls] == [
                ["0002", "0003"],
                ["0004"],
            ]

    asyncio.run(run())


def test_restart_clears_only_command_and_preserves_all_saved_progress():
    async def run():
        async with fixture([("001", "A")], cursor=77202, upper=4623292) as (_, factory):
            async with factory() as db:
                state = await db.get(CatalogSyncState, 1)
                state.processed, state.matched, state.not_found = 19062, 18791, 265
                row = await control.control_row(db)
                row.refresh_cursor = "c" * 64
                row.refresh_completed = False
                await db.commit()
            await worker.boot(factory)
            async with factory() as db:
                state = await db.get(CatalogSyncState, 1)
                assert (
                    state.cycle,
                    state.cursor,
                    state.upper_id,
                    state.processed,
                    state.matched,
                    state.not_found,
                ) == (1, 77202, 4623292, 19062, 18791, 265)
                row = await control.control_row(db)
                assert not row.requested and row.status == "paused"
                assert row.refresh_cursor == "c" * 64 and not row.refresh_completed

    asyncio.run(run())


def test_standalone_worker_boot_never_executes_old_request(monkeypatch):
    async def run():
        async with fixture([("001", "A")]) as (engine, factory):
            called = []

            async def forbidden(*args, **kwargs):
                called.append(True)

            monkeypatch.setattr(worker, "run_request", forbidden)
            shutdown = asyncio.Event()
            task = asyncio.create_task(worker.serve(engine, factory, shutdown))
            await asyncio.sleep(0.02)
            shutdown.set()
            await task
            assert called == []
            async with factory() as db:
                row = await control.control_row(db)
                assert not row.requested and row.heartbeat_at is None

    asyncio.run(run())


def test_transient_api_error_retries_same_checkpoint_then_completes():
    async def run():
        calls = []
        async with fixture([("0001", "A"), ("0002", "B")]) as (engine, factory):

            async def client(items):
                calls.append(items)
                if len(calls) == 1:
                    raise LookupError("transport_error", retryable=True)
                return results(items)

            await worker.run_request(engine, factory, asyncio.Event(), client)
            assert calls[0] == calls[1]
            async with factory() as db:
                state = await db.get(CatalogSyncState, 1)
                assert state.completed and state.cursor == 2 and state.errors == 1
                assert (await control.control_row(db)).status == "completed"

    asyncio.run(run())


@pytest.mark.parametrize("retryable,count", [(False, 1), (True, 2)])
def test_api_errors_are_bounded_and_do_not_advance_cursor(retryable, count):
    async def run():
        calls = []
        async with fixture([("001", "A")]) as (engine, factory):

            async def client(items):
                calls.append(items)
                raise LookupError("http_503", retryable=retryable)

            await worker.run_request(engine, factory, asyncio.Event(), client)
            assert len(calls) == count
            async with factory() as db:
                row = await control.control_row(db)
                assert not row.requested and row.status == "failed"
                state = await db.get(CatalogSyncState, 1)
                assert state.cursor == 0 and state.errors == count

    asyncio.run(run())


def test_memory_pressure_pauses_safely_without_http(monkeypatch):
    async def run():
        async with fixture([("001", "A")]) as (engine, factory):
            calls = []

            def reject():
                raise LookupError("memory_headroom", retryable=True)

            monkeypatch.setattr(worker, "check_memory", reject)

            async def client(items):
                calls.append(items)

            await worker.run_request(engine, factory, asyncio.Event(), client)
            assert calls == []
            async with factory() as db:
                row = await control.control_row(db)
                assert row.status == "paused" and not row.requested
                assert (await db.get(CatalogSyncState, 1)).cursor == 0

    asyncio.run(run())


def test_new_sales_tail_preserves_cycle_and_does_not_refresh_old_identities():
    async def run():
        async with fixture(
            [("001", "A"), ("001", "A"), ("002", "B")],
            cursor=1,
            upper=1,
            completed=True,
        ) as (engine, factory):
            async with factory() as db:
                lookup = CatalogLookup(
                    identity=sync.identity("001", "A"),
                    code="001",
                    article="a",
                    status="not_found",
                    cycle=1,
                )
                db.add(lookup)
                await db.commit()
            calls = []

            async def client(items):
                calls.append(items)
                return results(items)

            await worker.run_request(engine, factory, asyncio.Event(), client)
            assert calls == [[{"code": "002", "article": "B"}]]
            async with factory() as db:
                state = await db.get(CatalogSyncState, 1)
                assert state.cycle == 1 and state.cursor == 3 and state.completed
            await command(factory)
            await worker.run_request(engine, factory, asyncio.Event(), client)
            assert len(calls) == 1  # No automatic complete-cycle restart.

    asyncio.run(run())


def test_refresh_resume_preserves_matching_and_catalog_on_failure():
    async def run():
        async with fixture([("0001", "A"), ("0002", "B")]) as (engine, factory):

            async def initial(items):
                return results(items)

            await worker.run_request(engine, factory, asyncio.Event(), initial)
            await command(factory, mode="refresh")

            async def failed(items):
                raise LookupError("http_401")

            await worker.run_request(engine, factory, asyncio.Event(), failed)
            async with factory() as db:
                assert (await db.get(CatalogProduct, 1)).brand == "Бренд"
                row = await control.control_row(db)
                assert row.refresh_cursor is None and not row.refresh_completed
                assert (await db.get(CatalogSyncState, 1)).cursor == 2
            await command(factory)
            calls = []

            async def refresh(items):
                calls.append(items)
                return results(items, status="ambiguous")

            await worker.run_request(engine, factory, asyncio.Event(), refresh)
            async with factory() as db:
                assert (await control.control_row(db)).refresh_completed
                assert (await db.get(CatalogProduct, 1)).brand == "Бренд"
                assert all(
                    row.status == "ambiguous"
                    for row in (await db.scalars(select(CatalogLookup))).all()
                )
                state = await db.get(CatalogSyncState, 1)
                assert state.cursor == 2 and state.cycle == 1
            assert all(
                "product_id" not in item and item["code"].startswith("000")
                for item in calls[0]
            )

    asyncio.run(run())


def test_shutdown_stops_after_successful_batch_and_resume_works():
    async def run():
        async with fixture([("001", "A"), ("002", "B"), ("003", "C")]) as (
            engine,
            factory,
        ):
            shutdown = asyncio.Event()

            async def client(items):
                shutdown.set()
                return results(items)

            await worker.run_request(engine, factory, shutdown, client)
            async with factory() as db:
                assert (await db.get(CatalogSyncState, 1)).cursor == 2
                assert not (await control.control_row(db)).requested
            await worker.boot(factory)
            await command(factory)

            async def again(items):
                return results(items)

            await worker.run_request(engine, factory, asyncio.Event(), again)
            async with factory() as db:
                assert (await db.get(CatalogSyncState, 1)).completed

    asyncio.run(run())


def test_worker_uses_existing_advisory_lock_and_rejects_busy(monkeypatch):
    calls = []

    @asynccontextmanager
    async def busy(engine, lock_id=sync.SYNC_LOCK_ID):
        calls.append(lock_id)
        raise sync.SyncBusy()
        yield

    monkeypatch.setattr(worker, "sync_lock", busy)

    async def run():
        async with fixture() as (engine, factory):
            await worker.run_request(engine, factory, asyncio.Event())
            async with factory() as db:
                row = await control.control_row(db)
                assert not row.requested and row.status == "paused"
            assert calls == [sync.SYNC_LOCK_ID]

    asyncio.run(run())


def test_memory_check_keeps_existing_minimum(monkeypatch):
    monkeypatch.setattr(worker, "available_memory", lambda: 128 * 1024 * 1024)
    with pytest.raises(LookupError, match="memory_headroom"):
        worker.check_memory()
    monkeypatch.setattr(worker, "available_memory", lambda: None)
    with pytest.raises(LookupError, match="memory_metrics_unavailable"):
        worker.check_memory()


def test_status_has_no_false_percentage_and_stale_worker_is_paused():
    async def run():
        async with fixture(cursor=77202, upper=4623292) as (_, factory):
            async with factory() as db:
                row = await control.control_row(db)
                row.status = "running"
                row.heartbeat_at = sync.now() - timedelta(minutes=1)
                await db.commit()
                result = await control.status(db)
                assert result["status"] == "paused" and not result["worker_ready"]
                assert result["eta_seconds"] is None and "percentage" not in result
                assert result["state"]["upper_id"] == 4623292

    asyncio.run(run())


@pytest.mark.parametrize(
    "path,method", [("status", "get"), ("start", "post"), ("pause", "post")]
)
def test_admin_auth_is_required(path, method, monkeypatch):
    monkeypatch.setattr(settings, "settings_admin_password_hash", "configured")
    monkeypatch.setattr(settings, "settings_admin_session_secret", "x" * 32)
    app = FastAPI()
    app.include_router(routes.router)
    with TestClient(app) as client:
        response = getattr(client, method)(
            f"/settings/catalog-sync/{path}",
            **({"json": {}} if method == "post" else {}),
        )
        assert response.status_code == 401


def test_signed_admin_can_manage_and_cross_origin_is_rejected(monkeypatch):
    monkeypatch.setattr(settings, "settings_admin_password_hash", "configured")
    monkeypatch.setattr(settings, "settings_admin_session_secret", "x" * 32)
    called = []

    async def start(db, mode):
        called.append(mode)
        return {"accepted": True}

    async def db():
        yield object()

    monkeypatch.setattr(control, "start", start)
    app = FastAPI()
    app.include_router(routes.router)
    app.dependency_overrides[routes.get_db] = db
    with TestClient(app) as client:
        client.cookies.set(COOKIE_NAME, create_session("x" * 32))
        assert client.post("/settings/catalog-sync/start", json={}).status_code == 202
        assert (
            client.post(
                "/settings/catalog-sync/start",
                json={},
                headers={"Origin": "https://evil.invalid"},
            ).status_code
            == 403
        )
        assert called == ["discovery"]


def test_configuration_and_compose_are_opt_in():
    from app.config import Settings
    from pydantic import ValidationError

    config = Settings(_env_file=None)
    assert (
        config.catalog_sync_batch_size == 100 and config.catalog_sync_pause_seconds == 1
    )
    with pytest.raises(ValidationError):
        Settings(_env_file=None, catalog_sync_batch_size=251)
    with pytest.raises(ValidationError):
        Settings(_env_file=None, catalog_sync_min_available_mb=128)
    root = Path(__file__).parents[2]
    text = (root / "docker-compose.yml").read_text()
    section = text.split("  catalog-sync:")[1].split("  frontend:")[0]
    assert 'profiles: ["catalog-sync"]' in section and 'restart: "no"' in section
    main = (root / "backend/app/main.py").read_text()
    assert "catalog_worker" not in main and "catalog_sync_worker" not in main


def test_cancellation_during_http_keeps_checkpoint_and_manual_resume():
    async def run():
        async with fixture([("001", "A"), ("002", "B")]) as (engine, factory):
            entered = asyncio.Event()

            async def client(items):
                entered.set()
                await asyncio.Event().wait()

            task = asyncio.create_task(
                worker.run_request(engine, factory, asyncio.Event(), client)
            )
            await entered.wait()
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            async with factory() as db:
                assert (await db.get(CatalogSyncState, 1)).cursor == 0
                assert not (await control.control_row(db)).requested
            await command(factory)

            async def resumed(items):
                return results(items)

            await worker.run_request(engine, factory, asyncio.Event(), resumed)
            async with factory() as db:
                assert (await db.get(CatalogSyncState, 1)).completed

    asyncio.run(run())


def test_refresh_pauses_after_batch_and_resumes_without_duplicate_calls():
    async def run():
        async with fixture([(f"000{i}", f"A{i}") for i in range(4)]) as (
            engine,
            factory,
        ):

            async def initial(items):
                return results(items)

            await worker.run_request(engine, factory, asyncio.Event(), initial)
            await command(factory, mode="refresh")
            calls = []

            async def refresh(items):
                calls.append(items)
                if len(calls) == 1:
                    await command(factory, False)
                return results(items, brand=None, material=None)

            await worker.run_request(engine, factory, asyncio.Event(), refresh)
            async with factory() as db:
                row = await control.control_row(db)
                assert row.refresh_cursor and not row.refresh_completed
            await command(factory)
            await worker.run_request(engine, factory, asyncio.Event(), refresh)
            assert len(calls) == 2
            assert {item["code"] for item in calls[0]}.isdisjoint(
                {item["code"] for item in calls[1]}
            )
            async with factory() as db:
                assert (await control.control_row(db)).refresh_completed
                assert (await db.get(CatalogProduct, 1)).brand is None

    asyncio.run(run())


def test_lost_lock_during_http_prevents_characteristics_and_checkpoint_commit():
    async def run():
        async with fixture([("001", "A")]) as (_, factory):
            calls = []

            async def guard():
                calls.append(True)
                if len(calls) > 1:
                    raise LookupError("lock_lost")

            async def client(items):
                return results(items)

            with pytest.raises(LookupError, match="lock_lost"):
                await sync.synchronize(factory, client=client, lock_guard=guard)
            async with factory() as db:
                assert (await db.get(CatalogSyncState, 1)).cursor == 0
                assert await db.get(CatalogProduct, 1) is None

    asyncio.run(run())
