"""Bounded PK scan, SQL monthly aggregates, per-product updates and durable cursors."""

import asyncio
from contextlib import asynccontextmanager
from datetime import date, datetime, timezone
from decimal import Decimal
import time
from sqlalchemy import (
    and_,
    case,
    delete,
    func,
    literal,
    or_,
    select,
    text,
    tuple_,
    update,
)
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from app.models import (
    ImportBatch,
    CatalogLookup,
    Sale,
    SaleItem,
    SeasonalityMonth,
    SeasonalityStoreMonth,
    SeasonalityProfile,
    SeasonalityState,
)
from app.services.product_analytics import product_key
from app.services.catalog_attributes import identity as catalog_identity
from app.services.product_analytics_runtime import (
    heavy_operation,
    available_memory,
    ensure_headroom,
    diagnostics,
)
from app.services.seasonality import ALGORITHM_VERSION, calculate_history, identity
from app.services.seasonality_storage import state

SOURCE_LOCK = 734190220
WORKER_LOCK = 734190221
CATALOG_LOCK = 734190117


class SeasonalityBusy(RuntimeError):
    pass


@asynccontextmanager
async def advisory(engine, *, key=SOURCE_LOCK, shared=False, wait=False):
    if engine.dialect.name != "postgresql":
        yield None  # Isolated SQLite tests only.
        return
    async with engine.connect() as connection:
        function = ("pg_advisory_lock" if wait else "pg_try_advisory_lock") + (
            "_shared" if shared else ""
        )
        acquired = await connection.scalar(
            text(f"SELECT {function}(:key)"), {"key": key}
        )
        if not wait and not acquired:
            raise SeasonalityBusy("resource_busy")
        await connection.commit()
        try:
            yield connection
        finally:

            async def release():
                try:
                    await connection.execute(
                        text(
                            "SELECT pg_advisory_unlock"
                            + ("_shared" if shared else "")
                            + "(:key)"
                        ),
                        {"key": key},
                    )
                    await connection.commit()
                except BaseException:
                    await connection.invalidate()
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


async def verify(connection, key):
    if connection is None:
        return
    owned = await connection.scalar(
        text(
            "SELECT EXISTS (SELECT 1 FROM pg_locks WHERE locktype='advisory' AND pid=pg_backend_pid() AND classid=0 AND objid=:key AND objsubid=1 AND granted)"
        ),
        {"key": key},
    )
    if not owned:
        raise SeasonalityBusy("lost_lock")
    await connection.commit()


def insertion(db, model):
    return (
        pg_insert(model)
        if db.bind.dialect.name == "postgresql"
        else sqlite_insert(model)
    )


async def limits(db):
    if db.bind.dialect.name == "postgresql":
        await db.execute(text("SET LOCAL statement_timeout = '10s'"))
        await db.execute(text("SET LOCAL lock_timeout = '1s'"))


async def _begin(db, current, today):
    upper = int(await db.scalar(select(func.max(SaleItem.id))) or 0)
    first_year = 0  # v2: all-history scope marker, not a calendar boundary
    rebuild = (
        current.rebuild_required
        or current.algorithm_version != ALGORITHM_VERSION
        or current.first_year != first_year
        or upper < current.source_cursor
        or not current.active_generation
    )
    if rebuild:
        current.generation = (
            max(
                current.generation,
                current.active_generation,
                int(
                    await db.scalar(select(func.max(SeasonalityMonth.generation))) or 0
                ),
                int(
                    await db.scalar(select(func.max(SeasonalityProfile.generation)))
                    or 0
                ),
            )
            + 1
        )
        current.cursor = 0
        current.coverage = (today.year * 12 + today.month) << 12
        current.calculated = 0
    else:
        current.generation = current.active_generation
        current.cursor = current.source_cursor
    current.coverage = ((today.year * 12 + today.month) << 12) | (
        current.coverage & 4095
    )
    current.algorithm_version = ALGORITHM_VERSION
    current.first_year = first_year
    current.upper_id = upper
    current.job_revision = current.source_revision
    current.rebuild_required = False
    current.phase = "scan"
    current.error_code = None


