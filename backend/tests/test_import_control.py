"""Synthetic metadata only. Native PG opts in to an isolated localhost database."""

import asyncio
from datetime import date, timedelta
from types import SimpleNamespace as Row
import os
from pathlib import Path
import smtplib
import uuid
import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from alembic import command
from alembic.config import Config
from app.models import Base, ImportBatch
from app.models.import_control import *
from app.services import import_control as control
from app.services.import_integrity import mail_failure
from app.config import settings
import start_backend as launcher

DAY = date(2026, 11, 8)


@pytest.mark.parametrize(
    "error,status,retry",
    [
        (smtplib.SMTPDataError(451, b"private"), "failed", True),
        (smtplib.SMTPAuthenticationError(535, b"private"), "failed", False),
        (smtplib.SMTPServerDisconnected("private"), "uncertain", False),
        (OSError("private"), "uncertain", False),
    ],
)
def test_smtp_only_retries_certain_rejections(error, status, retry):
    result = mail_failure(error)
    assert (result[0], result[2]) == (status, retry)
    assert "private" not in result[1]


@pytest.fixture
def pg(monkeypatch, request):
    url = os.environ.get("IMPORT_CONTROL_TEST_DATABASE_URL")
    if not url:
        pytest.skip("Isolated IMPORT_CONTROL_TEST_DATABASE_URL required")
    parsed = sa.engine.make_url(url)
    assert (
        parsed.host in ("127.0.0.1", "localhost")
        and parsed.database == "import_control_test"
    )
    admin = sa.create_engine(
        parsed.set(drivername="postgresql+psycopg"), poolclass=sa.pool.NullPool
    )
    schema = "importctl_" + uuid.uuid4().hex
    with admin.begin() as conn:
        conn.execute(sa.text("CREATE SCHEMA " + schema))
    sync = sa.create_engine(
        parsed.set(drivername="postgresql+psycopg"),
        poolclass=sa.pool.NullPool,
        connect_args={"options": "-csearch_path=" + schema},
    )
    monkeypatch.setattr(
        settings, "database_url", parsed.render_as_string(hide_password=False)
    )
    monkeypatch.setenv("DATABASE_URL", parsed.render_as_string(hide_password=False))
    monkeypatch.setenv("PGOPTIONS", "-csearch_path=" + schema)
    config = Config()
    config.set_main_option(
        "script_location", str(Path(__file__).parents[1] / "alembic")
    )
    command.upgrade(config, getattr(request, "param", "20261111_27"))
    async_engine = create_async_engine(
        parsed.set(drivername="postgresql+asyncpg"),
        poolclass=sa.pool.NullPool,
        connect_args={"server_settings": {"search_path": schema}},
    )
    try:
        yield sync, async_engine, async_sessionmaker(
            async_engine, expire_on_commit=False
        ), schema
    finally:
        asyncio.run(async_engine.dispose())
        sync.dispose()
        with admin.begin() as conn:
            conn.execute(sa.text("DROP SCHEMA " + schema + " CASCADE"))
        admin.dispose()


async def received(db, index, state="success", day=DAY):
    batch = ImportBatch(
        filename=f"fixture_{index}_{day:%d.%m.%Y}.html",
        file_size=100,
        status="completed" if state == "success" else "failed",
        total_rows=0,
        processed_rows=0,
        added_rows=0,
        duplicate_rows=0,
        skipped_rows=0,
        error_rows=0,
        period_start=day,
        period_end=day,
    )
    db.add(batch)
    await db.flush()
    await control.record_receipt(db, batch, digest="a" * 64)
    return batch


def test_duplicate_receipts_and_corrected_file_quarantine(pg, monkeypatch):
    from app.services import import_integrity as worker

    monkeypatch.setattr(worker, "SessionLocal", pg[2])

    async def run():
        async with pg[2]() as db:
            await seed(db, 1)
            first = await received(db, 0)
            await db.commit()
            repeat = await received(db, 0)
            await db.flush()
            r = await db.scalar(
                sa.select(ImportReceipt).where(ImportReceipt.import_id == repeat.id)
            )
            assert r.duplicate_of is not None
            repeat.status = "processing"
            r.status = "processing"
            r.sha256 = "b" * 64
            await db.commit()
            identity = repeat.id
        assert (
            await worker.revision_guard(identity) == "corrected_source_requires_review"
        )
        async with pg[2]() as db:
            c = await control.config(db)
            c.enabled = False
            await db.commit()
        assert await worker.revision_guard(identity) is None

    asyncio.run(run())


