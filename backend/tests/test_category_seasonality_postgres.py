"""Native schema29 only in a fixture-owned localhost schema; fake ClientsVR."""

import asyncio, calendar, json, os, time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock
import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config
from app.models import (
    Base,
    Sale,
    SaleItem,
    ImportBatch,
    CatalogProduct,
    CatalogLookup,
    CategorySeasonSnapshot as Snapshot,
    CategorySeasonState as State,
    CategorySeasonFact as Fact,
)
from app.services.catalog_attributes import identity
from app.services import category_seasonality_snapshot as service
from app.routers.category_seasonality_report import Request
from test_sales_control import sales_pg
from test_import_control import pg
import start_backend as launcher

CLOCK = datetime(2026, 11, 13, 12, tzinfo=timezone.utc)


@pytest.fixture
def snapshot_pg(sales_pg, monkeypatch):
    cfg = Config()
    cfg.set_main_option("script_location", str(Path(__file__).parents[1] / "alembic"))
    command.upgrade(cfg, "20261114_29")
    monkeypatch.setattr(
        service.clients_vr,
        "get_managers",
        AsyncMock(return_value=["M", "Нет менеджера"]),
    )
    monkeypatch.setattr(
        service.clients_vr,
        "get_buyer_types",
        AsyncMock(return_value=["Опт", "Розница"]),
    )

    async def membership(kind, value, db=None):
        return ["Клиент A"] if value in ["M", "Опт"] else ["Клиент B"]

    monkeypatch.setattr(service.clients_vr, "get_snapshot_filter_clients", membership)
    return sales_pg


async def seed(db):
    db.add(
        ImportBatch(
            id=300, filename="synthetic", file_size=1, status="completed", error_rows=0
        )
    )
    await db.flush()
    now = datetime.now(timezone.utc)
    for n, (parent, section, brand, maker) in enumerate(
        [
            (1, "Апрель", "A", "X"),
            (1, "Июль", "B", "Y"),
            (2, "Апрель", "A", "Y"),
            (None, "Неизвестно", "A", "X"),
        ],
        1,
    ):
        db.add(
            CatalogProduct(
                product_id=n,
                code=f"00{n}",
                article="дубль",
                category_id=parent,
                category="Посуда" if parent == 1 else "Декор" if parent else None,
                subcategory=section,
                legacy_category=section,
                brand=brand,
                manufacturer=maker,
                horeca=False,
                source_updated_at=now,
                synced_at=now,
            )
        )
        await db.flush()
        code = f"00{n}" if n != 4 else "\u00a0Straße\u00a0"
        db.add(
            CatalogLookup(
                identity=identity(code, "ДУБЛЬ"),
                code=code.strip().casefold(),
                article="дубль",
                product_id=n,
                status="matched",
                matched_by="code",
            )
        )
        for y in [2024, 2025, 2026]:
            for m in range(9 if y == 2024 else 1, 12 if y == 2026 else 13):
                # Current November 2026 must never enter the snapshot.
                days = calendar.monthrange(y, m)[1]
                allowed = range(
                    1,
                    (
                        14
                        if (y, m) == (2025, 4)
                        else (
                            18
                            if (y, m) == (2026, 3)
                            else 1 if (y, m) == (2026, 4) else days
                        )
                    )
                    + 1,
                )
                for d in allowed:
                    s = Sale(
                        sale_date=date(y, m, d),
                        document_number=f"{n}-{y}-{m}-{d}",
                        department="Store",
                        client=" Клиент A " if n % 2 else "Клиент B",
                        total_amount=10,
                        fingerprint=f"{n}-{y}-{m}-{d}",
                        import_id=300,
                    )
                    db.add(s)
                    await db.flush()
                    db.add(
                        SaleItem(
                            sale_id=s.id,
                            code=code,
                            article="ДУБЛЬ",
                            name="Private SKU",
                            quantity=5 if m == (7 if n == 2 else 4) else 1,
                            actual_price=2,
                        )
                    )
    await db.commit()


async def read(factory, params, actual=False):
    async with factory() as db:
        await service.settings_sql(db)
        return await service.read_report(db, params, include_actual=actual)


