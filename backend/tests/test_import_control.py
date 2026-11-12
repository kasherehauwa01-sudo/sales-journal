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
from app.services.import_control_worker import mail_failure
from app.config import settings
import start_backend as launcher

DAY = date(2026, 11, 8)


def rule(index=0, **extra):
    return Row(
        filename_template=f"fixture_{index}_{{date}}.html",
        date_format="dmy",
        start_date=DAY,
        end_date=None,
        **extra,
    )


@pytest.mark.parametrize(
    "count,label",
    [
        (1, "Не загружен 1 файл"),
        (2, "Не загружено 2 файла"),
        (5, "Не загружено 5 файлов"),
        (11, "Не загружено 11 файлов"),
        (21, "Не загружен 21 файл"),
        (22, "Не загружено 22 файла"),
        (112, "Не загружено 112 файлов"),
    ],
)
def test_russian_files(count, label):
    assert control.missing_label(count) == label


@pytest.mark.parametrize(
    "a,af,b,bf,overlap",
    [
        ("fixture_{date}.html", "dmy", "fixture_{date}.html", "dmy", True),
        ("fixture_A_{date}.html", "dmy", "fixture_B_{date}.html", "dmy", False),
        ("fixture_{date}.html", "dmy", "fixture_08.11.2026.html", "dmy", True),
        ("fixture_{date}.html", "dmy", "fixture_{date}.html", "iso", False),
    ],
)
def test_bounded_pattern_intersection(a, af, b, bf, overlap):
    assert control.patterns_overlap(a, af, b, bf) == overlap


@pytest.mark.parametrize(
    "value",
    [
        "../a.html",
        "x/{date}.html",
        "a_{bad}.html",
        "a_{date}_{date}.html",
        "a\n.html",
        "a#1.html",
    ],
)
def test_unsafe_templates_rejected(value):
    with pytest.raises(ValueError):
        control.validate_template(value)


@pytest.mark.parametrize(
    "state,period,expected",
    [
        ("success", True, "success"),
        ("success", False, "unavailable"),
        ("error", False, "error"),
        ("processing", False, "waiting"),
    ],
)
def test_receipts_do_not_confuse_missing_unknown_and_received(state, period, expected):
    r = rule()
    receipt = Row(
        filename=control.expected_filename(r, DAY),
        status=state,
        period_start=DAY if period else None,
        period_end=DAY if period else None,
        error_code="synthetic",
        import_id=1,
    )
    assert control.receipt_state(r, DAY, [receipt])[0] == expected
    assert control.receipt_state(r, DAY, [])[0] == "missing"


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


async def seed(db, n=12):
    c = await control.config(db)
    c.enabled = True
    c.start_date = DAY
    c.day_offset = 1
    for i in range(n):
        department = ImportDepartment(code=f"SYNTH-{i}", name=f"Fixture {i}")
        db.add(department)
        await db.flush()
        await control.save_rule(
            db,
            department,
            DAY,
            None,
            f"fixture_{i}_{{date}}.html",
            "dmy",
            department.name,
            True,
            True,
            "test",
        )
    await db.commit()
    return c


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


@pytest.mark.parametrize("successful", [0, 10, 11, 12])
def test_complete_counts_and_idempotent_notices(pg, successful):
    async def run():
        async with pg[2]() as db:
            c = await seed(db)
            c.email_enabled = True
            c.recipients = "fixture@example.invalid"
            for i in range(successful):
                await received(db, i)
            result = await control.check_date(db, DAY, confirmed=True)
            await db.commit()
            assert (
                result["expected"] == 12
                and result["successful"] == successful
                and result["missing"] == 12 - successful
            )
            assert result["status"] == (
                "complete" if successful == 12 else "incomplete"
            )
            await control.check_date(db, DAY, confirmed=True)
            await db.commit()
            assert await db.scalar(
                sa.select(sa.func.count()).select_from(ImportNotice)
            ) == int(successful < 12)

    asyncio.run(run())


