"""Small real PG/asyncpg source-only evidence and authenticated HTTP contract."""

import asyncio
import hashlib
import json
import os
from datetime import date, datetime, timezone
from decimal import Decimal
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import insert, select, text, update, delete
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy.pool import NullPool

from app.config import settings
from app.database import get_db
from app.models import Base, Sale, ImportBatch
from app.routers.analytics_delivery import router
from app.services import calltrack_identity_history as service


def test_canonical_json_numeric_order_and_unicode():
    assert (
        service.encode({"z": "А", "a": 1})
        == service.encode({"a": 1, "z": "А"})
        == b'{"a":1,"z":"\xd0\x90"}\n'
    )
    assert (
        service.canonical_value(Decimal("123456789012345678901234567890.12300"))
        == "123456789012345678901234567890.123"
    )
    assert service.canonical_value(Decimal("-0.000")) == "0"
    assert (
        service.canonical_value(datetime(2026, 1, 1, tzinfo=timezone.utc))
        == "2026-01-01T00:00:00.000000Z"
    )
    with pytest.raises(ValueError):
        service.canonical_value(Decimal("NaN"))
    assert set(service.FIELDS) == set(Sale.__table__.columns.keys())


@pytest.fixture
def lab(monkeypatch):
    raw = os.environ.get("CALLTRACK_HISTORY_TEST_DATABASE_URL")
    if not raw:
        pytest.skip("Dedicated localhost PG test URL required")
    from sqlalchemy.engine import make_url

    url = make_url(raw)
    assert url.host == "127.0.0.1" and url.database == "calltrack_history_test"
    schema = "history_" + uuid4().hex
    admin = create_async_engine(raw, poolclass=NullPool)
    engine = create_async_engine(
        raw,
        poolclass=NullPool,
        connect_args={"server_settings": {"search_path": schema}},
    )
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async def setup():
        async with admin.begin() as c:
            await c.execute(text("CREATE SCHEMA " + schema))
        async with engine.begin() as c:
            await c.run_sync(
                lambda conn: Base.metadata.create_all(
                    conn, tables=[ImportBatch.__table__, Sale.__table__]
                )
            )
        async with factory() as db:
            await db.execute(
                insert(ImportBatch).values(
                    id=1, filename="synthetic", file_size=1, status="completed"
                )
            )
            await db.execute(
                insert(Sale),
                [
                    row(1),
                    row(2, client="  ООО\u00a0\u00a0РАДУГА\t"),
                    row(3, client="ооо радуга плюс"),
                    row(4, department="Другой"),
                    row(5, amount="-3", document="ВЗВ-5", day=5),
                    row(6, amount="100", document="РНВ-6", day=6),
                    row(7, amount="20", document="ВРМ-7", day=7),
                    row(8, amount="0", day=8),
                    row(9, client="Straße"),
                    row(10, client="STRASSE"),
                ],
            )
            await db.commit()

    asyncio.run(setup())
    app = FastAPI()
    app.include_router(router, prefix="/api")

    async def dependency():
        async with factory() as db:
            yield db

    app.dependency_overrides[get_db] = dependency
    monkeypatch.setattr(settings, "calltrack_integration_token", "synthetic-token")
    # A new loop per test is used; NullPool prevents retained asyncpg loop affinity.
    yield app, factory, engine

    async def cleanup():
        await engine.dispose()
        async with admin.begin() as c:
            await c.execute(text("DROP SCHEMA " + schema + " CASCADE"))
        await admin.dispose()

    asyncio.run(cleanup())


def row(
    id,
    client="ооо радуга",
    department="Авиаторов",
    amount="10.20",
    document=None,
    day=1,
):
    return dict(
        id=id,
        sale_date=date(2026, 1, day),
        document_number=document or "РЕА-" + str(id),
        client=client,
        department=department,
        total_amount=Decimal(amount),
        fingerprint="synthetic-" + str(id),
        import_id=1,
    )


async def request(app, path, *, token=True):
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://lab"
    ) as client:
        return await client.get(
            "/api/integrations/calltrack/analytics/" + path,
            headers={"Authorization": "Bearer synthetic-token"} if token else {},
        )


