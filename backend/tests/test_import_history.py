"""Opt-in native metadata fixtures only, never production sales/FTP/email."""

import asyncio
import os
from datetime import date, datetime, timezone
from types import SimpleNamespace as Row

import pytest
import sqlalchemy as sa
from fastapi import HTTPException
from app.models.import_control import *
from app.services import import_control as control, import_history as history
from test_import_control import pg, received
import start_backend as launcher


@pytest.mark.parametrize(
    "first",
    [date(2019, 9, 21), date(2000, 11, 11), date(2025, 4, 26), date(2026, 5, 29)],
)
def test_plan_uses_individual_first_rule(first):
    rows = [
        Row(department_id=1, start_date=first),
        Row(department_id=1, start_date=date(2026, 11, 10)),
        Row(department_id=2, start_date=date(2020, 1, 1)),
    ]
    plan = history.make_plan(rows, date(2026, 11, 11), 1)
    assert len(plan) == 1
    assert plan[0]["date_from"] == first.isoformat()
    assert plan[0]["first_rule_date"] == first.isoformat()


@pytest.mark.parametrize(
    "name,expected",
    [
        ("ReestrRN_Bahturova_09.11.2026.html", "ReestrRN_Bahturova_{date}.html"),
        ("ReestrRN_X_31.02.2026.html", None),
        ("fixture_{date}.html", None),
        ("constant.html", None),
    ],
)
def test_repair_proposes_only_valid_dated_literals(name, expected):
    assert (
        history.proposed_template(Row(filename_template=name, date_format="dmy"))
        == expected
    )


async def setup(
    db, first=date(2026, 11, 8), end=None, template="fixture_0_{date}.html"
):
    c = await control.config(db)
    c.enabled = True
    c.automatic = True
    c.email_enabled = True
    c.recipients = "fixture@example.invalid"
    c.start_date = date(2026, 11, 11)
    dep = ImportDepartment(code="SYNTH", name="Synthetic department")
    db.add(dep)
    await db.flush()
    await control.save_rule(
        db, dep, first, end, template, "dmy", dep.name, True, True, "test"
    )
    await db.commit()
    return dep


def test_historical_before_activation_detects_never_received_and_no_mail(pg):
    async def run():
        async with pg[2]() as db:
            dep = await setup(db, template="ReestrRN_Bahturova_{date}.html")
            result = await control.check_date(
                db, date(2026, 11, 9), historical=True, department_id=dep.id
            )
            await db.commit()
            assert result["missing"] == 1
            issue = await db.scalar(sa.select(ImportIssue))
            assert issue.expected_filename == "ReestrRN_Bahturova_09.11.2026.html"
            assert issue.status == "missing"
            assert not await db.scalar(sa.select(ImportNotice.id))
            assert not await db.scalar(sa.select(ImportDayCheck.sales_date))

    asyncio.run(run())


def test_success_error_late_import_idempotency_and_no_daily_pollution(pg):
    async def run():
        async with pg[2]() as db:
            dep = await setup(db)
            await received(db, 0, "error")
            result = await control.check_date(
                db, date(2026, 11, 8), historical=True, department_id=dep.id
            )
            await db.commit()
            assert result["errors"] == 1
            await received(db, 0, "success")
            for _ in range(2):
                result = await control.check_date(
                    db, date(2026, 11, 8), historical=True, department_id=dep.id
                )
                await db.commit()
            assert result["successful"] == 1
            assert (
                await db.scalar(sa.select(sa.func.count()).select_from(ImportIssue))
                == 1
            )
            assert (await db.scalar(sa.select(ImportIssue))).status == "resolved"
            assert (await db.scalar(sa.select(ImportHistoryResult))).status == "success"
            assert not await db.scalar(sa.select(ImportNotice.id))

    asyncio.run(run())


def test_paused_resume_cursor_completed_repeat_and_gap(pg):
    async def run():
        async with pg[2]() as db:
            dep = await setup(db, end=date(2026, 11, 8))
            await history.start(
                db, actor="test", department_id=dep.id, date_to=date(2026, 11, 10)
            )
            await db.commit()
        assert await history.tick(pg[2], batch_size=1) == 1
        async with pg[2]() as db:
            row = await db.get(ImportHistoryJob, 1)
            assert row.next_date == date(2026, 11, 9)
            await history.command(db, "pause", "test")
            await db.commit()
        assert await history.tick(pg[2]) == 0
        async with pg[2]() as db:
            await history.command(db, "resume", "test")
            await db.commit()
        assert await history.tick(pg[2]) == 2
        async with pg[2]() as db:
            row = await db.get(ImportHistoryJob, 1)
            assert (row.status, row.checked, row.total) == ("completed", 3, 3)
            assert (
                await db.scalar(sa.select(sa.func.count()).select_from(ImportIssue))
                == 1
            )
            await history.start(db, actor="test", date_to=date(2026, 11, 10))
            await db.commit()
        assert await history.tick(pg[2]) == 3
        async with pg[2]() as db:
            assert (
                await db.scalar(sa.select(sa.func.count()).select_from(ImportIssue))
                == 1
            )

    asyncio.run(run())


