"""Regression checks against the frozen pre-optimization loop, no external APIs."""

import asyncio
import logging
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import event
from fastapi import HTTPException
from app.models import CatalogLookup, CatalogProduct
from app.services import catalog_attributes as attributes
from benchmarks.benchmark_local_catalog import before
from test_catalog_directory import database


async def seed(factory, count=8):
    stamp = datetime(2026, 9, 30, 1, 2, 3, 123456, tzinfo=timezone.utc)
    async with factory() as db:
        for index in range(count):
            db.add(
                CatalogProduct(
                    product_id=index + 1,
                    code=f"000{index}",
                    article=f"00{index}",
                    manufacturer=None if index % 2 else "Завод",
                    brand="Бренд",
                    category="Родитель",
                    subcategory="Подкатегория",
                    legacy_category="Прежняя категория",
                    material=None,
                    horeca=index % 2 == 0,
                    category_id=7,
                    image_url=f"https://image.example.test/{index}.jpg",
                    source_updated_at=stamp,
                    synced_at=stamp,
                )
            )
        await db.flush()
        for index in range(count):
            status = ["matched", "not_found", "ambiguous", "pending", "invalid"][
                index % 5
            ]
            db.add(
                CatalogLookup(
                    identity=attributes.identity(f"000{index}", f"00{index}"),
                    code=f"000{index}",
                    article=f"00{index}",
                    status=status,
                    product_id=index + 1,
                    matched_by="code" if status == "matched" else None,
                    cycle=1,
                )
            )
        await db.commit()


def row(index, key=None):
    return {
        "key": key or f"code:000{index}",
        "code": f"000{index}",
        "article": f"00{index}",
    }


@pytest.mark.parametrize(
    "count,expected_queries", [(0, 0), (1, 1), (3, 1), (4, 2), (6, 2), (7, 3)]
)
def test_batch_boundaries_and_repeated_identities(count, expected_queries, monkeypatch):
    monkeypatch.setattr(attributes, "READ_BATCH_SIZE", 3)
    hashes, memory_checks = [], []
    original = attributes.identity
    monkeypatch.setattr(
        attributes,
        "identity",
        lambda code, article: (hashes.append((code, article)), original(code, article))[
            1
        ],
    )
    monkeypatch.setattr(
        attributes, "ensure_headroom", lambda: memory_checks.append(True)
    )

    async def run():
        async with database() as (engine, factory):
            queries, batches = [], []

            def sql(_conn, _cursor, statement, parameters, _context, _many):
                if statement.startswith("SELECT"):
                    queries.append(statement)
                    batches.append(parameters)

            event.listen(engine.sync_engine, "before_cursor_execute", sql)
            rows = [row(index) for index in range(count)]
            # Entire second period duplicates first; include absent lookups too.
            rows += list(reversed(rows))
            async with factory() as db:
                result = await attributes.local_catalog(iter(rows), db)
            assert len(queries) == expected_queries
            assert len(hashes) == len(rows)  # One SHA per input row, not twice.
            queried = [value for values in batches for value in values]
            assert len(queried) == len(set(queried)) == count
            assert len(result) == count
            assert all(
                info == {"catalog_status": "not_synced", "catalog_synced_at": None}
                for info in result.values()
            )
            assert len(memory_checks) == (len(rows) + 2) // 3

    asyncio.run(run())


def test_scalar_projection_preserves_status_dates_photos_and_nulls(monkeypatch):
    monkeypatch.setattr(attributes, "READ_BATCH_SIZE", 3)

    async def run():
        async with database() as (_, factory):
            await seed(factory)
            rows = [row(i) for i in range(8)] + [
                row(20),
                {"key": "empty", "code": None, "article": None},
            ]
            async with factory() as db:
                expected = await before(rows, db)
            async with factory() as db:
                actual = await attributes.local_catalog(rows, db)
                assert (
                    len(db.identity_map) == 0
                )  # No CatalogProduct/Lookup ORM objects.
            assert actual == expected
            assert actual["code:0000"]["category"] == "Прежняя категория"
            assert actual["code:0000"]["catalog_category"] == "Родитель"
            assert actual["code:0000"]["section"] == "Подкатегория"
            assert actual["code:0000"]["image_url"].endswith("/0.jpg")
            assert actual["code:0000"]["material"] is None
            assert (
                actual["code:0001"]["catalog_synced_at"] is not None
            )  # Last-good time remains.
            assert actual["code:0002"]["catalog_status"] == "ambiguous"

    asyncio.run(run())