def test_snapshot_filters_partial_months_raw_read_prohibition_and_atomicity(
    snapshot_pg, monkeypatch
):
    engine_sync, engine, factory, _ = snapshot_pg

    async def run():
        assert (await service.rebuild(engine, clock=CLOCK))["status"] == "uninitialized"
        async with factory() as db:
            await seed(db)
        result = await service.rebuild(engine, manual=True, clock=CLOCK)
        assert result["status"] == "published"
        sid = result["id"]
        base = await read(factory, Request())
        assert base["snapshot_id"] == sid and len(base["rows"]) == 4
        row = next(
            r for r in base["rows"] if r["name"] == "Апрель" and r["parent"] == "Посуда"
        )
        assert (
            2024 in row["units"]["cells"][8]["years"]
            and 2026 in row["units"]["cells"][0]["years"]
        )
        assert (
            2026 not in row["units"]["cells"][2]["years"]
            and 2026 not in row["units"]["cells"][3]["years"]
        )
        assert row["units"]["actual"] == []
        overlap = await read(factory, Request(categories=["category:1", row["key"]]))
        assert len(overlap["rows"]) == 2
        assert overlap["rows"][0]["units"]["cells"] == row["units"]["cells"]
        filtered = await read(
            factory,
            Request(
                brands=["A", "B"],
                manufacturers=["Y"],
                manager=["Нет менеджера"],
                buyer_type=["Розница"],
            ),
        )
        assert [r["name"] for r in filtered["rows"]] == ["Июль"]
        assert (await read(factory, Request(brands=["B"], buyer_type=["Опт"])))[
            "rows"
        ] == []
        # Actual snapshot sums match direct source SQL for exact joint filters.
        detail = await read(
            factory, Request(categories=[row["key"]], snapshot_id=sid), True
        )
        actual = detail["rows"][0]["units"]["actual"]
        assert any(
            a["month"] == 4 and a["year"] == 2026 and not a["included"] for a in actual
        )
        async with factory() as db:
            direct = await db.scalar(
                sa.select(sa.func.sum(SaleItem.quantity))
                .join(Sale)
                .where(Sale.sale_date < date(2026, 11, 1), SaleItem.code == "001")
            )
            assert sum(a["value"] for a in actual) == float(direct)

        # Independently aggregate raw rows for the exact joint selection, then
        # compare both metric profiles, extrema and missing-month cells.
        from app.services.category_seasonality import seasonal_profile, coverage_method

        params = Request(
            years=[2025, 2026],
            stores=["Store"],
            categories=["category:1"],
            brands=["A", "B"],
            manufacturers=["Y"],
            manager=["Нет менеджера"],
            buyer_type=["Розница"],
        )
        selected = await read(factory, params, True)
        async with factory() as db:
            raw = (
                await db.execute(
                    sa.text(
                        "SELECT extract(year from s.sale_date)::int y,extract(month from s.sale_date)::int m,sum(i.quantity) units,sum(i.quantity*i.actual_price) revenue,count(*) n FROM sale_items i JOIN sales s ON s.id=i.sale_id WHERE i.code='002' AND s.department='Store' AND lower(trim(s.client))='клиент b' AND s.sale_date>='2025-01-01' AND s.sale_date<'2026-11-01' GROUP BY y,m"
                    )
                )
            ).all()
            days = (
                await db.execute(
                    sa.text(
                        "SELECT extract(year from sale_date)::int y,extract(month from sale_date)::int m,count(DISTINCT sale_date) n FROM sales WHERE department='Store' AND sale_date>='2025-01-01' AND sale_date<'2026-11-01' GROUP BY y,m"
                    )
                )
            ).all()
        cov = {
            ("Store", y, m): coverage_method(n, calendar.monthrange(y, m)[1])
            for y, m, n in days
        }
        for metric in ["units", "revenue"]:
            expected = seasonal_profile(
                {("Store", r.y, r.m): (getattr(r, metric), int(r.n)) for r in raw},
                cov,
                [2025, 2026],
            )
            actual_profile = selected["rows"][0][metric]
            assert (
                actual_profile["cells"] == expected["cells"]
                and actual_profile["peaks"] == expected["peaks"]
                and actual_profile["troughs"] == expected["troughs"]
            )
            assert actual_profile["actual"] == expected["actual"]

        # Query listener rejects any fallback to sales/items/catalog/external APIs.
        def no_raw(conn, cursor, statement, parameters, context, many):
            lower = statement.lower()
            assert not any(
                f" {t} " in lower or f" {t}\n" in lower
                for t in ["sales", "sale_items", "catalog_products", "catalog_lookups"]
            )

        sa.event.listen(engine.sync_engine, "before_cursor_execute", no_raw)
        try:
            assert (await read(factory, Request(brands=["A"])))["state"] == "ready"
            assert (
                await read(
                    factory, Request(categories=[row["key"]], snapshot_id=sid), True
                )
            )["rows"]
        finally:
            sa.event.remove(engine.sync_engine, "before_cursor_execute", no_raw)
        # No automatic run before the scheduled Saturday19:00, including restart.
        assert (await service.rebuild(engine, clock=CLOCK))["status"] == "current"
        original = service.materialize

        async def fail(db, sid, end):
            await db.execute(
                sa.text(
                    "INSERT INTO category_season_categories(snapshot_id,id,key,parent_key,parent,name) VALUES(:sid,999,'partial','p','p','partial')"
                ),
                {"sid": sid},
            )
            raise RuntimeError("PRIVATE_PAYLOAD")

        monkeypatch.setattr(service, "materialize", fail)
        due = datetime(2026, 11, 14, 16, tzinfo=timezone.utc)
        with pytest.raises(RuntimeError):
            await service.rebuild(engine, clock=due)
        assert (await read(factory, Request()))["snapshot_id"] == sid
        assert (await read(factory, Request()))["previous_after_error"]
        async with factory() as db:
            assert (
                await db.scalar(
                    sa.text(
                        "SELECT count(*) FROM category_season_categories WHERE id=999"
                    )
                )
                == 0
            )
        monkeypatch.setattr(service, "materialize", original)
        assert (await service.rebuild(engine, clock=due))["status"] == "current"
        # Next scheduled week refreshes; repeated replicas cannot build twice.
        later = due + timedelta(days=7)
        pair = await asyncio.gather(
            service.rebuild(engine, clock=later), service.rebuild(engine, clock=later)
        )
        assert sum(r["status"] == "published" for r in pair) == 1
        assert (await service.rebuild(engine, clock=later))["status"] == "current"

    asyncio.run(run())
    launcher.check_schema(engine_sync, Base.metadata, "20261114_29")