def test_parallel_start_and_worker_lock_skip(pg):
    async def run():
        async with pg[2]() as db:
            await setup(db)
            await history.start(db, actor="test", date_to=date(2026, 11, 8))
            await db.commit()
            with pytest.raises(HTTPException) as e:
                await history.start(db, actor="test")
            assert e.value.status_code == 409
            await db.rollback()
            await control.lock(db)
            assert await history.tick(pg[2]) == 0
            await db.rollback()
        assert await history.tick(pg[2]) == 1

    asyncio.run(run())


def test_rule_change_pauses_then_explicit_recalculation_uses_new_start(pg):
    async def run():
        async with pg[2]() as db:
            dep = await setup(db)
            await history.start(db, actor="test", date_to=date(2026, 11, 10))
            await db.commit()
        assert await history.tick(pg[2], batch_size=1) == 1
        async with pg[2]() as db:
            rule = await db.scalar(sa.select(ImportRule))
            rule.start_date = date(2026, 11, 9)
            await db.commit()
        assert await history.tick(pg[2]) == 0
        async with pg[2]() as db:
            row = await db.get(ImportHistoryJob, 1)
            assert row.error_code == "rules_changed"
            with pytest.raises(HTTPException):
                await history.command(db, "resume", "test")
            await db.rollback()
            await history.start(db, actor="test", date_to=date(2026, 11, 10))
            await db.commit()
        assert await history.tick(pg[2]) == 3
        async with pg[2]() as db:
            result = await db.get(ImportHistoryResult, (date(2026, 11, 8), dep.id))
            assert result.status == "not_expected"
            issue = await db.scalar(
                sa.select(ImportIssue).where(
                    ImportIssue.sales_date == date(2026, 11, 8)
                )
            )
            assert issue.closure_method == "rule_no_longer_applies"

    asyncio.run(run())


def test_preview_approval_preserves_dates_history_and_stale_token(pg):
    async def run():
        async with pg[2]() as db:
            dep = await setup(
                db,
                first=date(2019, 9, 21),
                template="ReestrRN_Bahturova_09.11.2026.html",
            )
            preview = await history.repair_preview(db)
            item = preview["items"][0]
            assert item["start_date"] == date(2019, 9, 21)
            # Reading preview never edits the rule.
            rule = await db.scalar(sa.select(ImportRule))
            assert rule.filename_template == item["before"]
            await history.repair_apply(
                db,
                token=preview["token"],
                ids=[rule.id],
                actor="test",
                reason="approved fixture",
            )
            await db.commit()
            assert (
                rule.start_date == date(2019, 9, 21)
                and rule.filename_template == item["after"]
            )
            assert await db.scalar(
                sa.select(ImportControlAudit.id).where(
                    ImportControlAudit.entity == "rule"
                )
            )
            with pytest.raises(HTTPException):
                await history.repair_apply(
                    db,
                    token=preview["token"],
                    ids=[rule.id],
                    actor="test",
                    reason="fixture",
                )

    asyncio.run(run())


def test_correction_conflict_rolls_back_entire_batch(pg):
    async def run():
        async with pg[2]() as db:
            await setup(db, template="fixture_09.11.2026.html")
            dep = ImportDepartment(code="OTHER", name="Other")
            db.add(dep)
            await db.flush()
            # Doesn't collide with the fixed literal, but would with the proposed wildcard.
            await control.save_rule(
                db,
                dep,
                date(2026, 11, 8),
                None,
                "fixture_08.11.2026.html",
                "dmy",
                dep.name,
                True,
                True,
                "test",
            )
            await db.commit()
            proposal = await history.repair_preview(db)
            ids = [p["rule_id"] for p in proposal["items"]]
            with pytest.raises(sa.exc.IntegrityError):
                await history.repair_apply(
                    db,
                    token=proposal["token"],
                    ids=ids,
                    actor="test",
                    reason="approved fixture",
                )
            await db.rollback()
            assert (
                "{date}"
                not in (
                    await db.scalar(sa.select(ImportRule).order_by(ImportRule.id))
                ).filename_template
            )

    asyncio.run(run())


def test_dated_literal_requires_review_without_false_missing(pg):
    async def run():
        async with pg[2]() as db:
            dep = await setup(db, template="fixture_09.11.2026.html")
            result = await control.check_date(
                db, date(2026, 11, 8), historical=True, department_id=dep.id
            )
            await db.commit()
            assert result["status"] == "unavailable"
            assert not await db.scalar(sa.select(ImportIssue.id))
            assert (
                await db.scalar(sa.select(ImportHistoryResult))
            ).error_code == "historical_template_requires_review"

    asyncio.run(run())


