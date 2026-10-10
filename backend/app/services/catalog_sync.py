"""Manual, bounded full reconciliation by sales PK; no scheduler or timestamp cursor."""

import asyncio
import logging
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from sqlalchemy import func, select, text, update
from app.models import CatalogLookup, CatalogProduct, CatalogSyncState, SaleItem
from app.services.catalog_attributes import identity, normalize
from app.services.catalog_lookup_client import (
    BATCH_SIZE,
    LookupError,
    LookupInput,
    lookup_products,
)
from app.services.product_analytics_cache import invalidate_product_analytics_cache
from app.services.product_analytics_runtime import ensure_headroom
from pydantic import ValidationError

log = logging.getLogger(__name__)
SYNC_LOCK_ID = 734190117


class SyncBusy(RuntimeError):
    pass


@asynccontextmanager
async def sync_lock(engine):
    # Separate nonblocking lock: never hold the analytics lock across network I/O.
    async with engine.connect() as connection:
        acquired = await connection.scalar(
            text("SELECT pg_try_advisory_lock(:key)"), {"key": SYNC_LOCK_ID}
        )
        if not acquired:
            raise SyncBusy("catalog_sync_already_running")
        # Session advisory locks survive commit, avoiding an idle transaction
        # across network requests while retaining the dedicated connection.
        await connection.commit()
        try:
            yield
        finally:

            async def release():
                try:
                    await connection.execute(
                        text("SELECT pg_advisory_unlock(:key)"), {"key": SYNC_LOCK_ID}
                    )
                    await connection.commit()
                except BaseException:
                    await connection.invalidate()  # Never return a locked session to the pool.
                    raise

            cleanup = asyncio.create_task(release())
            cancelled = False
            while not cleanup.done():
                try:
                    await asyncio.shield(cleanup)
                except asyncio.CancelledError:
                    cancelled = True
            cleanup.result()
            if cancelled:
                raise asyncio.CancelledError


def now():
    return datetime.now(timezone.utc)


async def _state(db):
    state = await db.get(CatalogSyncState, 1)
    if state is None:
        state = CatalogSyncState(
            id=1,
            revision=0,
            cycle=0,
            cursor=0,
            upper_id=0,
            completed=True,
            status="idle",
            processed=0,
            matched=0,
            not_found=0,
            ambiguous=0,
            errors=0,
            invalid=0,
        )
        db.add(state)
        await db.flush()
    return state


async def save_results(db, pending, results, cycle):
    """Apply a fully validated successful batch; callers commit data + checkpoint."""
    ids = {
        result.product.product_id for result in results if result.status == "matched"
    }
    products = (
        {
            product.product_id: product
            for product in (
                await db.scalars(
                    select(CatalogProduct).where(CatalogProduct.product_id.in_(ids))
                )
            ).all()
        }
        if ids
        else {}
    )
    for result in results:
        if result.status != "matched":
            continue
        incoming = result.product
        product = products.get(incoming.product_id)
        if product is None:
            product = CatalogProduct(product_id=incoming.product_id)
            db.add(product)
            products[incoming.product_id] = product
        for field in (
            "code",
            "article",
            "manufacturer",
            "brand",
            "category",
            "subcategory",
            "category_id",
            "legacy_category",
            "material",
            "horeca",
        ):
            setattr(product, field, getattr(incoming, field))
        product.source_updated_at = incoming.updated_at
        product.synced_at = now()
    # Flush parents before assigning FK identities (also for unrelated ORM mappers).
    await db.flush()
    for lookup, result in zip(pending, results):
        if result.status == "matched":
            lookup.product_id = result.product.product_id
            lookup.synced_at = now()
        # Retain last-good data for inspection on not_found/ambiguous; the new
        # status prevents report use. These results are not deletion proof.
        lookup.matched_by = result.matched_by
        lookup.status = result.status
        lookup.checked_at = now()
        lookup.cycle = cycle
    await db.flush()