def test_closed_and_new_stores_preserve_history(pg):
    async def run():
        async with pg[2]() as db:
            await seed(db)
            old = await db.get(ImportDepartment, 1)
            await control.save_rule(
                db,
                old,
                DAY + timedelta(days=1),
                None,
                "fixture_0_{date}.html",
                "dmy",
                old.name,
                False,
                True,
                "test",
            )
            new = ImportDepartment(code="SYNTH-new", name="New fixture")
            db.add(new)
            await db.flush()
            await control.save_rule(
                db,
                new,
                DAY + timedelta(days=2),
                None,
                "fixture_new_{date}.html",
                "dmy",
                new.name,
                True,
                True,
                "test",
            )
            for day, expected in [
                (DAY, 12),
                (DAY + timedelta(days=1), 11),
                (DAY + timedelta(days=2), 12),
            ]:
                result = await control.check_date(db, day, confirmed=True)
                assert result["expected"] == expected

    asyncio.run(run())


def test_waiting_and_late_arrival_keep_issue_history(pg):
    async def run():
        async with pg[2]() as db:
            await seed(db, 1)
            result = await control.check_date(db, DAY)
            assert result["status"] == "waiting"
            assert (
                await db.scalar(sa.select(sa.func.count()).select_from(ImportIssue))
                == 0
            )
            await control.check_date(db, DAY, confirmed=True)
            await db.commit()
            await received(db, 0)
            await db.commit()
            result = await control.check_date(db, DAY, confirmed=True)
            await db.commit()
            issue = await db.scalar(sa.select(ImportIssue))
            assert (
                result["status"] == "complete"
                and issue.status == "resolved"
                and issue.resolved_at
                and issue.closure_method == "late_import"
            )

    asyncio.run(run())


def test_database_trigger_rejects_conflicting_rules(pg):
    async def run():
        async with pg[2]() as db:
            await seed(db, 1)
            other = ImportDepartment(code="SYNTH-other", name="Other")
            db.add(other)
            await db.flush()
            db.add(
                ImportRule(
                    department_id=other.id,
                    start_date=DAY,
                    filename_template="fixture_0_{date}.html",
                    date_format="dmy",
                    display_name="Other",
                    active=True,
                    controlled=True,
                )
            )
            with pytest.raises(sa.exc.IntegrityError):
                await db.commit()

    asyncio.run(run())


@pytest.mark.parametrize("pg", ["20261109_26"], indirect=True)
def test_revision26_readonly_start_and_defaults(pg):
    launcher.check_schema(pg[0], Base.metadata, "20261109_26")
    with pg[0].begin() as conn:
        assert launcher.revision(conn) == "20261109_26"
        conn.execute(sa.text("INSERT INTO import_control_config(id) VALUES(1)"))
        assert conn.execute(
            sa.text("SELECT enabled,automatic,email_enabled FROM import_control_config")
        ).one() == (False, False, False)
    launcher.migrate(pg[0], "20261109_26", Base.metadata)


@pytest.mark.parametrize(
    "kind,expected",
    [
        ("empty", "error"),
        ("validator", "error"),
        ("zero_sales", "success"),
        ("unknown", "unavailable"),
        ("multi_day_observed", "unavailable"),
    ],
)
def test_file_quality_and_zero_sales(pg, kind, expected):
    async def run():
        async with pg[2]() as db:
            await seed(db, 1)
            batch = await received(db, 0)
            if kind == "empty":
                batch.file_size = 0
            if kind == "validator":
                batch.error_rows = 1
            if kind in ("zero_sales", "unknown", "multi_day_observed"):
                batch.period_start = batch.period_end = None
            if kind == "zero_sales":
                batch.declared_period_start = batch.declared_period_end = DAY
                batch.source_period_method = "HTML_PREAMBLE"
            if kind == "multi_day_observed":
                batch.period_start = DAY - timedelta(days=1)
                batch.period_end = DAY + timedelta(days=1)
            await control.record_receipt(db, batch)
            await db.flush()
            r = await db.scalar(sa.select(ImportRule))
            receipts = (await db.scalars(sa.select(ImportReceipt))).all()
            assert control.receipt_state(r, DAY, receipts)[0] == expected

    asyncio.run(run())


