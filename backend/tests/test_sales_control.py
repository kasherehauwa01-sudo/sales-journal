"""Native schema28 in isolated localhost DB, synthetic sales and fake SMTP only."""

import asyncio
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
import smtplib
import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from app.models import Sale, ImportBatch, SmtpConfig
from app.models.sales_control import *
from app.services import sales_control as service
from app.routers.sales_control import NotificationSettings
from test_import_control import pg
import start_backend as launcher


@pytest.fixture
def sales_pg(pg, monkeypatch):
    cfg = Config()
    cfg.set_main_option("script_location", str(Path(__file__).parents[1] / "alembic"))
    with pg[0].begin() as db:
        db.execute(
            sa.text(
                "CREATE INDEX ix_sales_department_date_control ON sales(department,sale_date)"
            )
        )
    command.upgrade(cfg, "20261112_28")
    monkeypatch.setattr(service, "engine", pg[1])
    monkeypatch.setattr(service, "SessionLocal", pg[2])
    monkeypatch.setattr(
        service, "now", lambda: datetime(2026, 11, 12, 7, tzinfo=timezone.utc)
    )
    return pg


async def sale(db, department, day, amount=0):
    batch = await db.get(ImportBatch, 1)
    if not batch:
        db.add(ImportBatch(id=1, filename="synthetic", file_size=1, status="completed"))
        await db.flush()
    import uuid

    row = Sale(
        department=department,
        sale_date=day,
        document_number="synthetic",
        total_amount=amount,
        import_id=1,
        fingerprint=uuid.uuid4().hex,
        social=False,
    )
    db.add(row)
    await db.flush()
    return row.id


async def seed(db):
    await sale(db, "Alpha", date(2026, 11, 1), 0)
    await sale(db, "Alpha", date(2026, 11, 11), 10)
    await sale(db, "Beta", date(2026, 11, 9), -10)
    await db.commit()


def test_actual_sales_first_dates_zero_negative_weekends_and_no_legacy_rules(sales_pg):
    async def run():
        async with sales_pg[2]() as db:
            await seed(db)
            # An old file-rule department without sales must not participate.
            from app.models.import_control import ImportDepartment

            db.add(ImportDepartment(code="OLD", name="No actual sales"))
            await db.commit()
            result = await service.missing(db, date(2026, 11, 8), date(2026, 11, 11))
            assert result["total"] == 5
            pairs = {(x["department"], x["sales_date"]) for x in result["items"]}
            assert ("Alpha", date(2026, 11, 11)) not in pairs
            assert ("Beta", date(2026, 11, 8)) not in pairs
            assert ("Beta", date(2026, 11, 9)) not in pairs
            assert len(pairs) == len(result["items"])
            assert result["items"][0]["sales_date"] == date(2026, 11, 11)
            assert [d["department"] for d in await service.departments(db)] == [
                "Alpha",
                "Beta",
            ]

    asyncio.run(run())


def test_exclusions_restore_late_sales_deletion_and_audit_without_mutating_sales(
    sales_pg,
):
    async def run():
        async with sales_pg[2]() as db:
            await seed(db)
            pairs = [("Beta", date(2026, 11, 11))]
            await service.exclusions(db, pairs, True, "synthetic-admin")
            assert (await service.missing(db, *[date(2026, 11, 11)] * 2))["total"] == 0
            assert (await service.missing(db, *[date(2026, 11, 11)] * 2, hidden=True))[
                "total"
            ] == 1
            identity = await sale(db, "Beta", date(2026, 11, 11))
            await db.commit()
            assert (await service.missing(db, *[date(2026, 11, 11)] * 2, hidden=True))[
                "total"
            ] == 0
            await db.execute(sa.delete(Sale).where(Sale.id == identity))
            await db.commit()
            assert (await service.missing(db, *[date(2026, 11, 11)] * 2))["total"] == 0
            await service.exclusions(db, pairs, False, "synthetic-admin")
            assert (await service.missing(db, *[date(2026, 11, 11)] * 2))["total"] == 1
            assert await db.scalar(sa.select(sa.func.count()).select_from(Sale)) == 3
            assert (
                await db.scalar(
                    sa.select(sa.func.count()).select_from(SalesControlAudit)
                )
                == 2
            )

    asyncio.run(run())