@pytest.mark.parametrize(
    "alter",
    [
        "ALTER TABLE import_control_config ALTER COLUMN enabled SET DEFAULT true",
        "ALTER TABLE import_expectation_rules DISABLE TRIGGER import_rule_guard",
        "ALTER TABLE import_control_config DROP CONSTRAINT import_control_config_id_check",
        "ALTER TABLE import_control_issues DROP CONSTRAINT uq_import_issue_day_department",
    ],
)
@pytest.mark.parametrize("pg", ["20261109_26"], indirect=True)
def test_revision26_blocks_incompatible_schema_with_old_models(pg, alter):
    with pg[0].begin() as conn:
        conn.execute(sa.text(alter))
    with pytest.raises(launcher.StartupBlocked):
        launcher.check_schema(pg[0], sa.MetaData(), "20261109_26")


@pytest.mark.parametrize("pg", ["20261109_26"], indirect=True)
def test_revision26_checks_all_models_and_noop_rollback_contract(pg):
    from sqlalchemy.dialects.postgresql import dialect

    for name, contract in launcher.IMPORT_CONTROL_CONTRACT.items():
        table = Base.metadata.tables[name]
        assert set(contract["columns"]) == set(table.c.keys())
        for column in table.columns:
            assert contract["columns"][column.name] == (
                launcher.type_name(column.type, dialect()),
                column.nullable,
            )
    launcher.check_schema(pg[0], sa.MetaData(), "20261109_26")
    with pytest.raises(launcher.StartupBlocked):
        launcher.migrate(pg[0], "20261030_25")


def test_ftp_cycle_waits_all_files_retries_and_marker_without_new_scheduler(
    pg, monkeypatch
):
    from app.services import ftp_autoload as ftp, import_integrity as worker
    from app.models import FtpConfig

    monkeypatch.setattr(ftp, "SessionLocal", pg[2])
    monkeypatch.setattr(ftp, "engine", pg[1])
    monkeypatch.setattr(worker, "SessionLocal", pg[2])
    monkeypatch.setattr(worker, "engine", pg[1])
    events = []

    class Remote:
        def nlst(self):
            return ["/source/fixture_0_08.11.2026.html", "/source/done_08.11.2026.ok"]

        def quit(self):
            pass

    monkeypatch.setattr(ftp, "_connect", lambda c: Remote())

    async def process(config, filename, cycle):
        events.append("file_started")
        await asyncio.sleep(0)
        events.append("file_finished")

    async def finish(identity, marker, error=None):
        assert events == ["file_started", "file_finished"]
        assert marker is False
        events.append("cycle_finished")

    monkeypatch.setattr(ftp, "_process_file", process)
    monkeypatch.setattr(worker, "finish_cycle", finish)

    async def run():
        async with pg[2]() as db:
            db.add(
                FtpConfig(
                    id=1,
                    host="fixture.invalid",
                    port=21,
                    username="fixture",
                    password="fixture",
                    directory="/",
                    protocol="FTP",
                    enabled=True,
                    retries=2,
                    retry_delay=0,
                )
            )
            await db.commit()
        await ftp._run_autoload_cycle("synthetic-cycle", DAY, "done_{date}.ok")
        attempts = []

        def operation():
            attempts.append(1)
            if len(attempts) < 2:
                raise OSError("synthetic")
            return "ok"

        assert await ftp._with_retries(Row(retries=2, retry_delay=0), operation) == "ok"
        assert len(attempts) == 2

    asyncio.run(run())
    assert events[-1] == "cycle_finished"