def test_queued_batch_prevents_premature_completion_and_mail(pg):
    async def run():
        async with pg[2]() as db:
            c = await seed(db, 1)
            c.email_enabled = True
            c.recipients = "fixture@example.invalid"
            db.add(
                ImportBatch(
                    filename=f"fixture_0_{DAY:%d.%m.%Y}.html",
                    file_size=100,
                    status="queued",
                )
            )
            await db.commit()
            state = await control.check_date(db, DAY, confirmed=True)
            await db.commit()
            assert state["status"] == "waiting"
            assert not await db.scalar(sa.select(ImportIssue.id))
            assert not await db.scalar(sa.select(ImportNotice.id))

    asyncio.run(run())


def test_concurrent_check_deduplicates_issues_and_notices(pg):
    async def run():
        async with pg[2]() as db:
            c = await seed(db, 13)
            c.email_enabled = True
            c.recipients = "fixture@example.invalid"
            await db.commit()

        async def check():
            async with pg[2]() as db:
                result = await control.check_date(db, DAY, confirmed=True)
                await db.commit()
                return result

        results = await asyncio.gather(check(), check(), check())
        assert all(r["expected"] == 13 for r in results)
        async with pg[2]() as db:
            assert (
                await db.scalar(sa.select(sa.func.count()).select_from(ImportIssue))
                == 13
            )
            assert (
                await db.scalar(sa.select(sa.func.count()).select_from(ImportNotice))
                == 1
            )

    asyncio.run(run())


def test_duplicate_receipts_and_corrected_file_quarantine(pg, monkeypatch):
    from app.services import import_control_worker as worker

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


def test_checked_rule_cannot_be_overwritten_or_history_disabled(pg):
    async def run():
        async with pg[2]() as db:
            await seed(db, 1)
            department = await db.scalar(sa.select(ImportDepartment))
            # A draft may be corrected before any check.
            await control.save_rule(
                db,
                department,
                DAY,
                None,
                "new_{date}.html",
                "dmy",
                "Renamed",
                True,
                True,
                "test",
            )
            await control.check_date(db, DAY, confirmed=True)
            await db.commit()
            with pytest.raises(ValueError, match="Историческое"):
                await control.save_rule(
                    db,
                    department,
                    DAY,
                    None,
                    "new_{date}.html",
                    "dmy",
                    "Renamed",
                    False,
                    False,
                    "test",
                )
            await db.rollback()
            department = await db.scalar(sa.select(ImportDepartment))
            await control.save_rule(
                db,
                department,
                DAY + timedelta(days=1),
                None,
                "new_{date}.html",
                "dmy",
                "Renamed",
                False,
                False,
                "test",
            )
            assert (await control.check_date(db, DAY, confirmed=True))["expected"] == 1
            await db.commit()
            assert await db.scalar(sa.select(ImportIssue.status)) == "missing"

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