def test_range_search_sort_pagination_and_month_year_boundaries(sales_pg):
    async def run():
        async with sales_pg[2]() as db:
            await sale(db, "Alpha", date(2025, 12, 30))
            await sale(db, "Beta", date(2026, 1, 2))
            await db.commit()
            first = await service.missing(
                db, date(2025, 12, 31), date(2026, 3, 31), page=1
            )
            second = await service.missing(
                db, date(2025, 12, 31), date(2026, 3, 31), page=2
            )
            assert len(first["items"]) == len(second["items"]) == 50
            assert not {(x["department"], x["sales_date"]) for x in first["items"]} & {
                (x["department"], x["sales_date"]) for x in second["items"]
            }
            assert (
                await service.missing(
                    db, date(2025, 12, 31), date(2026, 1, 2), search="01.01.2026"
                )
            )["total"] == 1
            assert (
                await service.missing(
                    db, date(2025, 12, 31), date(2026, 1, 2), search="ALPha"
                )
            )["total"] == 3
            for start, end in [
                (date(2026, 11, 12), date(2026, 11, 12)),
                (date(2026, 2, 2), date(2026, 1, 1)),
                (date(2020, 1, 1), date(2026, 1, 1)),
            ]:
                with pytest.raises(Exception) as error:
                    await service.missing(db, start, end)
                assert error.value.status_code == 422

    asyncio.run(run())


async def configure(db):
    row = await service.settings_row(db)
    row.enabled = True
    row.recipients = ["staff@example.invalid"]
    row.send_time = "09:00"
    row.not_before = datetime(2026, 11, 1, tzinfo=timezone.utc)
    db.add(
        SmtpConfig(
            id=1,
            host="smtp.invalid",
            port=25,
            security="NONE",
            username="",
            password="SECRET_SENTINEL",
            sender_email="sender@example.invalid",
            sender_name="Journal",
        )
    )
    await db.commit()


@pytest.mark.parametrize(
    "outcome",
    ["sent", "temporary", "uncertain", "all_present", "hidden", "disabled", "no_smtp"],
)
def test_notification_once_restart_no_false_full_and_smtp_policy(
    sales_pg, monkeypatch, outcome
):
    calls = []

    def send(*args):
        calls.append(args)
        if outcome == "temporary":
            raise smtplib.SMTPDataError(451, b"PRIVATE_SENTINEL")
        if outcome == "uncertain":
            raise smtplib.SMTPServerDisconnected("PRIVATE_SENTINEL")
        return {}

    monkeypatch.setattr(service, "send_message", send)

    async def run():
        async with sales_pg[2]() as db:
            await seed(db)
            await configure(db)
            if outcome == "all_present":
                await sale(db, "Beta", date(2026, 11, 11))
                await db.commit()
            if outcome == "hidden":
                await service.exclusions(
                    db, [("Beta", date(2026, 11, 11))], True, "admin"
                )
            if outcome == "disabled":
                row = await service.settings_row(db)
                row.enabled = False
                await db.commit()
            if outcome == "no_smtp":
                await db.execute(sa.delete(SmtpConfig))
                await db.commit()
        await service.notification_tick()
        await service.notification_tick()
        async with sales_pg[2]() as db:
            rows = (await db.scalars(sa.select(SalesControlDelivery))).all()
            if outcome == "disabled":
                assert rows == [] and calls == []
                return
            assert len(rows) == 1
            row = rows[0]
            expected = {
                "all_present": "not_required",
                "hidden": "not_required",
                "no_smtp": "failed",
                "temporary": "failed",
            }.get(outcome, outcome)
            assert row.status == expected
            assert len(calls) == (
                0 if outcome in ("all_present", "hidden", "no_smtp") else 1
            )
            if calls:
                assert calls[0][-1] == ["Beta"]
            assert await db.scalar(sa.select(sa.func.count()).select_from(Sale)) >= 3

    asyncio.run(run())