def test_cancellation_marks_batch_error_and_preserves_receipt(
    pg, monkeypatch, tmp_path
):
    from app.services import imports, import_integrity as worker

    monkeypatch.setattr(imports, "SessionLocal", pg[2])
    monkeypatch.setattr(worker, "SessionLocal", pg[2])

    def cancelled(path):
        raise asyncio.CancelledError()

    monkeypatch.setattr(imports, "read_sales", cancelled)

    async def run():
        path = tmp_path / "fixture.html"
        path.write_text("<html>synthetic</html>")
        async with pg[2]() as db:
            batch = ImportBatch(
                filename="fixture.html",
                stored_path=str(path),
                file_size=path.stat().st_size,
                status="queued",
            )
            db.add(batch)
            await db.commit()
            identity = batch.id
        with pytest.raises(asyncio.CancelledError):
            await imports._process_import(identity, 0)
        async with pg[2]() as db:
            batch = await db.get(ImportBatch, identity)
            receipt = await db.scalar(sa.select(ImportReceipt))
            assert (
                batch.status == "failed"
                and batch.error_text == "import_interrupted"
                and receipt.status == "error"
            )

    asyncio.run(run())


def test_simultaneous_source_revisions_are_serialized_and_cancel_releases_lock(
    pg, monkeypatch
):
    from app.services import import_integrity as worker

    monkeypatch.setattr(worker, "SessionLocal", pg[2])
    monkeypatch.setattr(worker, "engine", pg[1])

    async def run():
        async with pg[2]() as db:
            await seed(db, 1)
            batch = await received(db, 0)
            await db.commit()
            identity = batch.id
        async with worker.source_owner(identity) as first:
            assert first
            async with worker.source_owner(identity) as second:
                assert not second
        async with worker.source_owner(identity) as resumed:
            assert resumed
        started = asyncio.Event()

        async def task():
            async with worker.source_owner(identity) as acquired:
                assert acquired
                started.set()
                await asyncio.Event().wait()

        pending = asyncio.create_task(task())
        await started.wait()
        pending.cancel()
        with pytest.raises(asyncio.CancelledError):
            await pending
        async with worker.source_owner(identity) as resumed:
            assert resumed

    asyncio.run(run())


@pytest.mark.parametrize("pg", ["20261030_25"], indirect=True)
def test_explicit25_to26_preserves_existing_metadata_and_old_image_contract(
    pg, monkeypatch
):
    with pg[0].begin() as conn:
        conn.execute(
            sa.text(
                "INSERT INTO import_batches(filename,file_size,status) VALUES('synthetic-existing.html',100,'completed')"
            )
        )
        previous = conn.execute(
            sa.text("SELECT id,filename,file_size,status FROM import_batches")
        ).all()
    with pytest.raises(launcher.StartupBlocked, match="revision differs"):
        launcher.check_schema(pg[0], Base.metadata, "20261109_26")
    launcher.migrate(pg[0], "20261109_26", Base.metadata)
    with pg[0].connect() as conn:
        assert launcher.revision(conn) == "20261109_26"
        assert (
            conn.execute(
                sa.text("SELECT id,filename,file_size,status FROM import_batches")
            ).all()
            == previous
        )
        assert conn.scalar(sa.text("SELECT count(*) FROM import_control_issues")) == 0
    monkeypatch.setattr(
        launcher.subprocess,
        "run",
        lambda *a, **k: pytest.fail("26→26 must not execute Alembic"),
    )
    launcher.migrate(pg[0], "20261109_26", Base.metadata)
    old = sa.MetaData()
    for table in Base.metadata.sorted_tables:
        if table.name not in {
            **launcher.IMPORT_CONTROL_CONTRACT,
            **launcher.IMPORT_HISTORY_CONTRACT,
        }:
            table.to_metadata(old)
    launcher.check_schema(pg[0], old, "20261109_26")


def test_lost_cycle_owner_is_interrupted_without_synthetic_completion(pg, monkeypatch):
    from app.services import import_integrity as worker

    monkeypatch.setattr(worker, "SessionLocal", pg[2])

    async def run():
        async with pg[2]() as db:
            await seed(db, 1)
            db.add(
                ImportCycle(
                    id="lost-synthetic-owner",
                    sales_date=DAY,
                    status="running",
                    source_confirmed=False,
                )
            )
            await db.commit()
        identity, day, _ = await worker.begin_cycle(sales_day=DAY)
        async with pg[2]() as db:
            old = await db.get(ImportCycle, "lost-synthetic-owner")
            assert old.status == "interrupted" and not old.source_confirmed
        await worker.finish_cycle(identity, False)
        async with pg[2]() as db:
            cycle = await db.get(ImportCycle, identity)
            assert cycle.status == "finished" and cycle.source_confirmed

    asyncio.run(run())