def test_conflicts_between_batches_match_original_and_aliases_are_independent(
    monkeypatch,
):
    monkeypatch.setattr(attributes, "READ_BATCH_SIZE", 2)

    async def run():
        async with database() as (_, factory):
            await seed(factory)
            rows = [
                row(0, "collision"),
                row(5, "separate"),
                row(5, "collision"),
                row(0, "collision"),
                row(0, "alias"),
                row(0, "second_alias"),
                row(20, "missing_alias"),
                row(20, "missing_other"),
                row(2, "collision"),
                row(2, "collision"),
            ]
            async with factory() as db:
                expected = await before(rows, db)
            async with factory() as db:
                actual = await attributes.local_catalog(iter(rows), db)
            assert actual == expected
            assert actual["collision"]["catalog_status"] == "ambiguous"
            assert actual["alias"] is not actual["second_alias"]
            assert actual["missing_alias"] is not actual["missing_other"]
            actual["alias"]["brand"] = "changed"
            assert actual["second_alias"]["brand"] == "Бренд"

    asyncio.run(run())


def test_same_identity_is_not_retained_across_invocations(monkeypatch):
    monkeypatch.setattr(attributes, "READ_BATCH_SIZE", 1)

    async def run():
        async with database() as (_, factory):
            await seed(factory, 1)
            async with factory() as db:
                first = await attributes.local_catalog([row(0)], db)
            async with factory() as db:
                product = await db.get(CatalogProduct, 1)
                product.brand = "Новый бренд"
                await db.commit()
            async with factory() as db:
                second = await attributes.local_catalog([row(0)], db)
            assert first["code:0000"]["brand"] == "Бренд"
            assert second["code:0000"]["brand"] == "Новый бренд"

    asyncio.run(run())


def test_memory_guard_prevents_sql(monkeypatch):
    def reject():
        raise HTTPException(503, "busy")

    monkeypatch.setattr(attributes, "ensure_headroom", reject)

    async def execute(*args):
        pytest.fail("SQL executed after headroom rejection")

    async def run():
        with pytest.raises(HTTPException):
            await attributes.local_catalog([row(0)], SimpleNamespace(execute=execute))

    asyncio.run(run())


def test_diagnostics_contain_only_counts_and_timing(caplog, monkeypatch):
    caplog.set_level(logging.INFO, logger=attributes.__name__)
    monkeypatch.setattr(attributes, "READ_BATCH_SIZE", 2)

    async def run():
        async with database() as (_, factory):
            async with factory() as db:
                rows = [
                    {
                        "key": "SECRET_REPORT_KEY",
                        "code": "SECRET_CODE",
                        "article": "SECRET_ARTICLE",
                    }
                ] * 4
                await attributes.local_catalog(rows, db)

    asyncio.run(run())
    record = next(
        record
        for record in caplog.records
        if hasattr(record, "local_catalog_sql_queries")
    )
    assert record.local_catalog_rows == 4
    assert record.local_catalog_unique_identities == 1
    assert record.local_catalog_sql_queries == 1
    assert record.local_catalog_elapsed_ms >= 0
    assert "SECRET_" not in caplog.text


def test_repeatable_read_covers_all_new_query_batches(monkeypatch):
    """Tiny optional PostgreSQL check; never uses app DATABASE_URL."""
    import os
    import uuid
    from benchmarks.benchmark_local_catalog import (
        prepare,
        clean,
        engine_for,
        identifiers,
    )
    from sqlalchemy import update
    from sqlalchemy.ext.asyncio import async_sessionmaker

    if not os.environ.get("LOCAL_CATALOG_BENCH_DATABASE_URL"):
        pytest.skip("Requires disposable localhost catalog_perf_test")
    monkeypatch.setattr(attributes, "READ_BATCH_SIZE", 2)

    async def run():
        schema = "catalog_bench_" + uuid.uuid4().hex
        await prepare(schema, 6)
        engine = engine_for(schema)
        factory = async_sessionmaker(engine, expire_on_commit=False)
        try:
            rows = [
                {
                    "key": f"key:{i}",
                    "code": identifiers(i)[0],
                    "article": identifiers(i)[1],
                }
                for i in range(6)
            ]
            async with factory() as db:
                await attributes.begin_catalog_snapshot(db)
                calls = 0
                # The benchmark engine has a 1-connection pool, so writer must
                # have its own engine while the snapshot retains its connection.
                writer_engine = engine_for(schema)
                writer_factory = async_sessionmaker(
                    writer_engine, expire_on_commit=False
                )

                async def snapshot_execute(query):
                    nonlocal calls
                    result = await db.execute(query)
                    calls += 1
                    if calls == 1:
                        async with writer_factory() as writer:
                            await writer.execute(
                                update(CatalogProduct)
                                .where(CatalogProduct.product_id == 5)
                                .values(brand="Новый бренд")
                            )
                            await writer.commit()
                    return result

                try:
                    result = await attributes.local_catalog(
                        rows, SimpleNamespace(execute=snapshot_execute)
                    )
                    assert calls == 3
                    assert result["key:4"]["brand"] == "Бренд 4"
                finally:
                    await writer_engine.dispose()
            async with factory() as db:
                latest = await attributes.local_catalog([rows[4]], db)
                assert latest["key:4"]["brand"] == "Новый бренд"
        finally:
            await engine.dispose()
            await clean(schema)

    asyncio.run(run())