def test_deadline_and_future_daily_independence(pg, monkeypatch):
    async def run():
        monkeypatch.setattr(
            control, "now", lambda: datetime(2026, 11, 10, 21, 19, tzinfo=timezone.utc)
        )
        async with pg[2]() as db:
            dep = await setup(db)
            r = await control.check_date(
                db, date(2026, 11, 10), historical=True, department_id=dep.id
            )
            await db.commit()
            assert r["status"] == "waiting"
            assert not await db.scalar(sa.select(ImportIssue.id))
            monkeypatch.setattr(
                control,
                "now",
                lambda: datetime(2026, 11, 10, 21, 20, tzinfo=timezone.utc),
            )
            r = await control.check_date(
                db, date(2026, 11, 10), historical=True, department_id=dep.id
            )
            await db.commit()
            assert r["status"] == "incomplete"
            r = await control.check_date(db, date(2026, 11, 10))
            assert r["error_code"] == "control_not_enabled"

    asyncio.run(run())


def test_late_hook_rechecks_without_enabled_automatic_control(pg):
    async def run():
        async with pg[2]() as db:
            dep = await setup(db)
            await control.check_date(
                db, date(2026, 11, 8), historical=True, department_id=dep.id
            )
            c = await control.config(db)
            c.enabled = False
            c.automatic = False
            await db.commit()
            await received(db, 0)
            receipt = await db.scalar(sa.select(ImportReceipt))
            identity = receipt.import_id
            await db.commit()
        await history.recheck_receipt(pg[2], identity)
        async with pg[2]() as db:
            assert (await db.scalar(sa.select(ImportIssue))).status == "resolved"

    asyncio.run(run())


def test_cancel_and_no_automatic_creation(pg):
    async def run():
        assert await history.tick(pg[2]) == 0
        async with pg[2]() as db:
            assert not await db.get(ImportHistoryJob, 1)
            await setup(db)
            await history.start(db, actor="test", date_to=date(2026, 11, 9))
            await db.commit()
        stop = asyncio.Event()
        stop.set()
        assert await history.tick(pg[2], stop=stop) == 0
        stop.clear()
        assert await history.tick(pg[2], batch_size=1, stop=stop) == 1
        # New factory/session after interruption resumes persisted next_date.
        assert await history.tick(pg[2], stop=stop) == 1

    asyncio.run(run())


def test_memory_failure_pauses_without_advancing(pg, monkeypatch):
    async def run():
        async with pg[2]() as db:
            await setup(db)
            await history.start(db, actor="test", date_to=date(2026, 11, 9))
            await db.commit()

        def reject():
            raise HTTPException(503, "synthetic memory")

        monkeypatch.setattr(history, "ensure_headroom", reject)
        assert await history.safe_tick(pg[2]) == 0
        async with pg[2]() as db:
            job = await db.get(ImportHistoryJob, 1)
            assert (
                job.status == "paused"
                and job.checked == 0
                and job.next_date == date(2026, 11, 8)
            )

    asyncio.run(run())


@pytest.mark.parametrize("pg", ["20261109_26"], indirect=True)
def test_explicit_migration26_to27_and_bridge_and_fail_closed(pg):
    from app.models import Base

    oldmeta = sa.MetaData()
    for table in Base.metadata.sorted_tables:
        if table.name not in launcher.IMPORT_HISTORY_CONTRACT:
            table.to_metadata(oldmeta)
    launcher.check_schema(pg[0], oldmeta, "20261109_26")
    with pytest.raises(launcher.StartupBlocked):
        launcher.check_schema(pg[0], oldmeta, "20261111_27")
    launcher.migrate(pg[0], "20261111_27", Base.metadata)
    launcher.migrate(pg[0], "20261111_27", Base.metadata)
    launcher.check_schema(pg[0], oldmeta, "20261111_27")
    with pg[0].begin() as conn:
        assert not conn.scalar(sa.text("SELECT count(*) FROM import_history_job"))
        conn.execute(
            sa.text(
                "ALTER TABLE import_history_results DROP CONSTRAINT import_history_results_department_id_fkey"
            )
        )
    with pytest.raises(launcher.StartupBlocked):
        launcher.check_schema(pg[0], oldmeta, "20261111_27")