@pytest.mark.parametrize(
    "outcome", ["sent", "temporary", "permanent", "uncertain", "partial", "no_smtp"]
)
def test_outbox_delivery_and_no_unsafe_repeat(pg, monkeypatch, outcome):
    from app.services import import_control_worker as worker
    from app.models import SmtpConfig

    monkeypatch.setattr(worker, "SessionLocal", pg[2])
    monkeypatch.setattr(worker, "engine", pg[1])
    calls = []

    def send(*args):
        calls.append(1)
        if outcome == "temporary":
            raise smtplib.SMTPDataError(451, b"synthetic")
        if outcome == "permanent":
            raise smtplib.SMTPDataError(550, b"synthetic")
        if outcome == "uncertain":
            raise smtplib.SMTPServerDisconnected("synthetic")
        return (
            {"fixture@example.invalid": (450, "synthetic")}
            if outcome == "partial"
            else {}
        )

    monkeypatch.setattr(worker, "_send", send)

    async def run():
        async with pg[2]() as db:
            c = await seed(db, 1)
            c.automatic = True
            c.email_enabled = True
            c.recipients = "fixture@example.invalid"
            db.add(
                ImportCycle(
                    id=str(uuid.uuid4()),
                    sales_date=DAY,
                    status="finished",
                    source_confirmed=True,
                    config_revision=c.revision,
                )
            )
            if outcome != "no_smtp":
                db.add(
                    SmtpConfig(
                        id=1,
                        host="invalid.test",
                        port=587,
                        username="fixture",
                        password="fixture",
                        sender_email="fixture@example.invalid",
                        sender_name="Fixture",
                        security="STARTTLS",
                    )
                )
            await control.check_date(db, DAY)
            await db.commit()
        await asyncio.gather(worker.deliver_one(), worker.deliver_one())
        async with pg[2]() as db:
            item = await db.scalar(sa.select(ImportNotice))
            expected = {
                "sent": "sent",
                "temporary": "failed",
                "permanent": "failed",
                "uncertain": "uncertain",
                "partial": "uncertain",
                "no_smtp": "failed",
            }[outcome]
            assert item.status == expected
            if outcome == "temporary":
                item.next_attempt = control.now() - timedelta(seconds=1)
                await db.commit()
                await worker.deliver_one()
                assert len(calls) == 2
            else:
                await db.commit()
                await worker.deliver_one()
                assert len(calls) == int(outcome != "no_smtp")

    asyncio.run(run())


def test_sender_crash_and_stale_notice_never_blindly_resend(pg, monkeypatch):
    from app.services import import_control_worker as worker

    monkeypatch.setattr(worker, "SessionLocal", pg[2])
    monkeypatch.setattr(worker, "engine", pg[1])
    monkeypatch.setattr(worker, "_send", lambda *a: pytest.fail("No SMTP allowed"))

    async def run():
        async with pg[2]() as db:
            c = await seed(db, 1)
            c.automatic = True
            c.email_enabled = True
            c.recipients = "fixture@example.invalid"
            db.add(
                ImportCycle(
                    id=str(uuid.uuid4()),
                    sales_date=DAY,
                    status="finished",
                    source_confirmed=True,
                    config_revision=c.revision,
                )
            )
            await control.check_date(db, DAY)
            await db.flush()
            item = await db.scalar(sa.select(ImportNotice))
            item.status = "sending"
            await db.commit()
        await worker.deliver_one()
        async with pg[2]() as db:
            item = await db.scalar(sa.select(ImportNotice))
            assert item.status == "uncertain"
            item.status = "pending"
            await received(db, 0)
            await db.commit()
        await worker.deliver_one()
        async with pg[2]() as db:
            assert await db.scalar(sa.select(ImportNotice.status)) == "cancelled"

    asyncio.run(run())


def test_cycle_failure_unavailable_and_completion_proof_survives_settings_edits(pg):
    async def run():
        async with pg[2]() as db:
            c = await seed(db, 1)
            cycle = ImportCycle(
                id=str(uuid.uuid4()),
                sales_date=DAY,
                status="failed",
                source_confirmed=False,
                config_revision=c.revision,
            )
            db.add(cycle)
            await db.commit()
            assert (await control.check_date(db, DAY))["status"] == "unavailable"
            cycle.status = "finished"
            cycle.source_confirmed = True
            c.revision += 1
            await db.commit()
            assert (await control.check_date(db, DAY))["status"] == "incomplete"

    asyncio.run(run())


def api_app(pg):
    from fastapi import FastAPI
    from app.routers.import_control import router
    from app.database import get_db

    app = FastAPI()
    app.include_router(router)

    async def session():
        async with pg[2]() as db:
            yield db

    app.dependency_overrides[get_db] = session
    return app


