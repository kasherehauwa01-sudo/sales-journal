import asyncio
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select, event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from app.models import (
    Base,
    CatalogProduct,
    CatalogLookup,
    CatalogSyncState,
    ImportBatch,
    Sale,
    SaleItem,
)
from app.services import catalog_attributes as attributes, catalog_sync as sync
from app.services.catalog_lookup_client import LookupError, LookupResponse
from app.services import product_analytics_cache as cache
from app.routers import product_analytics as router


def product(code="001", **values):
    return {
        "product_id": 1,
        "code": code,
        "article": "DUP",
        "manufacturer": "Завод",
        "brand": "Бренд",
        "category_id": 7,
        "category": "Родитель",
        "subcategory": "Раздел",
        "legacy_category": "Раздел",
        "material": "Фарфор",
        "horeca": False,
        "updated_at": "2026-10-10T08:00:00.123456Z",
        **values,
    }


def results(items, status="matched", **values):
    return LookupResponse.model_validate(
        {
            "schema_version": 1,
            "items": [
                {
                    "request_index": i,
                    "status": status,
                    "matched_by": (
                        ("code" if item.get("code") else "article")
                        if status == "matched"
                        else None
                    ),
                    "product": (
                        product(item.get("code", "SOURCE"), **values)
                        if status == "matched"
                        else None
                    ),
                }
                for i, item in enumerate(items)
            ],
        }
    ).items


@asynccontextmanager
async def database(rows=()):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")

    @event.listens_for(engine.sync_engine, "connect")
    def foreign_keys(connection, _):
        cursor = connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    async with factory() as db:
        db.add(ImportBatch(id=1, filename="fixture", file_size=0, status="completed"))
        await db.flush()
        db.add(
            Sale(
                id=1,
                sale_date=date(2026, 9, 10),
                document_number="1",
                department="Магазин",
                total_amount=1,
                fingerprint="1",
                import_id=1,
            )
        )
        await db.flush()
        for index, (code, article) in enumerate(rows, 1):
            db.add(
                SaleItem(
                    id=index,
                    sale_id=1,
                    code=code,
                    article=article,
                    name="Товар",
                    quantity=1,
                    actual_price=12.25,
                )
            )
        await db.commit()
    try:
        yield engine, factory
    finally:
        await engine.dispose()
        cache.invalidate_product_analytics_cache()


def test_identity_preserves_zeros_and_pair_and_normalizes():
    assert attributes.identity(" 001 ", " A ") == attributes.identity("001", "a")
    assert attributes.identity("001", "a") != attributes.identity("1", "a")
    assert attributes.identity("001", "a") != attributes.identity("001", "b")
    assert attributes.identity(None, " DUP ") == attributes.identity(" ", "dup")


def test_sync_resumes_and_reconciles_current_data_without_timestamp_cursor():
    calls = []
    brand = ["Бренд"]

    async def client(items):
        calls.append(items)
        return results(items, brand=brand[0])

    async def run():
        async with database(
            [("001", "A"), ("001", "A"), ("002", "B"), ("003", "C")]
        ) as (_, factory):
            first = await sync.synchronize(
                factory, max_products=1, batch_size=2, client=client
            )
            assert first["processed"] == 1
            async with factory() as db:
                state = await db.get(CatalogSyncState, 1)
                assert (
                    state.cursor == 2
                    and state.status == "paused"
                    and not state.completed
                )
            await sync.synchronize(
                factory, max_products=10, batch_size=2, client=client
            )
            assert [len(items) for items in calls] == [1, 2]
            async with factory() as db:
                assert (await db.get(CatalogSyncState, 1)).completed
            # Same source timestamp, changed attributes: a full new pass MUST refresh.
            brand[0] = "Обновлённый"
            await sync.synchronize(
                factory, max_products=10, batch_size=2, client=client
            )
            async with factory() as db:
                assert (await db.get(CatalogProduct, 1)).brand == "Обновлённый"
                assert (await db.get(CatalogSyncState, 1)).cycle == 2

    asyncio.run(run())