def test_waiting_historical_day_is_finalized_without_activation(pg, monkeypatch):
    async def run():
        monkeypatch.setattr(
            control, "now", lambda: datetime(2026, 11, 10, 21, 19, tzinfo=timezone.utc)
        )
        async with pg[2]() as db:
            dep = await setup(db)
            await control.check_date(
                db, date(2026, 11, 10), historical=True, department_id=dep.id
            )
            c = await control.config(db)
            c.enabled = False
            c.automatic = False
            await db.commit()
        monkeypatch.setattr(
            control, "now", lambda: datetime(2026, 11, 10, 21, 20, tzinfo=timezone.utc)
        )
        await history.recheck_waiting(pg[2])
        async with pg[2]() as db:
            assert (await db.scalar(sa.select(ImportHistoryResult))).status == "missing"
            assert await db.scalar(sa.select(ImportIssue.id))
            assert not await db.scalar(sa.select(ImportNotice.id))

    asyncio.run(run())


def test_reconciliation_uses_bounded_metadata_not_sales(pg, capsys):
    import time, tracemalloc

    statements = []

    def sql(conn, cursor, statement, params, context, executemany):
        statements.append(statement)

    async def run():
        async with pg[2]() as db:
            await setup(db, first=date(2000, 11, 11))
            job = await history.start(db, actor="test", date_to=date(2026, 11, 10))
            await db.commit()
            assert job.total > 9000
        sa.event.listen(pg[1].sync_engine, "before_cursor_execute", sql)
        tracemalloc.start()
        started = time.perf_counter()
        try:
            assert await history.tick(pg[2], batch_size=25, seconds=10) == 25
            _, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
            sa.event.remove(pg[1].sync_engine, "before_cursor_execute", sql)
        assert not any("FROM sales" in s or "FROM sale_items" in s for s in statements)
        assert peak < 16 * 1024 * 1024
        print(
            f"history_fixture planned_days=9496 batch_days=25 sql_queries={len(statements)} elapsed_ms={(time.perf_counter()-started)*1000:.3f} peak_traced_bytes={peak}"
        )
        async with pg[2]() as db:
            assert (
                await db.scalar(
                    sa.select(sa.func.count()).select_from(ImportHistoryResult)
                )
                == 25
            )

    asyncio.run(run())


def test_historical_full_recheck_refreshes_existing_daily_card_without_mail(pg):
    async def run():
        async with pg[2]() as db:
            await setup(db)
            c = await control.config(db)
            c.start_date = date(2026, 11, 8)
            await control.check_date(db, date(2026, 11, 8), confirmed=True)
            await db.commit()
            assert (
                await db.get(ImportDayCheck, date(2026, 11, 8))
            ).status == "incomplete"
            await received(db, 0)
            await db.commit()
            await control.check_date(db, date(2026, 11, 8), historical=True)
            await db.commit()
            assert (
                await db.get(ImportDayCheck, date(2026, 11, 8))
            ).status == "complete"

    asyncio.run(run())


@pytest.mark.skipif(
    os.environ.get("SCHEMA27_COMPOSE_TESTS") != "1",
    reason="Local Compose config only, opt-in",
)
@pytest.mark.parametrize("kind", ["migrate", "rollback"])
def test_opt_in_overrides_preserve_base_services_without_up(kind):
    import json, subprocess
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    env = {
        **os.environ,
        "POSTGRES_PASSWORD": "synthetic-only",
        "SALES_BACKEND_GID": "10001",
        "SALES_CANDIDATE_IMAGE": "sha256:" + "a" * 64,
        "SALES_ROLLBACK_IMAGE": "sha256:" + "b" * 64,
        "SALES_ROLLBACK_LAUNCHER": "/tmp/synthetic-launcher27.py",
    }
    base = [
        "docker",
        "--host=unix:///var/run/docker.sock",
        "compose",
        "--project-directory",
        str(root),
        "-f",
        str(root / "docker-compose.yml"),
    ]
    original = json.loads(
        subprocess.run(
            base + ["config", "--format", "json"],
            env=env,
            capture_output=True,
            text=True,
            check=True,
        ).stdout
    )
    override = "deploy/" + (
        "migrate.import27.explicit.override.yml"
        if kind == "migrate"
        else "rollback.import27.checked.override.yml"
    )
    result = json.loads(
        subprocess.run(
            base
            + [
                "-f",
                str(root / override),
                "--profile",
                "schema-maintenance",
                "config",
                "--format",
                "json",
            ],
            env=env,
            capture_output=True,
            text=True,
            check=True,
        ).stdout
    )
    assert result["services"]["db"] == original["services"]["db"]
    assert result["services"]["frontend"] == original["services"]["frontend"]
    service = result["services"]["schema-migrate" if kind == "migrate" else "backend"]
    assert service["command"][-1] == "20261111_27"
    assert service["environment"]["ANALYTICS_AUTOMATION_ENABLED"] == "false"
    if kind == "migrate":
        assert service["profiles"] == ["schema-maintenance"]
    else:
        assert (
            "--rollback" in service["command"] and service["volumes"][-1]["read_only"]
        )