@pytest.mark.parametrize(
    "method,path,body",
    [
        ("GET", "/settings", None),
        ("GET", "/departments", None),
        ("GET", "/historical/status", None),
        ("GET", "/workers", None),
        ("GET", "/historical/results", None),
        ("POST", "/historical/start", {"approve": True}),
        ("POST", "/historical/pause", None),
        ("POST", "/historical/resume", None),
        ("GET", "/template-repair/preview", None),
        ("POST", "/template-repair/apply", {}),
        ("POST", "/check", {"date_from": str(DAY), "date_to": str(DAY)}),
        ("PUT", "/settings", {}),
        ("POST", "/departments", {}),
        ("POST", "/issues/1/close", {"reason": "fixture"}),
        ("POST", "/notices/1/retry", {"reason": "fixture"}),
    ],
)
def test_direct_http_cannot_bypass_admin_auth(pg, monkeypatch, method, path, body):
    import httpx

    monkeypatch.setattr(settings, "settings_admin_password_hash", "configured")
    monkeypatch.setattr(
        settings, "settings_admin_session_secret", "synthetic-secret-" * 4
    )

    async def run():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=api_app(pg)), base_url="http://test"
        ) as client:
            response = await client.request(method, "/import-control" + path, json=body)
            assert response.status_code == 401

    asyncio.run(run())


def test_administrative_api_create_edit_check_close_audit_and_activation(
    pg, monkeypatch
):
    import httpx
    from app.services.settings_auth_crypto import create_session
    from app.services.settings_auth import COOKIE_NAME

    secret = "synthetic-secret-" * 4
    monkeypatch.setattr(settings, "settings_admin_password_hash", "configured")
    monkeypatch.setattr(settings, "settings_admin_session_secret", secret)

    async def run():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=api_app(pg)),
            base_url="http://test",
            cookies={COOKIE_NAME: create_session(secret)},
        ) as client:
            prefix = "/import-control"
            config = (await client.get(prefix + "/settings")).json()
            assert (
                not config["enabled"]
                and not config["automatic"]
                and not config["email_enabled"]
            )
            payload = {
                "code": "SYNTH",
                "name": "Fixture",
                "start_date": str(DAY),
                "filename_template": "fixture_{date}.html",
            }
            response = await client.post(prefix + "/departments", json=payload)
            assert response.status_code == 201, response.text
            identity = response.json()["id"]
            assert (
                await client.post(
                    prefix + "/departments", json={**payload, "code": "other"}
                )
            ).status_code == 422
            config.pop("revision")
            config.update(enabled=True, start_date=str(DAY), day_offset=1, approve=True)
            assert (
                await client.put(prefix + "/settings", json=config)
            ).status_code == 200
            assert (
                await client.put(
                    prefix + "/settings", json={**config, "automatic": True}
                )
            ).status_code == 200
            assert (
                await client.post(
                    prefix + f"/cycles/{DAY}/confirm",
                    json={"reason": "Synthetic proof"},
                )
            ).json()["missing"] == 1
            future = date.today() + timedelta(days=3650)
            assert (
                await client.post(
                    prefix + f"/cycles/{future}/confirm",
                    json={"reason": "Synthetic proof"},
                )
            ).status_code == 422
            issues = (await client.get(prefix + "/issues")).json()
            assert issues["total"] == 1
            issue = issues["items"][0]
            assert (
                await client.post(
                    prefix + f'/issues/{issue["id"]}/close',
                    json={"reason": "Approved exemption"},
                )
            ).status_code == 200
            assert (await client.get(prefix + "/issues")).json()["total"] == 0
            assert (await client.get(prefix + "/issues?status=all")).json()[
                "total"
            ] == 1
            edit = {key: value for key, value in payload.items() if key != "code"}
            edit.update(
                name="Renamed fixture",
                start_date=str(DAY + timedelta(days=1)),
                active=False,
            )
            assert (
                await client.post(prefix + f"/departments/{identity}/rules", json=edit)
            ).status_code == 201
            audit = (
                await client.get(prefix + f"/audit?department_id={identity}")
            ).json()
            assert audit["total"] >= 4
            assert all(
                r["actor"] == "settings-admin:shared-session" for r in audit["items"]
            )
            assert (
                await client.post(
                    prefix + "/check",
                    json={
                        "date_from": str(DAY),
                        "date_to": str(DAY + timedelta(days=31)),
                    },
                )
            ).status_code == 422

    asyncio.run(run())