async def scan_batch(db, current, size):
    ids = (
        await db.scalars(
            select(SaleItem.id)
            .where(SaleItem.id > current.cursor, SaleItem.id <= current.upper_id)
            .order_by(SaleItem.id)
            .limit(size)
        )
    ).all()
    if not ids:
        current.cursor = current.upper_id
        current.phase = "calculate"
        return 0
    finish = ids[-1]
    # Aggregate all recorded years in this bounded PK batch; no detailed sales
    # history is returned to Python.
    year = func.extract("year", Sale.sale_date).label("year")
    month = func.extract("month", Sale.sale_date).label("month")
    # Group raw names too: Python strip/casefold differs from SQL trim/lower
    # for tabs/Unicode. Hash merging inside this bounded batch is authoritative.
    named = SaleItem.name.label("name")
    store = func.coalesce(Sale.department, "Без подразделения").label("store")
    query = (
        select(
            store,
            SaleItem.code,
            SaleItem.article,
            named,
            year,
            month,
            func.sum(SaleItem.quantity).label("units"),
            func.sum(case((SaleItem.quantity > 0, 1), else_=0)).label("positive_lines"),
            func.max(
                case(
                    (
                        or_(
                            ImportBatch.id.is_(None),
                            ImportBatch.status == "failed",
                            ImportBatch.error_rows > 0,
                        ),
                        1,
                    ),
                    else_=0,
                )
            ).label("incomplete_source"),
            func.min(Sale.sale_date).label("first_sale"),
            func.max(Sale.sale_date).label("last_sale"),
        )
        .select_from(SaleItem)
        .join(Sale, Sale.id == SaleItem.sale_id)
        .outerjoin(ImportBatch, ImportBatch.id == Sale.import_id)
        .where(SaleItem.id > current.cursor, SaleItem.id <= finish)
        .group_by(store, SaleItem.code, SaleItem.article, named, year, month)
    )
    records = (await db.execute(query)).all()
    products = {}
    names = {}
    monthly = {}
    store_monthly = {}
    old_coverage = current.coverage
    for item in records:
        key = product_key(item.article, item.code, item.name)
        digest = identity(key)
        products[digest] = key
        if key.startswith("article:"):
            fingerprint = identity((item.name or "").strip().casefold())
            low, high = names.get(digest, (fingerprint, fingerprint))
            names[digest] = (min(low, fingerprint), max(high, fingerprint))
        current.coverage |= 1 << (int(item.month) - 1)
        pk = (digest, int(item.year), int(item.month))
        if pk not in monthly:
            monthly[pk] = {
                "generation": current.generation,
                "identity": digest,
                "year": int(item.year),
                "month": int(item.month),
                "units": Decimal(0),
                "positive_lines": 0,
                "incomplete_source": False,
                "first_sale": item.first_sale,
                "last_sale": item.last_sale,
            }
        target = monthly[pk]
        target["incomplete_source"] |= bool(item.incomplete_source)
        target["units"] += item.units
        target["positive_lines"] += int(item.positive_lines)
        target["first_sale"] = min(target["first_sale"], item.first_sale)
        target["last_sale"] = max(target["last_sale"], item.last_sale)
        store_pk = (*pk, item.store)
        if store_pk not in store_monthly:
            store_monthly[store_pk] = {
                "generation": current.generation,
                "identity": digest,
                "year": int(item.year),
                "month": int(item.month),
                "store": item.store,
                "units": Decimal(0),
                "positive_lines": 0,
                "incomplete_source": False,
                "first_sale": item.first_sale,
                "last_sale": item.last_sale,
            }
        by_store = store_monthly[store_pk]
        by_store["units"] += item.units
        by_store["positive_lines"] += int(item.positive_lines)
        by_store["incomplete_source"] |= bool(item.incomplete_source)
        by_store["first_sale"] = min(by_store["first_sale"], item.first_sale)
        by_store["last_sale"] = max(by_store["last_sale"], item.last_sale)
    # Each incoming id occurs once. Month sums and source cursor commit together.
    affected = {digest for digest, _, _ in monthly}
    product_values = list(products.items())
    for offset in range(0, len(product_values), 100):
        group = product_values[offset : offset + 100]
        statement = insertion(db, SeasonalityProfile).values(
            [
                {
                    "generation": current.generation,
                    "identity": digest,
                    "product_key": key,
                    "name_min": names.get(digest, (None, None))[0],
                    "name_max": names.get(digest, (None, None))[1],
                    "coverage": -1,
                    "dirty": True,
                }
                for digest, key in group
            ]
        )
        low = case(
            (SeasonalityProfile.name_min.is_(None), statement.excluded.name_min),
            (statement.excluded.name_min.is_(None), SeasonalityProfile.name_min),
            (
                statement.excluded.name_min < SeasonalityProfile.name_min,
                statement.excluded.name_min,
            ),
            else_=SeasonalityProfile.name_min,
        )
        high = case(
            (SeasonalityProfile.name_max.is_(None), statement.excluded.name_max),
            (statement.excluded.name_max.is_(None), SeasonalityProfile.name_max),
            (
                statement.excluded.name_max > SeasonalityProfile.name_max,
                statement.excluded.name_max,
            ),
            else_=SeasonalityProfile.name_max,
        )
        changed_names = or_(
            and_(
                SeasonalityProfile.name_min.is_(None),
                statement.excluded.name_min.is_not(None),
            ),
            func.coalesce(low != SeasonalityProfile.name_min, False),
            func.coalesce(high != SeasonalityProfile.name_max, False),
        )
        await db.execute(
            statement.on_conflict_do_update(
                index_elements=["generation", "identity"],
                set_={
                    "name_min": low,
                    "name_max": high,
                    "dirty": or_(SeasonalityProfile.dirty, changed_names),
                },
            )
        )
        dirty = [digest for digest, _ in group if digest in affected]
        if dirty:
            await db.execute(
                update(SeasonalityProfile)
                .where(
                    SeasonalityProfile.generation == current.generation,
                    SeasonalityProfile.identity.in_(dirty),
                )
                .values(dirty=True)
            )
    month_values = list(monthly.values())
    for offset in range(0, len(month_values), 100):
        statement = insertion(db, SeasonalityMonth).values(
            month_values[offset : offset + 100]
        )
        await db.execute(
            statement.on_conflict_do_update(
                index_elements=["generation", "identity", "year", "month"],
                set_={
                    "units": SeasonalityMonth.units + statement.excluded.units,
                    "incomplete_source": or_(
                        SeasonalityMonth.incomplete_source,
                        statement.excluded.incomplete_source,
                    ),
                    "positive_lines": SeasonalityMonth.positive_lines
                    + statement.excluded.positive_lines,
                    "first_sale": case(
                        (
                            SeasonalityMonth.first_sale < statement.excluded.first_sale,
                            SeasonalityMonth.first_sale,
                        ),
                        else_=statement.excluded.first_sale,
                    ),
                    "last_sale": case(
                        (
                            SeasonalityMonth.last_sale > statement.excluded.last_sale,
                            SeasonalityMonth.last_sale,
                        ),
                        else_=statement.excluded.last_sale,
                    ),
                },
            )
        )
    month_values = list(store_monthly.values())
    for offset in range(0, len(month_values), 100):
        statement = insertion(db, SeasonalityStoreMonth).values(
            month_values[offset : offset + 100]
        )
        await db.execute(
            statement.on_conflict_do_update(
                index_elements=["generation", "identity", "year", "month", "store"],
                set_={
                    "units": SeasonalityStoreMonth.units + statement.excluded.units,
                    "incomplete_source": or_(
                        SeasonalityStoreMonth.incomplete_source,
                        statement.excluded.incomplete_source,
                    ),
                    "positive_lines": SeasonalityStoreMonth.positive_lines
                    + statement.excluded.positive_lines,
                    "first_sale": case(
                        (
                            SeasonalityStoreMonth.first_sale
                            < statement.excluded.first_sale,
                            SeasonalityStoreMonth.first_sale,
                        ),
                        else_=statement.excluded.first_sale,
                    ),
                    "last_sale": case(
                        (
                            SeasonalityStoreMonth.last_sale
                            > statement.excluded.last_sale,
                            SeasonalityStoreMonth.last_sale,
                        ),
                        else_=statement.excluded.last_sale,
                    ),
                },
            )
        )
    current.cursor = finish
    return len(ids)