@pytest.mark.parametrize(
    "failure", [LookupError("http_500", True), LookupError("transport_error", True)]
)
def test_failed_batch_keeps_last_good_data_and_checkpoint(failure):
    async def good(items):
        return results(items)

    async def bad(items):
        raise failure

    async def run():
        async with database([("001", "A")]) as (_, factory):
            await sync.synchronize(factory, client=good)
            async with factory() as db:
                old_revision = (await db.get(CatalogSyncState, 1)).revision
            with pytest.raises(LookupError):
                await sync.synchronize(factory, client=bad)
            async with factory() as db:
                assert (await db.get(CatalogProduct, 1)).brand == "Бренд"
                lookup = await db.get(CatalogLookup, attributes.identity("001", "A"))
                assert lookup.status == "matched"
                state = await db.get(CatalogSyncState, 1)
                assert (
                    state.cursor == 0
                    and state.revision == old_revision
                    and state.errors == 1
                )
            await sync.synchronize(factory, client=good)
            async with factory() as db:
                assert (await db.get(CatalogSyncState, 1)).completed

    asyncio.run(run())


@pytest.mark.parametrize("status", ["not_found", "ambiguous"])
def test_nonmatches_are_explicit_and_do_not_delete_previous_product(status):
    async def run():
        async with database([("001", "DUP"), (None, "DUP")]) as (_, factory):

            async def good(items):
                return results(items)

            await sync.synchronize(factory, client=good)

            async def missing(items):
                return results(items, status=status)

            await sync.synchronize(factory, client=missing)
            async with factory() as db:
                assert await db.get(CatalogProduct, 1) is not None
                rows = await attributes.local_catalog(
                    [
                        {"key": "code:001", "code": "001", "article": "DUP"},
                        {"key": "article:dup", "code": None, "article": "DUP"},
                    ],
                    db,
                )
                assert rows["code:001"]["catalog_status"] == status
                assert "brand" not in rows["code:001"]
                assert rows["article:dup"]["catalog_status"] == status

    asyncio.run(run())


def test_null_attributes_replace_old_values_and_material_and_legacy_category():
    async def run():
        async with database([("001", "A")]) as (_, factory):

            async def client(items):
                return results(items)

            await sync.synchronize(factory, client=client)
            async with factory() as db:
                info = await attributes.local_catalog(
                    [{"key": "code:001", "code": "001", "article": "A"}], db
                )
                assert info["code:001"]["category"] == "Раздел"
                assert info["code:001"]["catalog_category"] == "Родитель"
                assert info["code:001"]["material"] == "Фарфор"

            async def empty(items):
                return results(
                    items,
                    brand=None,
                    manufacturer=None,
                    legacy_category=None,
                    subcategory=None,
                    material=None,
                )

            await sync.synchronize(factory, client=empty)
            async with factory() as db:
                stored = await db.get(CatalogProduct, 1)
                assert stored.brand is None and stored.material is None

    asyncio.run(run())


def test_scan_and_batch_limits_and_safe_stop():
    seen = []
    stop = asyncio.Event()

    async def client(items):
        seen.append(len(items))
        return results(items)

    async def run():
        async with database([(str(i), str(i)) for i in range(510)]) as (_, factory):
            result = await sync.synchronize(
                factory,
                batch_size=250,
                max_products=300,
                max_scan_rows=300,
                client=client,
            )
            assert (
                seen == [250, 50]
                and result["scanned"] == 300
                and result["processed"] == 300
            )
            stop.set()
            result = await sync.synchronize(factory, stop_event=stop, client=client)
            assert result["processed"] == 0 and seen == [250, 50]
            async with factory() as db:
                assert (await db.get(CatalogSyncState, 1)).status == "stopped"

    asyncio.run(run())


def test_invalid_identifiers_are_recorded_without_api_and_not_truncated():
    async def client(items):
        pytest.fail("invalid identifiers reached API")

    async def run():
        async with database([(None, None), ("x" * 129, "A"), ("x" * 129, "A")]) as (
            _,
            factory,
        ):
            result = await sync.synchronize(factory, batch_size=100, client=client)
            assert result["invalid"] == 2 and result["processed"] == 2
            async with factory() as db:
                assert (
                    await db.get(CatalogLookup, attributes.identity("x" * 129, "A"))
                ).code == "x" * 129

    asyncio.run(run())