def test_ftp_cycle_waits_all_files_retries_and_marker_without_new_scheduler(
    pg, monkeypatch
):
    from app.services import ftp_autoload as ftp, import_control_worker as worker
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
    from app.services import imports, import_control_worker as worker

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
    from app.services import import_control_worker as worker

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
        if table.name not in {**launcher.IMPORT_CONTROL_CONTRACT, **launcher.IMPORT_HISTORY_CONTRACT}:
            table.to_metadata(old)
    launcher.check_schema(pg[0], old, "20261109_26")


def test_lost_cycle_owner_is_interrupted_without_synthetic_completion(pg, monkeypatch):
    from app.services import import_control_worker as worker

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
        identity, day, _ = await worker.begin_cycle()
        async with pg[2]() as db:
            old = await db.get(ImportCycle, "lost-synthetic-owner")
            assert old.status == "interrupted" and not old.source_confirmed
        await worker.finish_cycle(identity, False)
        async with pg[2]() as db:
            cycle = await db.get(ImportCycle, identity)
            assert cycle.status == "finished" and cycle.source_confirmed

    asyncio.run(run())


def test_later_success_supersedes_old_interrupted_batch_but_new_error_is_visible(pg):
    async def run():
        async with pg[2]() as db:
            await seed(db, 1)
            old = await received(db, 0)
            old.status = "processing"
            await control.record_receipt(db, old)
            await db.commit()
            await received(db, 0)
            await db.commit()
            assert (await control.check_date(db, DAY, confirmed=True))[
                "status"
            ] == "complete"
            newer = await received(db, 0, state="error")
            await db.commit()
            state = await control.check_date(db, DAY, confirmed=True)
            assert state["status"] == "error" and state["errors"] == 1

    asyncio.run(run())


