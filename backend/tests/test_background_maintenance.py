"""Synthetic lifecycle/concurrency tests; native tests use the localhost schema27 fixture."""

import asyncio
from contextlib import suppress
from datetime import datetime, timezone, timedelta
import threading
from types import SimpleNamespace
from uuid import uuid4

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from app.models import FtpConfig, ImportBatch, RfmSnapshot, Sale
from app.services import (
    background_workers as workers,
    ftp_autoload as ftp,
    rfm_snapshot as rfm,
)
from app.services.async_resources import run_blocking
from test_import_control import pg
from test_rfm_snapshot import database, seed, END


async def cancel(task):
    task.cancel()
    with suppress(asyncio.CancelledError):
        await task


def test_periodic_timeout_rolls_back_and_never_overlaps_or_logs_payload(caplog):
    async def run():
        active = 0
        peak = 0
        calls = 0
        closed = 0

        async def hanging():
            nonlocal active, peak, calls, closed
            calls += 1
            active += 1
            peak = max(active, peak)
            try:
                await asyncio.Event().wait()
            finally:
                active -= 1
                closed += 1

        task = asyncio.create_task(
            workers.periodic("timeout_fixture", hanging, interval=0.01, timeout=0.01)
        )
        await asyncio.sleep(0.13)
        await cancel(task)
        assert 1 <= calls <= 4 and closed == calls and peak == 1
        assert workers.observations["timeout_fixture"]["error_code"] == "timeout"

        async def failure():
            raise RuntimeError("PRIVATE_SENTINEL")

        task = asyncio.create_task(
            workers.periodic("failure_fixture", failure, interval=0.01)
        )
        await asyncio.sleep(0.02)
        await cancel(task)
        assert "PRIVATE_SENTINEL" not in caplog.text

    asyncio.run(run())


def test_cancel_waits_for_thread_before_releasing_side_effect_ownership():
    async def run():
        entered = threading.Event()
        release = threading.Event()
        finished = threading.Event()

        def network():
            entered.set()
            release.wait(2)
            finished.set()

        task = asyncio.create_task(run_blocking(network))
        assert await asyncio.to_thread(entered.wait, 1)
        task.cancel()
        await asyncio.sleep(0.01)
        task.cancel()
        await asyncio.sleep(0.01)
        assert not task.done() and not finished.is_set()
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert finished.is_set()

    asyncio.run(run())


def test_refresh_interval_is_anchored_to_build_not_reset_by_every_import(monkeypatch):
    now = datetime.now(timezone.utc)
    signature = {"revision": 2, "added_rows": 10, "upper_batch": 3}
    previous = SimpleNamespace(
        period_to=END,
        period_from=rfm.snapshot_period(END)[0],
        algorithm_version=1,
        boundaries={"_refresh_source": {"revision": 1}},
        calculated_at=now - timedelta(seconds=301),
    )
    assert rfm.refresh_due(previous, signature, END, now=now)
    previous.calculated_at = now - timedelta(seconds=299)
    assert not rfm.refresh_due(previous, signature, END, now=now)
    previous.boundaries["_refresh_source"] = signature
    assert rfm.snapshot_current(previous, signature, END)
    assert not rfm.refresh_due(previous, signature, END, now=now + timedelta(days=1))


def test_legacy_ftp_scope_reproduces_idle_snapshot_and_new_cycle_closes_it(
    pg, monkeypatch
):
    """The old scope is reproduced verbatim around SELECT+network wait, no real FTP."""

    async def run():
        nonce = "ftp-lifecycle-" + uuid4().hex
        engine = create_async_engine(
            pg[1].url,
            connect_args={
                "server_settings": {"search_path": pg[3], "application_name": nonce}
            },
        )
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        monkeypatch.setattr(ftp, "SessionLocal", sessions)
        async with sessions() as db:
            db.add(
                FtpConfig(
                    id=1,
                    host="fixture.invalid",
                    port=21,
                    username="fixture",
                    password="SECRET_SENTINEL",
                    directory="/",
                    protocol="FTP",
                    enabled=True,
                    retries=1,
                    retry_delay=0,
                )
            )
            await db.commit()
        entered = asyncio.Event()
        release = asyncio.Event()

        async def listing(*args):
            entered.set()
            await release.wait()
            return [], False

        async def legacy():
            async with sessions() as db:
                config = await db.get(FtpConfig, 1)
                await listing(config, None)

        async def idle():
            async with pg[1].connect() as conn:
                return await conn.scalar(
                    sa.text(
                        "SELECT count(*) FROM pg_stat_activity WHERE application_name=:name AND state='idle in transaction' AND backend_xmin IS NOT NULL"
                    ),
                    {"name": nonce},
                )

        task = asyncio.create_task(legacy())
        await entered.wait()
        assert await idle() == 1
        release.set()
        await task
        entered.clear()
        release.clear()
        monkeypatch.setattr(ftp, "_with_retries", listing)
        task = asyncio.create_task(ftp._run_autoload_cycle())
        await entered.wait()
        assert await idle() == 0
        release.set()
        await task
        await engine.dispose()

    asyncio.run(run())