def test_report_pipeline_is_local_and_cache_invalidates_across_sync(monkeypatch):
    async def no_network(*args):
        pytest.fail("report called VRCatalog")

    monkeypatch.setattr(router, "get_catalog_batch_info", no_network)

    async def managers(*args):
        return None

    monkeypatch.setattr(router, "_manager_clients", managers)
    build_calls = []

    async def periods(data, db, start, end, clients):
        build_calls.append(1)
        return [
            {
                "key": "code:001",
                "code": "001",
                "article": "A",
                "name": "Товар",
                "revenue": 12.25 if start == data.date_from else 10.125,
                "units": 1.0,
                "checks": 1,
                "first_sale": start,
                "last_sale": end,
            }
        ]

    monkeypatch.setattr(router, "_period_rows", periods)

    # SQLite has no PostgreSQL REPEATABLE READ; explicitly emulate only the
    # snapshot boundary in this fixture, not application behavior.
    async def snapshot(db):
        pass

    monkeypatch.setattr(router, "begin_catalog_snapshot", snapshot)
    brand = ["Бренд"]

    async def client(items):
        return results(items, brand=brand[0])

    async def run():
        async with database([("001", "A")]) as (_, factory):
            await sync.synchronize(factory, client=client)
            request = router.AnalyticsRequest(
                date_from=date(2026, 9, 1), date_to=date(2026, 9, 30)
            )
            async with factory() as db:
                first = await router._dataset(request, db)
            async with factory() as db:
                second = await router._dataset(request, db)
            assert first == second and len(build_calls) == 2
            assert (
                first[0][0]["revenue_difference"] == 2.125
                and first[0][0]["brand"] == "Бренд"
            )
            # Cross-worker simulation: suppress this worker's local invalidation;
            # persisted revision alone must force a new cache entry.
            monkeypatch.setattr(
                sync, "invalidate_product_analytics_cache", lambda: None
            )
            brand[0] = "Новый"
            await sync.synchronize(factory, client=client)
            async with factory() as db:
                third = await router._dataset(request, db)
            assert third[0][0]["brand"] == "Новый" and len(build_calls) == 4
            async with factory() as db:
                assert await attributes.local_options(db, "brand") == ["Новый"]
                assert await attributes.local_options(db, "brand", "Новый") == ["Новый"]

    asyncio.run(run())


def test_missing_and_conflicting_lookup_pairs_are_not_arbitrarily_matched():
    async def run():
        async with database([("001", "A"), ("001", "B")]) as (_, factory):

            async def client(items):
                response = results(items)
                response[1].product.product_id = 2
                return response

            await sync.synchronize(factory, client=client)
            async with factory() as db:
                rows = await attributes.local_catalog(
                    [
                        {"key": "code:001", "code": "001", "article": "A"},
                        {"key": "code:001", "code": "001", "article": "B"},
                        {"key": "code:missing", "code": "missing", "article": None},
                    ],
                    db,
                )
                assert rows["code:001"]["catalog_status"] == "ambiguous"
                assert rows["code:missing"]["catalog_status"] == "not_synced"

    asyncio.run(run())


def test_synclock_is_nonblocking_and_always_released():
    statements = []

    class Connection:
        acquired = True

        async def scalar(self, stmt, params):
            statements.append(str(stmt))
            return self.acquired

        async def execute(self, stmt, params):
            statements.append(str(stmt))

        async def commit(self):
            pass

        async def invalidate(self):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

    connection = Connection()
    engine = SimpleNamespace(connect=lambda: connection)

    async def run():
        with pytest.raises(RuntimeError):
            async with sync.sync_lock(engine):
                raise RuntimeError("stop")
        assert "pg_advisory_unlock" in statements[-1]
        connection.acquired = False
        with pytest.raises(sync.SyncBusy):
            async with sync.sync_lock(engine):
                pytest.fail("second sync admitted")

    asyncio.run(run())


def test_status_counts_stale_pending_and_safe_diagnostics(caplog):
    async def run():
        async with database([("PRIVATE_CODE", "PRIVATE_ARTICLE")]) as (_, factory):

            async def client(items):
                return results(items)

            await sync.synchronize(factory, client=client)
            async with factory() as db:
                lookup = await db.get(
                    CatalogLookup,
                    attributes.identity("PRIVATE_CODE", "PRIVATE_ARTICLE"),
                )
                lookup.synced_at = datetime.now(timezone.utc) - timedelta(days=31)
                db.add(
                    CatalogLookup(
                        identity="pending",
                        code="pending",
                        article=None,
                        status="pending",
                        cycle=0,
                    )
                )
                await db.commit()
                status = await attributes.sync_status(db)
                assert (
                    status["stale_records"] == 1
                    and status["unsynchronized_records"] == 1
                )
                assert status["automatic_sync_enabled"] is False

    import logging

    caplog.set_level(logging.INFO)
    asyncio.run(run())
    assert "PRIVATE_CODE" not in caplog.text and "PRIVATE_ARTICLE" not in caplog.text