async def calculate_batch(db, current, size, today):
    query = (
        select(SeasonalityProfile)
        .where(
            SeasonalityProfile.generation == current.generation,
            or_(
                SeasonalityProfile.dirty.is_(True),
                SeasonalityProfile.coverage != current.coverage,
            ),
        )
        .order_by(SeasonalityProfile.identity)
        .limit(size)
    )
    profiles = (await db.scalars(query)).all()
    if not profiles:
        current.active_generation = current.generation
        current.source_cursor = current.upper_id
        current.last_success = datetime.now(timezone.utc)
        current.last_checked = today
        current.phase = "cleanup"
        return 0
    keys = [p.identity for p in profiles]
    article_keys = {
        catalog_identity(None, p.product_key.split(":", 1)[1]): p.identity
        for p in profiles
        if p.product_key.startswith("article:")
    }
    ambiguous = set()
    if article_keys:
        lookups = (
            (
                await db.execute(
                    select(CatalogLookup.identity).where(
                        CatalogLookup.identity.in_(article_keys),
                        CatalogLookup.status == "ambiguous",
                    )
                )
            )
            .scalars()
            .all()
        )
        ambiguous = {article_keys[key] for key in lookups}
    for profile in profiles:
        ensure_headroom()
        items = []
        cursor = await db.stream_scalars(
            select(SeasonalityMonth)
            .where(
                SeasonalityMonth.generation == current.generation,
                SeasonalityMonth.identity == profile.identity,
            )
            .order_by(SeasonalityMonth.year, SeasonalityMonth.month)
            .execution_options(yield_per=250)
        )
        try:
            async for record in cursor:
                if len(items) % 250 == 0:
                    ensure_headroom()
                items.append(
                    {
                        "year": record.year,
                        "month": record.month,
                        "units": record.units,
                        "positive_lines": record.positive_lines,
                        "incomplete_source": record.incomplete_source,
                    }
                )
        finally:
            await cursor.close()
        profile.data = calculate_history(items, today)
        if profile.product_key.startswith("unknown:") or (
            profile.product_key.startswith("article:")
            and (profile.name_min != profile.name_max or profile.identity in ambiguous)
        ):
            profile.data = calculate_history([], today)
            profile.data["reason"] = (
                "Нет надёжного кода или артикул неоднозначен: сезонная история не сопоставляется случайным выбором товара."
            )
        profile.calculated_at = datetime.now(timezone.utc)
        profile.coverage = current.coverage
        profile.dirty = False
    current.calculated += len(profiles)
    return len(profiles)