def test_background_rfm_durable_coalescing_partial_import_restart_and_busy(monkeypatch):
    async def run():
        async with database() as (engine, sessions):
            await seed(sessions)
            monkeypatch.setattr(rfm, "current_date", lambda: END)
            first = await rfm.rebuild_snapshot(engine, as_of=END)
            original = rfm.rfm_rows
            calls = 0

            async def counted(*a, **kw):
                nonlocal calls
                calls += 1
                return await original(*a, **kw)

            monkeypatch.setattr(rfm, "rfm_rows", counted)
            # Simulate three committed imports, even an interrupted partial file.
            for n in range(3):
                async with sessions() as db:
                    batch = await db.get(ImportBatch, 1)
                    batch.added_rows += 1
                    db.add(
                        Sale(
                            id=100 + n,
                            client="Synthetic",
                            sale_date=END,
                            total_amount=1,
                            department="A",
                            document_number=str(n),
                            fingerprint="new" + str(n),
                            import_id=1,
                            social=False,
                        )
                    )
                    await db.commit()
                await rfm.rebuild_after_sales_update(engine)  # lightweight wake-up only
            assert calls == 0
            assert (await rfm.refresh_tick(engine))["status"] == "pending"
            async with sessions() as db:
                snapshot = await db.get(RfmSnapshot, first["id"])
                snapshot.calculated_at -= timedelta(seconds=301)
                await db.commit()
            # Lose the process Event: persistent metadata still discovers dirty data.
            monkeypatch.setattr(rfm, "refresh_requested", asyncio.Event())
            assert (await rfm.refresh_tick(engine))["status"] == "current"
            assert calls == 1
            assert (await rfm.refresh_tick(engine))[
                "status"
            ] == "current" and calls == 1
            # Exclusive source ownership is never waited on inside the RFM worker.
            async with engine.connect() as owner:
                await owner.execute(
                    sa.text("SELECT pg_advisory_xact_lock(:k)"), {"k": rfm.SOURCE_LOCK}
                )
                assert (await rfm.rebuild_snapshot(engine, background=True))[
                    "status"
                ] == "busy"
                await owner.rollback()
            async with sessions() as db:
                state = await rfm.refresh_status(db)
                assert state["state"] == "current" and state["duration_ms"] is not None

    asyncio.run(run())


def test_rfm_late_source_update_cancellation_and_cross_connection_singleflight(
    monkeypatch,
):
    from app.services.source_control import advisory
    from app.services.sales_revision import changed

    async def run():
        async with database() as (engine, sessions):
            await seed(sessions)
            monkeypatch.setattr(rfm, "current_date", lambda: END)
            first = await rfm.rebuild_snapshot(engine, as_of=END)
            original = rfm.rfm_rows
            entered = asyncio.Event()
            release = asyncio.Event()

            async def slow(*a, **kw):
                entered.set()
                await release.wait()
                return await original(*a, **kw)

            monkeypatch.setattr(rfm, "rfm_rows", slow)
            async with sessions() as db:
                snapshot = await db.get(RfmSnapshot, first["id"])
                snapshot.calculated_at -= timedelta(seconds=301)
                await changed(db)
                await db.commit()
            build = asyncio.create_task(
                rfm.rebuild_snapshot(engine, background=True, as_of=END)
            )
            await asyncio.wait_for(entered.wait(), 2)
            async with sessions() as db:
                assert (await rfm.refresh_status(db))["state"] == "rebuilding"
            assert (
                await asyncio.wait_for(
                    rfm.rebuild_snapshot(engine, background=True, as_of=END), 2
                )
            )["status"] == "busy"

            async def late_import():
                async with advisory(engine, shared=True, wait=True):
                    async with sessions() as db:
                        batch = await db.get(ImportBatch, 1)
                        batch.added_rows += 1
                        db.add(
                            Sale(
                                id=200,
                                client="Late synthetic",
                                sale_date=END,
                                total_amount=11,
                                department="A",
                                document_number="late",
                                fingerprint="late",
                                import_id=1,
                                social=False,
                            )
                        )
                        await changed(db)
                        await db.commit()

            incoming = asyncio.create_task(late_import())
            await asyncio.sleep(0.03)
            assert not incoming.done()
            release.set()
            built = await build
            await incoming
            async with sessions() as db:
                assert (await rfm.refresh_status(db))["state"] == "pending"
            # Cancellation cannot publish a partial generation or retain either lock.
            async with sessions() as db:
                snapshot = await db.get(RfmSnapshot, built["id"])
                snapshot.calculated_at -= timedelta(seconds=301)
                await db.commit()
            entered.clear()
            release.clear()
            build = asyncio.create_task(
                rfm.rebuild_snapshot(engine, background=True, as_of=END)
            )
            await asyncio.wait_for(entered.wait(), 2)
            await cancel(build)
            async with sessions() as db:
                assert (await rfm.active_snapshot(db)).id == built["id"]
                for key in (rfm.RFM_LOCK, rfm.SOURCE_LOCK):
                    assert await db.scalar(
                        sa.text("SELECT pg_try_advisory_xact_lock(:k)"), {"k": key}
                    )
            monkeypatch.setattr(rfm, "rfm_rows", original)
            result = await rfm.refresh_tick(engine)
            assert result["status"] == "current"
            async with sessions() as db:
                assert (await rfm.active_snapshot(db)).client_count == 4
                assert (await rfm.refresh_status(db))["state"] == "current"

    asyncio.run(run())