def test_save_no_immediate_check_validates_recipients_catchup_and_concurrency(
    sales_pg, monkeypatch
):
    async def run():
        async with sales_pg[2]() as db:
            await seed(db)
            payload = NotificationSettings(
                enabled=True,
                send_time="09:00",
                recipients=["A@example.invalid", "a@example.invalid"],
                revision=1,
            )
            result = await service.save_settings(db, payload, "admin")
            assert len(result["recipients"]) == 1
            row = await service.settings_row(db)
            assert row.not_before.astimezone(service.MOSCOW).date() == date(
                2026, 11, 13
            )
            assert not await db.scalar(sa.select(SalesControlDelivery.id))
        await service.notification_tick()
        async with sales_pg[2]() as db:
            assert not await db.scalar(sa.select(SalesControlDelivery.id))
        # Restart after next morning's09:00 catches exactly latest due day, not all history.
        monkeypatch.setattr(
            service, "now", lambda: datetime(2026, 11, 15, 7, tzinfo=timezone.utc)
        )
        await service.notification_tick()
        async with sales_pg[2]() as db:
            rows = (await db.scalars(sa.select(SalesControlDelivery))).all()
            assert len(rows) == 1 and rows[0].sales_date == date(2026, 11, 14)
            with pytest.raises(Exception) as e:
                await service.save_settings(db, payload, "other-admin")
            assert e.value.status_code == 409

    asyncio.run(run())


def test_native_launcher28_and_bridge27_models_and_constraint_drift(sales_pg):
    launcher.check_schema(
        sales_pg[0],
        __import__("app.models", fromlist=["Base"]).Base.metadata,
        "20261112_28",
    )
    # Frozen contract works without any new ORM models in the old image.
    old = sa.MetaData()
    for table in __import__(
        "app.models", fromlist=["Base"]
    ).Base.metadata.sorted_tables:
        if table.name not in launcher.SALES_CONTROL_CONTRACT:
            table.to_metadata(old)
    launcher.check_schema(sales_pg[0], old, "20261112_28")
    with sales_pg[0].begin() as db:
        db.execute(
            sa.text(
                "ALTER TABLE sales_control_deliveries DROP CONSTRAINT uq_sales_control_delivery"
            )
        )
    with pytest.raises(launcher.StartupBlocked):
        launcher.check_schema(sales_pg[0], old, "20261112_28")


def test_api_auth_retired_paths_and_confirmations(sales_pg, monkeypatch):
    import httpx
    from fastapi import FastAPI
    from app.routers.sales_control import router
    from app.database import get_db
    from app.services.settings_auth import require_settings_admin
    from app.config import settings

    monkeypatch.setattr(settings, "settings_admin_password_hash", "configured")
    monkeypatch.setattr(settings, "settings_admin_session_secret", "fixture-" * 8)

    async def run():
        app = FastAPI()
        app.include_router(router)

        async def session():
            async with sales_pg[2]() as db:
                yield db

        app.dependency_overrides[get_db] = session
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            assert (await client.get("/sales-control/missing")).status_code == 401
            assert (await client.get("/import-control/status")).status_code == 404
            app.dependency_overrides[require_settings_admin] = lambda: None
            assert (await client.get("/sales-control/missing")).status_code == 200
            assert (
                await client.post(
                    "/sales-control/exclusions",
                    json={
                        "items": [{"department": "Alpha", "sales_date": "2026-11-11"}]
                    },
                )
            ).status_code == 422

    asyncio.run(run())


