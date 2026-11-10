from datetime import date, datetime, timezone
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from app.services.import_deadline import deadline, cycle_sales_day, due_sales_day
from app.services import import_control as control


@pytest.mark.parametrize(
    "at,day",
    [
        ("2026-11-09T23:39:59+03:00", "2026-11-08"),
        ("2026-11-09T23:40:00+03:00", "2026-11-09"),
        ("2026-11-10T00:19:59+03:00", "2026-11-09"),
        ("2026-11-10T00:20:00+03:00", "2026-11-09"),
        ("2026-11-09T21:20:00+00:00", "2026-11-09"),
    ],
)
def test_cycle_date_across_midnight(at, day):
    assert cycle_sales_day(datetime.fromisoformat(at)) == date.fromisoformat(day)


@pytest.mark.parametrize(
    "at,day",
    [
        ("2026-11-10T00:19:59+03:00", "2026-11-08"),
        ("2026-11-10T00:20:00+03:00", "2026-11-09"),
        ("2027-01-01T00:20:00+03:00", "2026-12-31"),
    ],
)
def test_due_day_and_year_boundary(at, day):
    assert due_sales_day(datetime.fromisoformat(at)) == date.fromisoformat(day)
    assert deadline(date.fromisoformat(day)).tzinfo.key == "Europe/Moscow"


@pytest.mark.parametrize(
    "at,expected",
    [
        ("2026-11-10T00:19:59+03:00", "waiting"),
        ("2026-11-10T00:20:00+03:00", "incomplete"),
    ],
)
def test_even_manual_confirmation_cannot_raise_missing_alarm_early(
    monkeypatch, at, expected
):
    day = date(2026, 11, 9)
    r = SimpleNamespace(
        id=1,
        department_id=1,
        start_date=day,
        end_date=None,
        filename_template="fixture_{date}.html",
        date_format="dmy",
        display_name="Fixture",
        active=True,
        controlled=True,
    )
    cycle = SimpleNamespace(
        status="finished", source_confirmed=True, started_at=deadline(day)
    )

    class Rows:
        def __init__(self, rows):
            self.rows = rows

        def all(self):
            return self.rows

    db = SimpleNamespace(
        execute=AsyncMock(return_value=Rows([])),
        scalars=AsyncMock(side_effect=[Rows([r]), Rows([]), Rows([]), Rows([cycle])]),
        scalar=AsyncMock(return_value=None),
        get=AsyncMock(return_value=None),
        flush=AsyncMock(),
        add=lambda row: None,
    )
    monkeypatch.setattr(control, "lock", AsyncMock())
    monkeypatch.setattr(
        control,
        "config",
        AsyncMock(
            return_value=SimpleNamespace(
                enabled=True,
                start_date=day,
                revision=1,
                email_enabled=False,
                recipients="",
            )
        ),
    )
    monkeypatch.setattr(control, "now", lambda: datetime.fromisoformat(at))
    result = asyncio.run(control.check_date(db, day, confirmed=True))
    assert result["status"] == expected


from test_import_control import pg, seed
from app.models.import_control import ImportCycle, ImportNotice, ImportDayCheck
from sqlalchemy import select, func


def test_only_post_deadline_ftp_listing_can_finalize_without_marker(pg, monkeypatch):
    from app.services import import_control_worker as worker

    day = date(2026, 11, 9)
    after = deadline(day)
    monkeypatch.setattr(worker, "SessionLocal", pg[2])
    monkeypatch.setattr(worker, "now", lambda: after)
    monkeypatch.setattr(control, "now", lambda: after)

    async def run():
        async with pg[2]() as db:
            c = await seed(db, 1)
            c.automatic = True
            c.email_enabled = True
            c.recipients = "fixture@example.invalid"
            db.add(
                ImportCycle(
                    id="before-deadline",
                    sales_date=day,
                    status="running",
                    source_confirmed=False,
                    started_at=datetime(2026, 11, 9, 23, 55, tzinfo=after.tzinfo),
                )
            )
            await db.commit()
        # A pre-deadline listing does not prove that no more files arrived by00:20.
        await worker.finish_cycle("before-deadline", True)
        async with pg[2]() as db:
            assert (await db.get(ImportDayCheck, day)).status == "waiting"
            assert await db.scalar(select(func.count()).select_from(ImportNotice)) == 0
            db.add(
                ImportCycle(
                    id="deadline-pass",
                    sales_date=day,
                    status="running",
                    source_confirmed=False,
                    started_at=after,
                )
            )
            await db.commit()
        # No marker is present or required.
        await worker.finish_cycle("deadline-pass", False)
        async with pg[2]() as db:
            assert (await db.get(ImportDayCheck, day)).status == "incomplete"
            assert await db.scalar(select(func.count()).select_from(ImportNotice)) == 1

    asyncio.run(run())


def test_deadline_catchup_is_durable_and_does_not_repeat_ftp(pg, monkeypatch):
    from app.services import import_control_worker as worker, ftp_autoload as ftp
    from app.models import FtpConfig

    for module in (worker, ftp):
        monkeypatch.setattr(module, "SessionLocal", pg[2])
        monkeypatch.setattr(module, "engine", pg[1])
    calls = []

    async def imported(identity, day, unused):
        calls.append(day)
        await worker.finish_cycle(identity, False)

    monkeypatch.setattr(ftp, "_run_autoload_cycle", imported)

    async def run():
        async with pg[2]() as db:
            c = await seed(db, 1)
            c.automatic = True
            db.add(
                FtpConfig(
                    id=1,
                    enabled=True,
                    host="fixture.invalid",
                    port=21,
                    protocol="FTP",
                    username="fixture",
                    password="fixture",
                    directory="/",
                )
            )
            await db.commit()
        await worker.check_deadline_cycle()
        await worker.check_deadline_cycle()
        assert calls == [due_sales_day(control.now())]
        async with pg[2]() as db:
            cycle = await db.scalar(select(ImportCycle))
            assert cycle.source_confirmed

    asyncio.run(run())