async def summary(app, key="ооо радуга"):
    from urllib.parse import urlencode

    return await request(app, "identity-history?" + urlencode({"client_key": key}))


async def page(app, hash, after=0, size=2, key="ооо радуга"):
    from urllib.parse import urlencode

    return await request(
        app,
        "identity-history/rows?"
        + urlencode(
            dict(client_key=key, history_hash=hash, after=after, page_size=size)
        ),
    )


def test_authenticated_full_source_pagination_and_positive_rule(lab):
    app, _, _ = lab

    async def run():
        assert (
            await request(app, "identity-history?client_key=x", token=False)
        ).status_code == 401
        s = (await summary(app)).json()
        assert s["version"] == s["normalization_version"] == 1 and s["sale_count"] == 6
        assert (
            s["last_positive_purchase"] == "2026-01-01"
            and s["automation_allowed"] is False
        )
        seen = []
        after = 0
        while True:
            response = await page(app, s["history_hash"], after)
            assert response.status_code == 200
            p = response.json()
            assert p["history_hash"] == s["history_hash"]
            seen.extend(p["items"])
            after = p["next_after"]
            if after is None:
                break
        assert [r["sale_id"] for r in seen] == [1, 2, 5, 6, 7, 8]
        assert seen[0]["total_amount"] == "10.2"
        digest = hashlib.sha256(
            service.encode(
                dict(
                    version=1,
                    normalization_version=1,
                    department="Авиаторов",
                    client_key="ооо радуга",
                    canonicalization="sale-headers-jsonl/1",
                )
            )
        )
        for r in seen:
            digest.update(service.encode(r))
        assert digest.hexdigest() == s["history_hash"]
        assert (await page(app, s["history_hash"], 3)).status_code == 409
        assert (
            await request(app, "identity-history/rows?client_key=x")
        ).status_code == 422

    asyncio.run(run())


@pytest.mark.parametrize(
    "mutation",
    [
        "insert_backdated",
        "update_money",
        "update_document",
        "update_other_field",
        "delete",
        "move_client",
        "move_department",
    ],
)
def test_changes_between_pages_fail_closed(lab, mutation):
    app, factory, _ = lab

    async def run():
        s = (await summary(app)).json()
        assert (await page(app, s["history_hash"])).status_code == 200
        async with factory() as db:
            if mutation == "insert_backdated":
                await db.execute(
                    insert(Sale).values(**{**row(20), "sale_date": date(2020, 1, 1)})
                )
            elif mutation == "delete":
                await db.execute(delete(Sale).where(Sale.id == 2))
            else:
                values = {
                    "update_money": dict(total_amount=Decimal("10.21")),
                    "update_document": dict(document_number="changed"),
                    "update_other_field": dict(author="synthetic author"),
                    "move_client": dict(client="another"),
                    "move_department": dict(department="Другой"),
                }[mutation]
                await db.execute(update(Sale).where(Sale.id == 2).values(**values))
            await db.commit()
        assert (await page(app, s["history_hash"], 1)).status_code == 409
        assert (await summary(app)).json()["history_hash"] != s["history_hash"]

    asyncio.run(run())


def test_empty_history_and_no_similar_name_mixing(lab):
    app, _, _ = lab

    async def run():
        s = (await summary(app, "missing")).json()
        assert (
            s["sale_count"] == 0 and s["empty"] and s["last_positive_purchase"] is None
        )
        assert (await page(app, s["history_hash"], key="missing")).json()["items"] == []
        assert (await summary(app, "ооо радуга плюс")).json()["sale_count"] == 1
        assert (await summary(app, "straße")).json()["sale_count"] == 1
        assert (await summary(app, "strasse")).json()["sale_count"] == 1
        assert (await summary(app, "' or 1=1--")).json()["sale_count"] == 0
        assert (await summary(app, "ООО РАДУГА")).status_code == 422
        assert (await summary(app, "   ")).status_code == 422
        for invalid in ["-1", "9223372036854775808", "abc"]:
            assert (
                await request(
                    app,
                    "identity-history/rows?client_key=x&history_hash="
                    + "a" * 64
                    + "&after="
                    + invalid,
                )
            ).status_code == 422

    asyncio.run(run())