def test_migration_launcher29_bridge_and_missing_constraint(snapshot_pg):
    sync, _, _, _ = snapshot_pg
    launcher.check_schema(sync, Base.metadata, "20261114_29")
    # Frozen contract must work with an old image's metadata too.
    launcher.check_schema(sync, sa.MetaData(), "20261114_29")
    with sync.begin() as db:
        db.execute(
            sa.text(
                "ALTER TABLE category_season_facts DROP CONSTRAINT ck_cs_fact_month_lines"
            )
        )
    with pytest.raises(launcher.StartupBlocked):
        launcher.check_schema(sync, sa.MetaData(), "20261114_29")


@pytest.mark.skipif(
    os.environ.get("CATEGORY_SEASON_BENCHMARK") != "1",
    reason="Explicit local synthetic benchmark only",
)
def test_snapshot_benchmark(snapshot_pg, monkeypatch):
    from benchmarks.benchmark_category_seasonality import run
    import subprocess

    _, engine, factory, _ = snapshot_pg
    monkeypatch.setattr(
        service.clients_vr, "get_managers", AsyncMock(return_value=["M"])
    )
    monkeypatch.setattr(
        service.clients_vr, "get_buyer_types", AsyncMock(return_value=["Опт"])
    )
    monkeypatch.setattr(
        service.clients_vr,
        "get_snapshot_filter_clients",
        AsyncMock(return_value=["Клиент 1", "Клиент 2"]),
    )
    reset = None
    if os.environ.get("CATEGORY_SEASON_COLD_RESTART_CONTAINER"):
        # Explicitly restricted to this owned local fixture, never a server URL.
        assert (
            os.environ["CATEGORY_SEASON_COLD_RESTART_CONTAINER"]
            == "sales-control-benchmark-test"
        )
        assert engine.url.host == "127.0.0.1" and engine.url.port == 18539

        async def reset():
            await asyncio.to_thread(
                subprocess.run,
                ["docker", "restart", "sales-control-benchmark-test"],
                check=True,
                stdout=subprocess.DEVNULL,
            )
            for _ in range(30):
                try:
                    async with engine.connect() as db:
                        await db.execute(sa.text("SELECT 1"))
                    return
                except Exception:
                    await asyncio.sleep(0.2)
            raise RuntimeError("Local fixture did not restart")

    result = asyncio.run(run(engine, factory, reset))
    assert result["build_result"]["rows"] > 0 and len(result["reads"]) == 4


