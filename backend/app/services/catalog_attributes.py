"""Read only the small projections for the requested sales identities."""

import hashlib
import json
from collections import Counter
from datetime import datetime, timedelta, timezone
from itertools import islice

from sqlalchemy import func, select
from app.services.product_analytics_runtime import ensure_headroom
from app.models import CatalogLookup, CatalogProduct, CatalogSyncState, SaleItem

READ_BATCH_SIZE = 250
STALE_DAYS = 30  # Diagnostic only; never silently discard successfully synced data.


def normalize(value):
    return (
        value.strip().casefold() if isinstance(value, str) and value.strip() else None
    )


def identity(code, article):
    return hashlib.sha256(
        json.dumps(
            [normalize(code), normalize(article)],
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()


async def begin_catalog_snapshot(db):
    # Per-report MVCC snapshot: all chunks and the cache revision agree while
    # the manual sync commits independently. This is not a server setting.
    await db.connection(execution_options={"isolation_level": "REPEATABLE READ"})


async def catalog_revision(db):
    return int(
        await db.scalar(
            select(CatalogSyncState.revision).where(CatalogSyncState.id == 1)
        )
        or 0
    )


def projection(lookup, product):
    status = lookup.status if lookup else "not_synced"
    info = {
        "catalog_status": status,
        "catalog_synced_at": product.synced_at if product else None,
    }
    # Retained last-good data is NOT used for a now-ambiguous/not-found identity.
    if status != "matched" or product is None:
        return info
    info.update(
        brand=product.brand,
        manufacturer=product.manufacturer,
        category=product.legacy_category,
        section=product.subcategory,
        material=product.material,
        horeca=product.horeca,
        catalog_category=product.category,
        category_id=product.category_id,
        product_id=product.product_id,
        image_url=product.image_url,
    )
    return info


async def local_catalog(rows, db):
    iterator = iter(rows)
    result = {}
    while batch := list(islice(iterator, READ_BATCH_SIZE)):
        ensure_headroom()
        keys = {identity(row.get("code"), row.get("article")) for row in batch}
        records = (
            await db.execute(
                select(CatalogLookup, CatalogProduct)
                .outerjoin(
                    CatalogProduct,
                    CatalogProduct.product_id == CatalogLookup.product_id,
                )
                .where(CatalogLookup.identity.in_(keys))
            )
        ).all()
        mapped = {lookup.identity: (lookup, product) for lookup, product in records}
        for row in batch:
            key = row["key"]
            pair = mapped.get(
                identity(row.get("code"), row.get("article")), (None, None)
            )
            info = projection(*pair)
            previous = result.get(key)
            # Conflicting raw pairs collapse to the old report key: never pick an
            # arbitrary source product. Numeric aggregation behavior is unchanged.
            if previous is not None and (
                previous.get("product_id"),
                previous["catalog_status"],
            ) != (info.get("product_id"), info["catalog_status"]):
                info = {"catalog_status": "ambiguous", "catalog_synced_at": None}
            result[key] = info
    return result


def coverage(rows):
    counts = Counter(row.get("catalog_status", "not_synced") for row in rows)
    missing = sum(count for status, count in counts.items() if status != "matched")
    return {
        "counts": dict(counts),
        "missing": missing,
        "warning": (
            f"Для {missing} товаров характеристики отсутствуют, не синхронизированы или сопоставление неоднозначно. Выполните ручную синхронизацию."
            if missing
            else None
        ),
    }


async def local_options(db, field, search=""):
    column = {
        "brand": CatalogProduct.brand,
        "manufacturer": CatalogProduct.manufacturer,
        "subcategory": CatalogProduct.subcategory,
    }[field]
    query = (
        select(column)
        .where(column.is_not(None), column != "")
        .distinct()
        .order_by(column)
    )
    if search.strip():
        needle = (
            search.strip().replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        )
        query = query.where(column.ilike("%" + needle + "%", escape="\\"))
    # Server-side cursor: avoid a duplicate full result list beside the response.
    result = await db.stream_scalars(query.execution_options(yield_per=250))
    try:
        return sorted([value async for value in result], key=str.casefold)
    finally:
        await result.close()


async def sync_status(db):
    state = await db.get(CatalogSyncState, 1)
    counts = dict(
        (
            await db.execute(
                select(CatalogLookup.status, func.count()).group_by(
                    CatalogLookup.status
                )
            )
        ).all()
    )
    stale = (
        await db.scalar(
            select(func.count())
            .select_from(CatalogLookup)
            .where(
                CatalogLookup.status == "matched",
                CatalogLookup.synced_at
                < datetime.now(timezone.utc) - timedelta(days=STALE_DAYS),
            )
        )
        or 0
    )
    sales_upper_id = int(await db.scalar(select(func.max(SaleItem.id))) or 0)
    return {
        "state": (
            {
                name: getattr(state, name)
                for name in (
                    "revision",
                    "cycle",
                    "cursor",
                    "upper_id",
                    "completed",
                    "status",
                    "processed",
                    "matched",
                    "not_found",
                    "ambiguous",
                    "errors",
                    "invalid",
                    "started_at",
                    "finished_at",
                    "last_success_at",
                    "last_full_success_at",
                    "error_code",
                )
            }
            if state
            else None
        ),
        "has_unscanned_sales": state is None or sales_upper_id > state.upper_id,
        "sales_upper_id": sales_upper_id,
        "lookup_counts": counts,
        "stale_records": stale,
        "stale_days": STALE_DAYS,
        "unsynchronized_records": counts.get("pending", 0),
        "coverage_scope": "discovered_identities; unscanned sales are not counted",
        "automatic_sync_enabled": False,
    }


async def remember_detail_image(db, row, image_url):
    """Keep photos fetched by explicit detail actions; lookup never fetches media."""
    if not image_url or image_url.startswith("data:") or len(image_url) > 8192:
        return
    lookup = await db.get(CatalogLookup, identity(row.get("code"), row.get("article")))
    if lookup is None or lookup.status != "matched" or lookup.product_id is None:
        return
    product = await db.get(CatalogProduct, lookup.product_id)
    if product is None or product.image_url == image_url:
        return
    product.image_url = image_url
    # Atomic increment shared by workers; no last-writer-wins revision update.
    from sqlalchemy import update

    await db.execute(
        update(CatalogSyncState)
        .where(CatalogSyncState.id == 1)
        .values(revision=CatalogSyncState.revision + 1)
    )
    await db.commit()
    from app.services.product_analytics_cache import invalidate_product_analytics_cache

    invalidate_product_analytics_cache()