def test_one_http_response_is_repeatable_read_during_import(lab, monkeypatch):
    app, factory, _ = lab
    original = service.row_value
    changed = False

    async def run():
        nonlocal changed
        before = (await summary(app)).json()
        # Pausing an async row stream after its first batch permits a concurrent writer.
        original_query = service.source_query

        async def write():
            async with factory() as db:
                await db.execute(insert(Sale).values(**row(30)))
                await db.commit()

        from sqlalchemy.ext.asyncio import AsyncSession

        original_stream = AsyncSession.stream

        async def stream(db, statement, *args, **kwargs):
            nonlocal changed
            result = await original_stream(db, statement, *args, **kwargs)
            if not changed and "sales." in str(statement):
                changed = True
                await write()
            return result

        monkeypatch.setattr(AsyncSession, "stream", stream)
        current = await page(app, before["history_hash"])
        assert (
            current.status_code == 200
            and current.json()["sale_count"] == before["sale_count"]
        )
        assert (await page(app, before["history_hash"], 1)).status_code == 409

    asyncio.run(run())


def test_timeout_is_not_empty_history_and_retry_works(lab, monkeypatch):
    app, _, _ = lab
    from sqlalchemy.ext.asyncio import AsyncSession

    original = AsyncSession.stream

    async def fail(db, statement, *args, **kwargs):
        await db.execute(text("SET LOCAL statement_timeout='1ms'"))
        await db.execute(text("SELECT pg_sleep(0.05)"))

    async def run():
        monkeypatch.setattr(AsyncSession, "stream", fail)
        response = await summary(app)
        assert response.status_code == 503 and "history_hash" not in response.json()
        assert "SELECT" not in response.text and "ооо" not in response.text
        monkeypatch.setattr(AsyncSession, "stream", original)
        assert (await summary(app)).status_code == 200

    asyncio.run(run())


def test_cancellation_closes_stream_and_releases_runtime_guard(lab, monkeypatch):
    app, _, _ = lab
    original = service.row_value
    from sqlalchemy.ext.asyncio import AsyncResult

    close = AsyncResult.close
    closed = False

    async def closing(self):
        nonlocal closed
        closed = True
        await close(self)

    def cancelled(row):
        raise asyncio.CancelledError()

    async def run():
        monkeypatch.setattr(AsyncResult, "close", closing)
        monkeypatch.setattr(service, "row_value", cancelled)
        with pytest.raises(asyncio.CancelledError):
            await summary(app)
        assert closed
        monkeypatch.setattr(service, "row_value", original)
        assert (await summary(app)).status_code == 200

    asyncio.run(run())


def test_large_group_spans_stream_batches_and_pages_under_readonly_settings(
    lab, monkeypatch
):
    app, factory, _ = lab
    original = service.scan

    async def checked(db, *args, **kwargs):
        flags = (
            await db.execute(
                text(
                    "SELECT current_setting('transaction_read_only'),current_setting('transaction_isolation'),current_setting('statement_timeout')"
                )
            )
        ).one()
        assert tuple(flags) == ("on", "repeatable read", "1min")
        return await original(db, *args, **kwargs)

    monkeypatch.setattr(service, "scan", checked)

    async def run():
        async with factory() as db:
            await db.execute(
                insert(Sale), [row(n, client="bulk") for n in range(100, 350)]
            )
            await db.commit()
        s = (await summary(app, "bulk")).json()
        assert s["sale_count"] == 250
        ids = []
        after = 0
        for expected_cursor in [199, 299, None]:
            response = await page(app, s["history_hash"], after, size=100, key="bulk")
            assert response.status_code == 200
            data = response.json()
            assert data["next_after"] == expected_cursor
            ids.extend(r["sale_id"] for r in data["items"])
            after = data["next_after"]
        assert ids == list(range(100, 350))
        assert (
            await page(app, s["history_hash"], size=101, key="bulk")
        ).status_code == 422

    asyncio.run(run())