async def synchronize(
    session_factory,
    *,
    max_products=1000,
    max_scan_rows=10000,
    batch_size=100,
    stop_event=None,
    client=lookup_products
):
    """Caller MUST hold sync_lock. Limits apply per invocation; cursor persists."""
    if (
        not 1 <= batch_size <= BATCH_SIZE
        or not 1 <= max_products <= 10000
        or not 1 <= max_scan_rows <= 100000
    ):
        raise ValueError("invalid_sync_limits")
    stop_event = stop_event or asyncio.Event()
    started = time.perf_counter()
    handled = scanned = 0
    counters = {"matched": 0, "not_found": 0, "ambiguous": 0, "invalid": 0, "errors": 0}
    async with session_factory() as db:
        state = await _state(db)
        if state.completed:
            state.cycle += 1
            state.cursor = 0
            state.upper_id = int(await db.scalar(select(func.max(SaleItem.id))) or 0)
            state.completed = False
        state.status = "running"
        state.started_at = now()
        state.finished_at = None
        state.error_code = None
        await db.commit()
    try:
        while (
            handled < max_products
            and scanned < max_scan_rows
            and not stop_event.is_set()
        ):
            ensure_headroom()
            async with session_factory() as db:
                state = await _state(db)
                rows = (
                    await db.execute(
                        select(SaleItem.id, SaleItem.code, SaleItem.article)
                        .where(
                            SaleItem.id > state.cursor, SaleItem.id <= state.upper_id
                        )
                        .order_by(SaleItem.id)
                        .limit(min(batch_size, max_scan_rows - scanned))
                    )
                ).all()
                if not rows:
                    state.completed = True
                    state.status = "completed"
                    state.last_full_success_at = now()
                    await db.commit()
                    break
                keys = {identity(row.code, row.article) for row in rows}
                existing = {
                    item.identity: item
                    for item in (
                        await db.scalars(
                            select(CatalogLookup).where(
                                CatalogLookup.identity.in_(keys)
                            )
                        )
                    ).all()
                }
                pending, inputs, invalid = [], [], []
                checkpoint = state.cursor
                invalid_count = 0
                visited = 0
                for row in rows:
                    key = identity(row.code, row.article)
                    lookup = existing.get(key)
                    if lookup is not None and lookup.cycle == state.cycle:
                        checkpoint = row.id
                        visited += 1
                        continue
                    if lookup is not None and (lookup in pending or lookup in invalid):
                        checkpoint = row.id
                        visited += 1
                        continue
                    if handled + len(pending) + invalid_count >= max_products:
                        break
                    if lookup is None:
                        lookup = CatalogLookup(
                            identity=key,
                            code=normalize(row.code),
                            article=normalize(row.article),
                            status="pending",
                            cycle=0,
                        )
                        db.add(lookup)
                        existing[key] = lookup
                    raw = {
                        name: value.strip()
                        for name, value in (
                            ("code", row.code),
                            ("article", row.article),
                        )
                        if isinstance(value, str) and value.strip()
                    }
                    try:
                        request = LookupInput.model_validate(raw).model_dump(
                            exclude_none=True
                        )
                    except ValidationError:
                        invalid.append(lookup)
                        invalid_count += 1
                    else:
                        pending.append(lookup)
                        inputs.append(request)
                    checkpoint = row.id
                    visited += 1
                # Keep discovered pending identities, but NOT the checkpoint, if
                # HTTP fails. Previous product projections/statuses survive.
                await db.commit()
                results = await client(inputs) if inputs else []
                await save_results(db, pending, results, state.cycle)
                for lookup in invalid:
                    lookup.status = "invalid"
                    lookup.matched_by = None
                    lookup.cycle = state.cycle
                    lookup.checked_at = now()
                batch_counts = {
                    name: sum(result.status == name for result in results)
                    for name in ("matched", "not_found", "ambiguous")
                }
                state.cursor = checkpoint
                if checkpoint >= state.upper_id:
                    state.completed = True
                    state.status = "completed"
                    state.last_full_success_at = now()
                state.processed += len(results) + invalid_count
                state.invalid += invalid_count
                for name, value in batch_counts.items():
                    setattr(state, name, getattr(state, name) + value)
                    counters[name] += value
                counters["invalid"] += invalid_count
                if results or invalid_count:
                    await db.execute(
                        update(CatalogSyncState)
                        .where(CatalogSyncState.id == 1)
                        .values(revision=CatalogSyncState.revision + 1)
                        .execution_options(synchronize_session=False)
                    )
                    state.last_success_at = now()
                await db.commit()
                if results or invalid_count:
                    invalidate_product_analytics_cache()
                scanned += visited
                handled += len(results) + invalid_count
                log.info(
                    "catalog_sync batch processed=%s matched=%s not_found=%s ambiguous=%s invalid=%s scanned=%s",
                    len(results),
                    batch_counts["matched"],
                    batch_counts["not_found"],
                    batch_counts["ambiguous"],
                    invalid_count,
                    visited,
                )
                # Release all batch objects/session before the next page.
            if state.completed:
                break
            await asyncio.sleep(0)
        async with session_factory() as db:
            state = await _state(db)
            if not state.completed:
                state.status = "stopped" if stop_event.is_set() else "paused"
            state.finished_at = now()
            await db.commit()
    except BaseException as exc:
        code = (
            exc.code
            if isinstance(exc, LookupError)
            else (
                "cancelled"
                if isinstance(exc, asyncio.CancelledError)
                else (
                    "memory_headroom"
                    if getattr(exc, "status_code", None) == 503
                    else "sync_error"
                )
            )
        )

        async def record_error():
            async with session_factory() as db:
                state = await _state(db)
                state.status = "stopped" if code == "cancelled" else "failed"
                state.error_code = code
                state.errors += 1
                state.finished_at = now()
                await db.commit()

        await asyncio.shield(record_error())
        counters["errors"] += 1
        log.warning(
            "catalog_sync stopped code=%s processed=%s scanned=%s",
            code,
            handled,
            scanned,
        )
        raise
    finally:
        log.info(
            "catalog_sync finished processed=%s scanned=%s matched=%s not_found=%s ambiguous=%s invalid=%s errors=%s elapsed_ms=%.3f",
            handled,
            scanned,
            counters["matched"],
            counters["not_found"],
            counters["ambiguous"],
            counters["invalid"],
            counters["errors"],
            (time.perf_counter() - started) * 1000,
        )
    return {"processed": handled, "scanned": scanned, **counters}
