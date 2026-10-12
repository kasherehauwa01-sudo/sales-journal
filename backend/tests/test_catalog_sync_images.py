import asyncio

import pytest
from pydantic import ValidationError
from app.models import CatalogProduct, CatalogSyncState
from app.services import catalog_attributes as attributes, catalog_sync as sync
from app.services.catalog_lookup_client import LookupProduct
from test_catalog_directory import database, product, results
from test_catalog_sync_worker import quiet  # noqa: F401


def test_optional_image_contract_remains_strict():
    assert LookupProduct.model_validate(product()).image_url is None
    assert LookupProduct.model_validate(product(image_url=None)).image_url is None
    assert (
        LookupProduct.model_validate(
            product(image_url="https://catalog.test/photo.jpg")
        ).image_url
        == "https://catalog.test/photo.jpg"
    )
    for values in ({"image_url": 123}, {"unexpected": "value"}):
        with pytest.raises(ValidationError):
            LookupProduct.model_validate(product(**values))


@pytest.mark.parametrize(
    "value",
    [
        None,
        "",
        "  ",
        "javascript:alert(1)",
        "data:image/png;base64,AAA",
        "file:///tmp/photo",
        "ftp://host/photo",
        "https://",
        "https://user:password@host/photo",
        "https://host:bad/photo",
        "https://host/has space",
        "https://host/\nphoto",
        "https://host\\evil/photo",
    ],
)
def test_unsafe_or_empty_image_is_ignored(value):
    assert sync.sync_image_url(value) is None


def test_public_root_normalization(monkeypatch):
    monkeypatch.setattr(
        sync.settings, "vrcatalog_api_url", "https://catalog.test/vr/catalog/api"
    )
    assert (
        sync.sync_image_url(" /media/photo.jpg ")
        == "https://catalog.test/vr/catalog/media/photo.jpg"
    )
    assert (
        sync.sync_image_url("https://cdn.test/photo.jpg?size=100")
        == "https://cdn.test/photo.jpg?size=100"
    )


@pytest.mark.parametrize(
    "next_values",
    [{}, {"image_url": None}, {"image_url": ""}, {"image_url": "javascript:alert(1)"}],
)
def test_resync_retains_photo_and_updates_report_cache(monkeypatch, next_values):
    invalidations = []
    monkeypatch.setattr(
        sync, "invalidate_product_analytics_cache", lambda: invalidations.append(True)
    )

    async def client(items):
        return results(items, image_url="https://catalog.test/photo.jpg")

    async def old_client(items):
        return results(items, **next_values)

    async def run():
        async with database([("001", "A")]) as (_, factory):
            await sync.synchronize(factory, client=client)
            async with factory() as db:
                first = (await db.get(CatalogSyncState, 1)).revision
                data = await attributes.local_catalog(
                    [{"key": "code:001", "code": "001", "article": "A"}], db
                )
                assert data["code:001"]["image_url"] == "https://catalog.test/photo.jpg"
                from app.services.product_analytics import merge_periods

                report = merge_periods(
                    [
                        {
                            "key": "code:001",
                            "code": "001",
                            "article": "A",
                            "name": "Товар",
                            "revenue": 100,
                            "units": 2,
                            "checks": 1,
                        }
                    ],
                    [],
                    data,
                )
                assert report[0]["image_url"] == "https://catalog.test/photo.jpg"
                assert report[0]["brand"] == "Бренд"
                assert report[0]["revenue"] == 100
            await sync.synchronize(factory, client=old_client)
            async with factory() as db:
                assert (
                    await db.get(CatalogProduct, 1)
                ).image_url == "https://catalog.test/photo.jpg"
                assert (await db.get(CatalogSyncState, 1)).revision > first

    asyncio.run(run())
    assert len(invalidations) == 2


def test_new_photo_replaces_old_link():
    images = iter(["https://catalog.test/old.jpg", "https://catalog.test/new.jpg"])

    async def client(items):
        return results(items, image_url=next(images))

    async def run():
        async with database([("001", "A")]) as (_, factory):
            await sync.synchronize(factory, client=client)
            await sync.synchronize(factory, client=client)
            async with factory() as db:
                assert (
                    await db.get(CatalogProduct, 1)
                ).image_url == "https://catalog.test/new.jpg"

    asyncio.run(run())


def test_existing_refresh_fills_photos_without_resetting_sales_cursor(quiet):
    from test_catalog_sync_worker import fixture, command
    from app.services import (
        catalog_sync_worker as worker,
        catalog_sync_control as control,
    )

    async def initial(items):
        return results(items)

    async def photographed(items):
        return results(items, image_url="https://catalog.test/refreshed.jpg")

    async def run():
        async with fixture([("001", "A"), ("002", "B")]) as (engine, factory):
            await worker.run_request(engine, factory, asyncio.Event(), initial)
            async with factory() as db:
                assert (await db.get(CatalogProduct, 1)).image_url is None
                revision = (await db.get(CatalogSyncState, 1)).revision
            await command(factory, mode="refresh")
            await worker.run_request(engine, factory, asyncio.Event(), photographed)
            async with factory() as db:
                state = await db.get(CatalogSyncState, 1)
                assert state.cursor == 2 and state.cycle == 1
                assert state.revision > revision
                assert (await control.control_row(db)).refresh_completed
                assert (
                    await db.get(CatalogProduct, 1)
                ).image_url == "https://catalog.test/refreshed.jpg"

    asyncio.run(run())