def test_cancelled_and_interrupted_build_never_publish_partial_data(
    snapshot_pg, monkeypatch
):
    _, engine, factory, _ = snapshot_pg

    async def run():
        async with factory() as db:
            await seed(db)
        first = await service.rebuild(engine, manual=True, clock=CLOCK)
        entered = asyncio.Event()

        async def blocked(db, sid, end):
            # Uncommitted data must roll back even after a repeated cancellation.
            await db.execute(
                sa.text(
                    "INSERT INTO category_season_categories(snapshot_id,id,key,parent_key,parent,name) VALUES(:sid,999,'x','p','p','x')"
                ),
                {"sid": sid},
            )
            entered.set()
            await asyncio.Event().wait()

        monkeypatch.setattr(service, "materialize", blocked)
        task = asyncio.create_task(
            service.rebuild(
                engine, clock=datetime(2026, 11, 14, 16, tzinfo=timezone.utc)
            )
        )
        await entered.wait()
        # Fast reads remain available while the worker holds heavy/source locks.
        assert (await read(factory, Request()))["snapshot_id"] == first["id"]
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        async with factory() as db:
            assert (
                await db.scalar(
                    sa.text(
                        "SELECT count(*) FROM category_season_categories WHERE id=999"
                    )
                )
                == 0
            )
            assert (
                await db.scalar(sa.select(Snapshot).where(Snapshot.status == "failed"))
            ).error_code == "cancelled"
            dead = Snapshot(
                id="interrupted-fixture",
                status="building",
                week=None,
                started_at=CLOCK,
                through=date(2026, 10, 31),
                options={},
                fact_rows=0,
            )
            db.add(dead)
            await db.commit()
        assert (await service.rebuild(engine, clock=CLOCK))["status"] == "current"
        async with factory() as db:
            assert (
                await db.get(Snapshot, "interrupted-fixture")
            ).error_code == "worker_interrupted"

    asyncio.run(run())


def test_explicit_28_to_29_migration_idempotency_and_bad_source(sales_pg):
    sync, _, factory, _ = sales_pg
    launcher.check_schema(sync, Base.metadata, "20261112_28")
    assert "category_season_facts" not in sa.inspect(sync).get_table_names()
    launcher.migrate(sync, "20261114_29", Base.metadata)
    launcher.migrate(sync, "20261114_29", Base.metadata)
    with sync.connect() as db:
        assert (
            db.scalar(sa.text("SELECT active_id FROM category_season_state WHERE id=1"))
            is None
        )
        assert db.scalar(sa.text("SELECT count(*) FROM category_season_facts")) == 0
    with sync.begin() as db:
        db.execute(sa.text("UPDATE alembic_version SET version_num='20261111_27'"))
    with pytest.raises(
        launcher.StartupBlocked, match="Unreviewed migration transition"
    ):
        launcher.migrate(sync, "20261114_29", Base.metadata)


def test_http_fast_reads_share_snapshot_and_reject_invalid_detail(
    snapshot_pg, monkeypatch
):
    from fastapi import FastAPI
    import httpx
    from app.database import get_db
    from app.routers.category_seasonality_report import router

    _, engine, factory, _ = snapshot_pg
    app = FastAPI()
    app.include_router(router, prefix="/api")

    async def database():
        async with factory() as db:
            yield db

    app.dependency_overrides[get_db] = database

    async def run():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as c:
            before = (await c.post("/api/reports/category-seasonality", json={})).json()
            assert before["state"] == "uninitialized" and before["last_success"] is None
            async with factory() as db:
                await seed(db)
            result = await service.rebuild(engine, manual=True, clock=CLOCK)
            monkeypatch.setattr(
                service,
                "materialize",
                AsyncMock(side_effect=AssertionError("Raw recomputation forbidden")),
            )
            report = (await c.post("/api/reports/category-seasonality", json={})).json()
            assert (
                report["snapshot_id"] == result["id"]
                and report["through"] == "2026-10-31"
            )
            p = dict(snapshot_id=result["id"], categories=[report["rows"][0]["key"]])
            detail = (
                await c.post("/api/reports/category-seasonality/detail", json=p)
            ).json()
            assert (
                detail["snapshot_id"] == report["snapshot_id"]
                and len(detail["rows"]) == 1
                and detail["rows"][0]["units"]["actual"]
            )
            assert (
                await c.post("/api/reports/category-seasonality/detail", json={})
            ).status_code == 422

    asyncio.run(run())


def test_fast_reader_admission_is_separate_bounded_and_released(snapshot_pg):
    from fastapi import FastAPI
    import httpx
    from app.database import get_db
    from app.routers.category_seasonality_report import router

    _, engine, factory, _ = snapshot_pg
    app = FastAPI()
    app.include_router(router, prefix="/api")

    async def database():
        async with factory() as db:
            yield db

    app.dependency_overrides[get_db] = database

    async def run():
        async with engine.connect() as owner:
            await owner.execute(sa.text("SELECT pg_advisory_xact_lock(734190292)"))
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                before = time.perf_counter()
                response = await client.post(
                    "/api/reports/category-seasonality", json={}
                )
                assert response.status_code == 503 and time.perf_counter() - before < 2
                await owner.rollback()
                assert (
                    await client.post("/api/reports/category-seasonality", json={})
                ).status_code == 200

    asyncio.run(run())