def test_local_filter_values_load_all_205_and_keep_names_and_search(monkeypatch):
    async def forbidden(*args, **kwargs):
        pytest.fail("local filters called CatalogVR")

    monkeypatch.setattr(router, "get_product_filters", forbidden)
    monkeypatch.setattr(router, "get_product_filter_options", forbidden)

    async def run():
        async with database() as (_, factory):
            async with factory() as db:
                for i in range(205):
                    item = product(str(i), product_id=i + 1, brand=f"Brand {i:03}")
                    item["source_updated_at"] = datetime.now(timezone.utc)
                    item["synced_at"] = datetime.now(timezone.utc)
                    item.pop("updated_at")
                    db.add(CatalogProduct(**item))
                await db.commit()
            async with factory() as db:
                values = await router.catalog_options("brand", db=db)
                assert (
                    len(values) == 205
                    and values[0] == "Brand 000"
                    and values[-1] == "Brand 204"
                )
                assert await router.catalog_options("brand", "Brand 20", db) == [
                    "Brand 200",
                    "Brand 201",
                    "Brand 202",
                    "Brand 203",
                    "Brand 204",
                ]

    asyncio.run(run())


def test_detail_image_is_retained_without_network_in_next_report():
    async def client(items):
        return results(items)

    async def run():
        async with database([("001", "A")]) as (_, factory):
            await sync.synchronize(factory, client=client)
            async with factory() as db:
                before = await attributes.catalog_revision(db)
                await attributes.remember_detail_image(
                    db,
                    {"code": "001", "article": "A"},
                    "https://example.test/photo.jpg",
                )
            async with factory() as db:
                info = await attributes.local_catalog(
                    [{"key": "code:001", "code": "001", "article": "A"}], db
                )
                assert info["code:001"]["image_url"] == "https://example.test/photo.jpg"
                assert await attributes.catalog_revision(db) == before + 1
            # Characteristics synchronization does not clear a stored photo.
            await sync.synchronize(factory, client=client)
            async with factory() as db:
                assert (
                    await db.get(CatalogProduct, 1)
                ).image_url == "https://example.test/photo.jpg"

    asyncio.run(run())


def test_manual_stop_waits_for_current_batch_and_does_not_start_next():
    stop = asyncio.Event()
    calls = []

    async def client(items):
        calls.append(items)
        stop.set()
        return results(items)

    async def run():
        async with database([("001", "A"), ("002", "B")]) as (_, factory):
            result = await sync.synchronize(
                factory, batch_size=1, stop_event=stop, client=client
            )
            assert result["processed"] == 1 and len(calls) == 1
            async with factory() as db:
                state = await db.get(CatalogSyncState, 1)
                assert state.cursor == 1 and state.status == "stopped"

    asyncio.run(run())


def test_cancelled_sync_records_checkpoint_and_can_resume():
    entered = asyncio.Event()

    async def client(items):
        entered.set()
        await asyncio.Event().wait()

    async def run():
        async with database([("001", "A")]) as (_, factory):
            task = asyncio.create_task(sync.synchronize(factory, client=client))
            await entered.wait()
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            async with factory() as db:
                state = await db.get(CatalogSyncState, 1)
                assert (
                    state.cursor == 0
                    and state.status == "stopped"
                    and state.error_code == "cancelled"
                )

            async def good(items):
                return results(items)

            await sync.synchronize(factory, client=good)
            async with factory() as db:
                assert (await db.get(CatalogSyncState, 1)).completed

    asyncio.run(run())