async def cleanup_batch(db, current, size):
    store_keys = (
        await db.execute(
            select(
                SeasonalityStoreMonth.generation,
                SeasonalityStoreMonth.identity,
                SeasonalityStoreMonth.year,
                SeasonalityStoreMonth.month,
                SeasonalityStoreMonth.store,
            )
            .where(SeasonalityStoreMonth.generation < current.active_generation - 1)
            .limit(size)
        )
    ).all()
    if store_keys:
        await db.execute(
            delete(SeasonalityStoreMonth).where(
                tuple_(
                    SeasonalityStoreMonth.generation,
                    SeasonalityStoreMonth.identity,
                    SeasonalityStoreMonth.year,
                    SeasonalityStoreMonth.month,
                    SeasonalityStoreMonth.store,
                ).in_([tuple(row) for row in store_keys])
            )
        )
        return len(store_keys)
    # Keep active and immediately previous generation; remove older data in PK
    # batches, never a massive deletion or TRUNCATE.
    rows = (
        await db.execute(
            select(
                SeasonalityMonth.generation,
                SeasonalityMonth.identity,
                SeasonalityMonth.year,
                SeasonalityMonth.month,
            )
            .where(SeasonalityMonth.generation < current.active_generation - 1)
            .limit(size)
        )
    ).all()
    if rows:
        await db.execute(
            delete(SeasonalityMonth).where(
                tuple_(
                    SeasonalityMonth.generation,
                    SeasonalityMonth.identity,
                    SeasonalityMonth.year,
                    SeasonalityMonth.month,
                ).in_([tuple(row) for row in rows])
            )
        )
        return len(rows)
    profiles = (
        await db.execute(
            select(SeasonalityProfile.generation, SeasonalityProfile.identity)
            .where(SeasonalityProfile.generation < current.active_generation - 1)
            .limit(size)
        )
    ).all()
    if profiles:
        await db.execute(
            delete(SeasonalityProfile).where(
                tuple_(SeasonalityProfile.generation, SeasonalityProfile.identity).in_(
                    [tuple(row) for row in profiles]
                )
            )
        )
        return len(profiles)
    current.phase = "idle"
    return 0