def test_ftp_file_releases_transactions_during_download_import_and_remote_finish(
    pg, monkeypatch, tmp_path
):
    from app.models import AutoImportLog

    async def run():
        nonce = "ftp-file-" + uuid4().hex
        engine = create_async_engine(
            pg[1].url,
            connect_args={
                "server_settings": {"search_path": pg[3], "application_name": nonce}
            },
        )
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        monkeypatch.setattr(ftp, "SessionLocal", sessions)
        monkeypatch.setattr(ftp, "import_temp_dir", lambda: tmp_path)

        class Remote:
            def retrbinary(self, command, write):
                write(b"synthetic")

            def delete(self, name):
                pass

            def quit(self):
                pass

        monkeypatch.setattr(ftp, "_connect", lambda c: Remote())

        async def no_transaction():
            async with pg[1].connect() as db:
                assert (
                    await db.scalar(
                        sa.text(
                            "SELECT count(*) FROM pg_stat_activity WHERE application_name=:n AND state='idle in transaction'"
                        ),
                        {"n": nonce},
                    )
                    == 0
                )

        original = ftp._with_retries

        async def operation(config, fn):
            await no_transaction()
            return await original(config, fn)

        monkeypatch.setattr(ftp, "_with_retries", operation)
        from app.services import import_integrity as control

        async def receipt(*args):
            await no_transaction()

        monkeypatch.setattr(control, "receipt_before", receipt)

        async def process(identity):
            await no_transaction()
            async with sessions() as db:
                batch = await db.get(ImportBatch, identity)
                batch.status = "completed"
                batch.error_rows = 0
                batch.added_rows = 1
                await db.commit()

        monkeypatch.setattr(ftp, "process_import", process)
        config = ftp.FtpSettings(
            "FTP", "fixture.invalid", 21, "fixture", "PRIVATE_SENTINEL", "/", 1, 0
        )
        await ftp._process_file(config, "synthetic.html")
        async with sessions() as db:
            entry = await db.scalar(sa.select(AutoImportLog))
            assert entry.status == "completed"
        assert not list(tmp_path.iterdir())
        await engine.dispose()

    asyncio.run(run())


def test_coalesce_timestamp_never_hides_a_new_source_revision(monkeypatch):
    from app.services.sales_revision import changed

    async def run():
        async with database() as (engine, sessions):
            await seed(sessions)
            built = await rfm.rebuild_snapshot(engine, as_of=END)
            async with sessions() as db:
                # Models a request queued before publication, followed by a writer
                # getting SOURCE ownership before that waiting request acquires it.
                snapshot = await db.get(RfmSnapshot, built["id"])
                snapshot.calculated_at = datetime.now(timezone.utc) + timedelta(
                    seconds=1
                )
                batch = await db.get(ImportBatch, 1)
                batch.added_rows += 1
                db.add(
                    Sale(
                        id=300,
                        client="Concurrent synthetic",
                        sale_date=END,
                        total_amount=10,
                        department="A",
                        document_number="x",
                        fingerprint="concurrent",
                        import_id=1,
                        social=False,
                    )
                )
                await changed(db)
                await db.commit()
            result = await rfm.rebuild_snapshot(engine, coalesce=True, as_of=END)
            assert result["id"] != built["id"] and result["client_count"] == 4

    asyncio.run(run())