def test_concurrent_ticks_settings_lock_and_interrupted_sender(sales_pg, monkeypatch):
    import threading

    entered, release = threading.Event(), threading.Event()
    calls = []

    def sender(*args):
        calls.append(1)
        entered.set()
        assert release.wait(5)
        return {}

    monkeypatch.setattr(service, "send_message", sender)

    async def run():
        async with sales_pg[2]() as db:
            await seed(db)
            await configure(db)
        first = asyncio.create_task(service.notification_tick())
        try:
            for _ in range(100):
                if entered.is_set():
                    break
                await asyncio.sleep(0.01)
            assert entered.is_set()
            await service.notification_tick()
            async with sales_pg[2]() as db:
                with pytest.raises(Exception) as caught:
                    await service.mutation_lock(db)
                assert caught.value.status_code == 409
                await db.rollback()
            assert calls == [1]
        finally:
            release.set()
            await first
        async with sales_pg[2]() as db:
            row = await db.scalar(sa.select(SalesControlDelivery))
            assert row.status == "sent"
            row.status = "sending"
            await db.commit()
        await service.notification_tick()
        async with sales_pg[2]() as db:
            row = await db.scalar(sa.select(SalesControlDelivery))
            assert row.status == "uncertain" and calls == [1]

    asyncio.run(run())


def test_mail_html_escape_and_plain_alternative(monkeypatch):
    captured = []

    class SMTP:
        def __init__(self, *a, **k):
            assert k["timeout"] == 30

        def send_message(self, message):
            captured.append(message)
            return {}

        def quit(self):
            pass

    monkeypatch.setattr(service.smtplib, "SMTP", SMTP)
    cfg = SimpleNamespace(
        host="fixture",
        port=25,
        security="NONE",
        username="",
        sender_name="Sales",
        sender_email="sales@example.test",
    )
    service.send_message(
        cfg, ["admin@example.test"], date(2026, 11, 11), ["<script> & Store"]
    )
    msg = captured[0]
    assert "<script>" not in msg.get_body(preferencelist=("html",)).get_content()
    assert (
        "&lt;script&gt; &amp; Store"
        in msg.get_body(preferencelist=("html",)).get_content()
    )
    assert "<script> & Store" in msg.get_body(preferencelist=("plain",)).get_content()


def test_explicit_index_prepare_bounded_session_and_idempotence(sales_pg, monkeypatch):
    import app.sales_control_index as cli
    import sys

    statements = []
    with sales_pg[0].begin() as db:
        db.execute(sa.text("DROP INDEX ix_sales_department_date_control"))
    sa.event.listen(
        sales_pg[0],
        "before_cursor_execute",
        lambda c, cur, sql, p, ctx, m: statements.append(sql),
    )
    monkeypatch.setattr(cli, "sync_engine", lambda settings: sales_pg[0])
    checked = []
    monkeypatch.setattr(cli, "ensure_headroom", lambda: checked.append(True))
    monkeypatch.setattr(sys, "argv", ["index", "--create"])
    cli.main()
    cli.main()
    assert checked == [True]
    assert statements.count("SET maintenance_work_mem='16MB'") == 1
    assert statements.count("SET max_parallel_maintenance_workers=0") == 1
    assert sum(x.startswith("CREATE INDEX CONCURRENTLY") for x in statements) == 1
    assert not any(
        x.startswith(("ALTER SYSTEM", "DROP TABLE", "TRUNCATE")) for x in statements
    )


def test_explicit_index_refuses_active_background_lock(sales_pg, monkeypatch):
    import app.sales_control_index as cli
    import sys

    monkeypatch.setattr(cli, "sync_engine", lambda settings: sales_pg[0])
    monkeypatch.setattr(sys, "argv", ["index", "--create"])
    monkeypatch.setattr(
        cli, "ensure_headroom", lambda: pytest.fail("Must refuse before build")
    )
    with sales_pg[0].connect() as owner:
        owner.execute(sa.text("SELECT pg_advisory_lock(734190221)"))
        owner.commit()
        with pytest.raises(launcher.StartupBlocked, match="Background/schema work"):
            cli.main()
        owner.execute(sa.text("SELECT pg_advisory_unlock(734190221)"))
        owner.commit()