async def run_batch(factory, engine, today, *, scan_size=1000, profile_size=100):
    if not 1 <= scan_size <= 2000 or not 1 <= profile_size <= 100:
        raise ValueError("invalid_limits")
    started = time.perf_counter()
    if available_memory() is None:
        raise SeasonalityBusy("memory_metrics_unavailable")
    async with heavy_operation("seasonality_batch"), advisory(
        engine
    ) as source_lock, advisory(engine, key=CATALOG_LOCK) as catalog_lock:
        ensure_headroom()
        async with factory() as db:
            await limits(db)
            if await db.scalar(
                select(ImportBatch.id)
                .where(ImportBatch.status.in_(["queued", "processing"]))
                .limit(1)
            ):
                raise SeasonalityBusy("import_pending")
            current = await state(db, lock=True)
            if (
                current.phase == "idle"
                or current.rebuild_required
                or current.first_year != 0
                or current.algorithm_version != ALGORITHM_VERSION
            ):
                await _begin(db, current, today)
            current.coverage = ((today.year * 12 + today.month) << 12) | (
                current.coverage & 4095
            )
            # Imports/deletions can happen between batches. Never publish before
            # catching the new source tail; deletion triggers a new generation.
            if current.source_revision != current.job_revision:
                current.upper_id = int(
                    await db.scalar(select(func.max(SaleItem.id))) or 0
                )
                current.job_revision = current.source_revision
                if current.cursor < current.upper_id:
                    current.phase = "scan"
            if current.phase in ("scan", "scan_all"):
                count = await scan_batch(db, current, scan_size)
            elif current.phase == "calculate":
                count = await calculate_batch(db, current, profile_size, today)
            else:
                count = await cleanup_batch(db, current, scan_size)
            current.error_code = None
            current.failure_count = 0
            await verify(source_lock, SOURCE_LOCK)
            await verify(catalog_lock, CATALOG_LOCK)
            await db.commit()
            result = {
                "phase": current.phase,
                "cursor": current.cursor,
                "upper_id": current.upper_id,
                "processed": count,
                "calculated": current.calculated,
                "active_generation": current.active_generation,
            }
        diagnostics(
            "seasonality_batch",
            elapsed_ms=(time.perf_counter() - started) * 1000,
            processed=count,
        )
        return result