def test_real_parser_repeat_and_corrected_source_do_not_duplicate_sales(
    pg, monkeypatch, tmp_path
):
    from app.services import imports, import_integrity as worker
    from app.models import Sale

    monkeypatch.setattr(imports, "SessionLocal", pg[2])
    monkeypatch.setattr(worker, "SessionLocal", pg[2])
    monkeypatch.setattr(worker, "engine", pg[1])

    async def run():
        async with pg[2]() as db:
            await seed(db, 1)

        async def perform(amount):
            path = tmp_path / f"source-{uuid.uuid4().hex}.html"
            path.write_text(
                f"""<html><p>за период с 08.11.2026 по 08.11.2026</p><table>
   <tr><td>Дата</td><td>№ Док.</td><td>Клиент</td><td>Подразделение</td><td>Сумма</td><td>Товары</td></tr>
   <tr><td>08.11.2026</td><td>РН-SYNTH-1</td><td>Синтетический клиент</td><td>Тестовое подразделение</td><td>{amount}</td><td>Тестовый товар</td></tr></table></html>"""
            )
            async with pg[2]() as db:
                batch = ImportBatch(
                    filename="fixture_0_08.11.2026.html",
                    stored_path=str(path),
                    file_size=path.stat().st_size,
                    status="queued",
                )
                db.add(batch)
                await db.commit()
                identity = batch.id
            await imports._process_import_with_source(identity)
            assert not path.exists()
            async with pg[2]() as db:
                batch = await db.get(ImportBatch, identity)
                return (
                    batch.status,
                    batch.added_rows,
                    batch.duplicate_rows,
                    batch.error_text,
                )

        assert (await perform("100,00"))[:3] == ("completed", 1, 0)
        assert (await perform("100,00"))[:3] == ("completed", 0, 1)
        corrected = await perform("200,00")
        assert (
            corrected[0] == "failed"
            and corrected[3] == "corrected_source_requires_review"
        )
        async with pg[2]() as db:
            assert await db.scalar(sa.select(sa.func.count()).select_from(Sale)) == 1
            assert str(await db.scalar(sa.select(Sale.total_amount))) == "100.00"

    asyncio.run(run())


def test_ftp_single_owner_cancellation_marks_cycle_and_allows_restart(pg, monkeypatch):
    from app.services import ftp_autoload as ftp, import_integrity as worker
    from app.models import FtpConfig

    for module in (ftp, worker):
        monkeypatch.setattr(module, "SessionLocal", pg[2])
        monkeypatch.setattr(module, "engine", pg[1])
    entered = asyncio.Event()
    calls = []

    async def cycle(*args):
        calls.append(args)
        entered.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(ftp, "_run_autoload_cycle", cycle)

    async def run():
        async with pg[2]() as db:
            await seed(db, 1)
            db.add(
                FtpConfig(
                    id=1,
                    host="fixture.invalid",
                    port=21,
                    username="fixture",
                    password="fixture",
                    directory="/",
                    protocol="FTP",
                    enabled=True,
                )
            )
            await db.commit()
        task = asyncio.create_task(ftp.run_autoload())
        await entered.wait()
        await ftp.run_autoload()
        assert len(calls) == 1
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        async with pg[2]() as db:
            cycle = await db.scalar(sa.select(ImportCycle))
            assert cycle.status == "failed" and cycle.error_code == "cycle_interrupted"

        async def completed(*args):
            pass

        monkeypatch.setattr(ftp, "_run_autoload_cycle", completed)
        await ftp.run_autoload()
        async with pg[2]() as db:
            assert (
                await db.scalar(sa.select(sa.func.count()).select_from(ImportCycle))
                == 2
            )

    asyncio.run(run())