def test_metadata_check_has_bounded_python_memory_and_never_reads_sales(pg):
    import time, tracemalloc

    statements = []

    def sql(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    async def run():
        async with pg[2]() as db:
            await seed(db, 12)
            for i in range(10):
                await received(db, i)
            await db.commit()
            sa.event.listen(pg[1].sync_engine, "before_cursor_execute", sql)
            tracemalloc.start()
            started = time.perf_counter()
            result = await control.check_date(db, DAY, confirmed=True)
            await db.commit()
            elapsed = (time.perf_counter() - started) * 1000
            _, peak = tracemalloc.get_traced_memory()
            tracemalloc.stop()
            assert result["expected"] == 12 and result["missing"] == 2
            assert len(statements) < 80 and peak < 16 * 1024 * 1024
            assert not any(
                "from sales" in s.lower() or "from sale_items" in s.lower()
                for s in statements
            )
            print(
                f"metadata_fixture departments=12 sql_queries={len(statements)} elapsed_ms={elapsed:.3f} peak_traced_bytes={peak}"
            )

    asyncio.run(run())


def test_idle_maintenance_recovers_finished_cycle_after_interrupted_postcheck(
    pg, monkeypatch
):
    from app.services import import_control_worker as worker

    monkeypatch.setattr(worker, "SessionLocal", pg[2])
    monkeypatch.setattr(worker, "engine", pg[1])

    async def run():
        async with pg[2]() as db:
            c = await seed(db, 1)
            c.automatic = True
            db.add(
                ImportCycle(
                    id="finished-no-postcheck",
                    sales_date=DAY,
                    status="finished",
                    source_confirmed=True,
                    finished_at=control.now(),
                )
            )
            await db.commit()
        await worker.maintenance()
        async with pg[2]() as db:
            assert (await db.get(ImportDayCheck, DAY)).status == "incomplete"
            assert (
                await db.scalar(sa.select(sa.func.count()).select_from(ImportIssue))
                == 1
            )

    asyncio.run(run())


def test_real_parser_repeat_and_corrected_source_do_not_duplicate_sales(
    pg, monkeypatch, tmp_path
):
    from app.services import imports, import_control_worker as worker
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
    from app.services import ftp_autoload as ftp, import_control_worker as worker
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


def test_closed_problem_and_empty_recipients_do_not_send(pg, monkeypatch):
    from app.services import import_control_worker as worker

    monkeypatch.setattr(worker, "SessionLocal", pg[2])
    monkeypatch.setattr(worker, "engine", pg[1])
    monkeypatch.setattr(
        worker, "_send", lambda *args: pytest.fail("No recipients, no mail")
    )

    async def run():
        async with pg[2]() as db:
            c = await seed(db, 1)
            c.email_enabled = True
            c.automatic = True
            c.recipients = "fixture@example.invalid"
            db.add(
                ImportCycle(
                    id="synthetic-final",
                    sales_date=DAY,
                    status="finished",
                    source_confirmed=True,
                )
            )
            await control.check_date(db, DAY)
            await db.commit()
            c.recipients = ""
            await db.commit()
        await worker.deliver_one()
        async with pg[2]() as db:
            item = await db.scalar(sa.select(ImportNotice))
            assert item.status == "failed" and item.error_code == "no_recipients"
            issue = await db.scalar(sa.select(ImportIssue))
            issue.status = "closed"
            issue.closure_method = "administrator"
            issue.closure_reason = "Synthetic approval"
            await db.commit()
            c = await control.config(db)
            c.recipients = "fixture@example.invalid"
            await db.commit()
            await control.check_date(db, DAY)
            await db.commit()
            assert (
                await db.scalar(sa.select(sa.func.count()).select_from(ImportNotice))
                == 1
            )

    asyncio.run(run())


def test_overlapping_changed_period_and_legacy_unhashed_source_require_review(
    pg, monkeypatch
):
    from app.services import import_control_worker as worker

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


def test_old_journal_is_reused_without_source_files_or_sales_scan(pg):
    async def run():
        async with pg[2]() as db:
            await seed(db, 1)
            old = ImportBatch(
                filename=f"fixture_0_{DAY:%d.%m.%Y}.html",
                stored_path=None,
                file_size=100,
                status="completed",
                error_rows=0,
                period_start=DAY,
                period_end=DAY,
                added_rows=1,
            )
            db.add(old)
            await db.commit()
            result = await control.check_date(db, DAY, confirmed=True)
            await db.commit()
            assert result["status"] == "complete" and result["successful"] == 1
            receipt = await db.scalar(sa.select(ImportReceipt))
            assert receipt.sha256 is None and receipt.import_id == old.id

    asyncio.run(run())


def test_status_never_reuses_old_complete_result_after_failed_new_cycle(pg):
    from app.routers.import_control import status

    async def run():
        async with pg[2]() as db:
            await seed(db, 1)
            await received(db, 0)
            await control.check_date(db, DAY, confirmed=True)
            await db.commit()
            assert (await status(db))["latest"]["status"] == "complete"
            db.add(
                ImportCycle(
                    id="new-failed-cycle",
                    sales_date=DAY,
                    status="failed",
                    source_confirmed=False,
                    error_code="ftp_listing_failed",
                    finished_at=control.now(),
                )
            )
            await db.commit()
            current = await status(db)
            assert (
                current["latest"]["status"] == "unavailable"
                and current["latest"]["successful"] is None
            )

    asyncio.run(run())


def test_repeated_cancellation_does_not_leak_pooled_session_locks(pg):
    from app.services.import_control_worker import owned_lock

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


def test_admin_closure_requires_a_nonblank_reason():
    from app.routers.import_control import Confirmation
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        Confirmation(reason="      ")
    assert Confirmation(reason="  Approved reason  ").reason == "Approved reason"


def test_email_explains_received_empty_file_instead_of_claiming_it_missing(pg):
    async def run():
        async with pg[2]() as db:
            c = await seed(db, 1)
            c.email_enabled = True
            c.recipients = "fixture@example.invalid"
            batch = await received(db, 0)
            batch.file_size = 0
            await control.record_receipt(db, batch)
            await db.commit()
            state = await control.check_date(db, DAY, confirmed=True)
            await db.commit()
            notice = await db.scalar(sa.select(ImportNotice))
            assert state["missing"] == 0 and state["errors"] == 1
            assert (
                "Нулевой размер файла" in notice.body
                and "Файл получен, но не импортирован" in notice.body
            )

    asyncio.run(run())