def test_legacy_rollback_mode_uses_old_catalog_without_local_queries(monkeypatch):
    monkeypatch.setattr(
        router.settings, "product_analytics_local_catalog_enabled", False
    )

    async def forbidden(*args, **kwargs):
        pytest.fail("local tables used in rollback mode")

    async def managers(*args):
        return None

    async def period(data, db, start, end, clients):
        return [
            {
                "key": "code:001",
                "code": "001",
                "article": "A",
                "name": "Товар",
                "revenue": 12.25,
                "units": 1.0,
                "checks": 1,
                "first_sale": start,
                "last_sale": end,
            }
        ]

    called = []

    async def catalog(rows):
        called.append(1)
        return {"code:001": {"brand": "Legacy", "category": "Раздел"}}

    monkeypatch.setattr(router, "local_catalog", forbidden)
    monkeypatch.setattr(router, "begin_catalog_snapshot", forbidden)
    monkeypatch.setattr(router, "catalog_revision", forbidden)
    monkeypatch.setattr(router, "_manager_clients", managers)
    monkeypatch.setattr(router, "_period_rows", period)
    monkeypatch.setattr(router, "_catalog", catalog)

    async def run():
        async with database() as (_, factory):
            async with factory() as db:
                rows, *_ = await router._dataset(
                    router.AnalyticsRequest(
                        date_from=date(2026, 9, 1), date_to=date(2026, 9, 30)
                    ),
                    db,
                )
                assert rows[0]["brand"] == "Legacy" and len(called) == 1
                assert await router.catalog_status(db) == {
                    "enabled": False,
                    "automatic_sync_enabled": False,
                }

    asyncio.run(run())


def test_financial_filters_groupings_and_classification_match_legacy_projection():
    from app.services.product_analytics import (
        GROUP_FIELDS,
        merge_periods,
        group_rows,
        summary,
        classify,
        filter_values,
    )

    now = [
        {
            "key": "code:001",
            "code": "001",
            "article": "A",
            "name": "Товар",
            "revenue": 12.25,
            "units": 2,
            "checks": 1,
        },
        {
            "key": "code:002",
            "code": "002",
            "article": "B",
            "name": "Возврат",
            "revenue": -2.125,
            "units": -1,
            "checks": 1,
        },
    ]
    old = [dict(now[0], revenue=10.125), dict(now[1], revenue=0)]
    incoming = product()
    current = CatalogProduct(
        **{key: value for key, value in incoming.items() if key != "updated_at"},
        source_updated_at=datetime.now(timezone.utc),
        synced_at=datetime.now(timezone.utc),
    )
    lookup = CatalogLookup(status="matched")
    info = attributes.projection(lookup, current)
    legacy = {
        "brand": incoming["brand"],
        "manufacturer": incoming["manufacturer"],
        "category": incoming["legacy_category"],
        "section": incoming["subcategory"],
        "material": incoming["material"],
    }
    reference = merge_periods(now, old, {row["key"]: legacy for row in now})
    actual = merge_periods(now, old, {row["key"]: info for row in now})
    assert actual == reference
    assert summary(actual) == summary(reference) and classify(actual) == classify(
        reference
    )
    for grouping in GROUP_FIELDS:
        assert group_rows(actual, grouping) == group_rows(reference, grouping)
    assert filter_values(actual, "brand", ["бренд"]) == reference


def test_snapshot_boundary_does_not_change_global_database_settings():
    calls = []

    class Db:
        async def connection(self, **kw):
            calls.append(kw)

    asyncio.run(attributes.begin_catalog_snapshot(Db()))
    assert calls == [{"execution_options": {"isolation_level": "REPEATABLE READ"}}]


def test_unicode_casefold_expansion_is_not_truncated_in_lookup_storage():
    async def client(items):
        return results(items, status="not_found")

    async def run():
        async with database([(None, "ß" * 255)]) as (_, factory):
            await sync.synchronize(factory, client=client)
            async with factory() as db:
                lookup = await db.get(
                    CatalogLookup, attributes.identity(None, "ß" * 255)
                )
                assert lookup.article == "ss" * 255 and lookup.status == "not_found"

    asyncio.run(run())


def test_newly_imported_sales_are_reported_as_unscanned_without_full_count():
    async def client(items):
        return results(items)

    async def run():
        async with database([("001", "A")]) as (_, factory):
            await sync.synchronize(factory, client=client)
            async with factory() as db:
                db.add(
                    SaleItem(
                        id=2,
                        sale_id=1,
                        code="002",
                        article="B",
                        name="Новый",
                        quantity=1,
                        actual_price=1,
                    )
                )
                await db.commit()
            async with factory() as db:
                status = await attributes.sync_status(db)
                assert status["has_unscanned_sales"] and status["sales_upper_id"] == 2
                assert status["state"]["completed"] and status["state"]["upper_id"] == 1

    asyncio.run(run())