def test_original_upload_basename_is_recorded_without_changing_safe_disk_path(
    pg, monkeypatch, tmp_path
):
    import httpx
    from app.services.settings_auth_crypto import create_session
    from app.services.settings_auth import COOKIE_NAME
    from app.routers import imports as router

    secret = "synthetic-secret-" * 4
    monkeypatch.setattr(settings, "settings_admin_password_hash", "configured")
    monkeypatch.setattr(settings, "settings_admin_session_secret", secret)
    monkeypatch.setattr(router, "import_temp_dir", lambda: tmp_path)

    async def no_processing(identity):
        pass

    monkeypatch.setattr(router, "process_import", no_processing)

    async def run():
        app = api_app(pg)
        app.include_router(router.router)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://test",
            cookies={COOKIE_NAME: create_session(secret)},
        ) as client:
            response = await client.post(
                "/imports",
                files=[
                    (
                        "files",
                        (
                            "fixture (original).html",
                            b"<html>synthetic</html>",
                            "text/html",
                        ),
                    )
                ],
            )
            assert response.status_code == 202, response.text
        async with pg[2]() as db:
            batch = await db.scalar(sa.select(ImportBatch))
            receipt = await db.scalar(sa.select(ImportReceipt))
            assert (
                receipt.filename == "fixture (original).html"
                and receipt.status == "queued"
            )
            assert (
                "(" not in batch.stored_path
                and batch.filename == "fixture _original_.html"
            )

    asyncio.run(run())


def test_overlapping_changed_period_and_legacy_unhashed_source_require_review(
    pg, monkeypatch
):
    from app.services import import_integrity as worker

    monkeypatch.setattr(worker, "SessionLocal", pg[2])

    async def run():
        async with pg[2]() as db:
            await seed(db, 1)
            previous = await received(db, 0)
            previous.declared_period_start = DAY - timedelta(days=1)
            previous.declared_period_end = DAY
            await control.record_receipt(db, previous)
            await db.commit()
            current = await received(db, 0)
            current.status = "processing"
            current.declared_period_start = DAY
            current.declared_period_end = DAY + timedelta(days=1)
            await control.record_receipt(db, current, digest="b" * 64)
            await db.commit()
            identity = current.id
        assert (
            await worker.revision_guard(identity) == "corrected_source_requires_review"
        )
        async with pg[2]() as db:
            legacy = ImportBatch(
                filename=f"fixture_0_{DAY:%d.%m.%Y}.html",
                file_size=100,
                status="completed",
                added_rows=1,
                period_start=DAY,
                period_end=DAY,
            )
            db.add(legacy)
            await db.commit()
        assert await worker.revision_guard(identity) == "legacy_source_requires_review"

    asyncio.run(run())


def test_repeated_cancellation_does_not_leak_pooled_session_locks(pg):
    from app.services.import_integrity import owned_lock

    async def run():
        pooled = create_async_engine(
            pg[1].url,
            connect_args={"server_settings": {"search_path": pg[3]}},
            pool_size=2,
            max_overflow=0,
        )
        entered = asyncio.Event()

        async def hold():
            async with owned_lock(pooled, 734190279) as acquired:
                assert acquired
                entered.set()
                await asyncio.Event().wait()

        try:
            task = asyncio.create_task(hold())
            await entered.wait()
            task.cancel()
            await asyncio.sleep(0)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            async with pg[1].connect() as observer:
                assert (
                    await observer.scalar(
                        sa.text(
                            "SELECT count(*) FROM pg_locks WHERE locktype='advisory' AND classid=0 AND objid=734190279"
                        )
                    )
                    == 0
                )
        finally:
            await pooled.dispose()

    asyncio.run(run())


async def seed(db, n=12):
    c = await control.config(db)
    c.enabled = True
    c.automatic = True
    c.email_enabled = True
    c.start_date = DAY
    c.recipients = "fixture@example.invalid"
    for i in range(n):
        dep = ImportDepartment(code=f"SYNTH-{i}", name=f"Fixture {i}")
        db.add(dep)
        await db.flush()
        db.add(
            ImportRule(
                department_id=dep.id,
                start_date=DAY,
                filename_template=f"fixture_{i}_{{date}}.html",
                date_format="dmy",
                display_name=dep.name,
                active=True,
                controlled=True,
            )
        )
    await db.commit()


def api_app(pg):
    from fastapi import FastAPI
    from app.database import get_db

    app = FastAPI()

    async def session():
        async with pg[2]() as db:
            yield db

    app.dependency_overrides[get_db] = session
    return app
