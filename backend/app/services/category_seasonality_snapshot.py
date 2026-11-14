"""Durable weekly category snapshots; interactive reads never access sales/catalog/APIs."""

import asyncio
import calendar
import logging
import time
from collections import defaultdict
from contextlib import AsyncExitStack
from datetime import date, datetime, timedelta, timezone
from uuid import uuid4
from zoneinfo import ZoneInfo
from sqlalchemy import select, func, text, insert, delete, update, exists, and_
from sqlalchemy.ext.asyncio import async_sessionmaker
from app.models import (
    Sale,
    SaleItem,
    CatalogLookup,
    CatalogProduct,
    ImportBatch,
    CategorySeasonSnapshot as Snapshot,
    CategorySeasonState as State,
    CategorySeasonCategory as Category,
    CategorySeasonClient as Client,
    CategorySeasonMembership as Membership,
    CategorySeasonFact as Fact,
    CategorySeasonCoverage as Coverage,
)
from app.models.import_control import ImportReceipt
from app.services.category_seasonality import (
    branch,
    directory,
    coverage_method,
    seasonal_profile,
    BATCH,
)
from app.services.catalog_attributes import identity
from app.services.source_control import (
    advisory,
    verify,
    ResourceBusy,
    SOURCE_LOCK,
    WORKER_LOCK,
    CATALOG_LOCK,
)
from app.services.product_analytics_runtime import (
    heavy_operation,
    ensure_headroom,
    memory_snapshot,
)
from app.services.report_execution import REPORT_SETTINGS
from app.services import clients_vr

MOSCOW = ZoneInfo("Europe/Moscow")
log = logging.getLogger(__name__)


def now():
    return datetime.now(timezone.utc)


def previous_slot(value):
    local = value.astimezone(MOSCOW)
    slot = (local - timedelta(days=(local.weekday() - 5) % 7)).replace(
        hour=19, minute=0, second=0, microsecond=0
    )
    return slot if slot <= local else slot - timedelta(days=7)


def next_slot(value):
    return previous_slot(value) + timedelta(days=7)


def cutoff(value):
    return value.astimezone(MOSCOW).date().replace(day=1)


async def settings_sql(db):
    for statement in REPORT_SETTINGS:
        await db.execute(text(statement))
    await db.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ"))


async def prepare_pairs(db, end, groups, snapshot_id):
    name = "cs_pairs_" + uuid4().hex
    await db.execute(
        text(
            f"CREATE TEMP TABLE {name}(code text,article text,category_id integer,brand text,manufacturer text) ON COMMIT DROP"
        )
    )
    codes, articles = func.coalesce(SaleItem.code, ""), func.coalesce(
        SaleItem.article, ""
    )
    stream = await db.stream(
        select(codes.label("code"), articles.label("article"))
        .select_from(SaleItem)
        .join(Sale)
        .where(Sale.sale_date < end)
        .distinct()
        .execution_options(yield_per=BATCH)
    )
    ids = {k: i + 1 for i, k in enumerate(groups)}
    try:
        async for batch in stream.partitions(BATCH):
            ensure_headroom()
            keyed = [(r, identity(r.code, r.article)) for r in batch]
            found = (
                await db.execute(
                    select(
                        CatalogLookup.identity,
                        CatalogProduct.category_id,
                        CatalogProduct.category,
                        CatalogProduct.subcategory,
                        CatalogProduct.legacy_category,
                        CatalogProduct.brand,
                        CatalogProduct.manufacturer,
                    )
                    .join(
                        CatalogProduct,
                        CatalogProduct.product_id == CatalogLookup.product_id,
                    )
                    .where(
                        CatalogLookup.identity.in_({k for r, k in keyed}),
                        CatalogLookup.status == "matched",
                    )
                )
            ).all()
            attrs = {r.identity: r for r in found}
            values = []
            for r, digest in keyed:
                p = attrs.get(digest)
                b = (
                    branch(p.category_id, p.category, p.subcategory, p.legacy_category)
                    if p
                    else branch(None, None, None, None)
                )
                values.append(
                    dict(
                        code=r.code,
                        article=r.article,
                        category_id=ids[b["key"]],
                        brand=p.brand or "" if p else "",
                        manufacturer=p.manufacturer or "" if p else "",
                    )
                )
            await db.execute(
                text(
                    f"INSERT INTO {name}(code,article,category_id,brand,manufacturer) VALUES(:code,:article,:category_id,:brand,:manufacturer)"
                ),
                values,
            )
    finally:
        await stream.close()
    await db.execute(text(f"ANALYZE {name}"))
    await db.execute(
        insert(Category),
        [dict(snapshot_id=snapshot_id, id=ids[k], **b) for k, b in groups.items()],
    )
    return name


async def freeze_clients(db, sid, end):
    await db.execute(
        text(
            """INSERT INTO category_season_clients(snapshot_id,id,name)
      SELECT :sid,row_number() OVER (ORDER BY name),name FROM
      (SELECT DISTINCT lower(trim(coalesce(client,''))) AS name FROM sales WHERE sale_date<:end) c"""
        ),
        dict(sid=sid, end=end),
    )
    result = {}
    for kind, labels, loader in [
        (
            "manager",
            await clients_vr.get_snapshot_filter_values("manager"),
            clients_vr.get_manager_clients,
        ),
        (
            "buyer_type",
            await clients_vr.get_snapshot_filter_values("buyer_type"),
            clients_vr.get_buyer_type_clients,
        ),
    ]:
        result[kind] = list(dict.fromkeys(labels))
        for value in result[kind]:
            ensure_headroom()
            names = await clients_vr.get_snapshot_filter_clients(kind, value, db)
            # Match existing SQL client_names_condition (lower + outer trim),
            # not a newly invented interpretation or product/client cross join.
            for offset in range(0, len(names), BATCH):
                normalized = list(
                    {
                        n.strip().lower()
                        for n in names[offset : offset + BATCH]
                        if n and n.strip()
                    }
                )
                if normalized:
                    await db.execute(
                        text(
                            """INSERT INTO category_season_memberships(snapshot_id,client_id,kind,value)
                      SELECT snapshot_id,id,:kind,:value FROM category_season_clients
                      WHERE snapshot_id=:sid AND name=ANY(CAST(:names AS text[]))
                      ON CONFLICT DO NOTHING"""
                        ),
                        dict(sid=sid, kind=kind, value=value, names=normalized),
                    )
    return result


async def materialize(db, sid, end):
    groups, nodes = await directory(db)
    pairs = await prepare_pairs(db, end, groups, sid)
    labels = await freeze_clients(db, sid, end)
    ensure_headroom()
    await db.execute(
        text(
            f"""INSERT INTO category_season_facts(snapshot_id,category_id,client_id,store,brand,manufacturer,year,month,units,revenue,lines)
       SELECT :sid,p.category_id,c.id,s.department,p.brand,p.manufacturer,extract(year from s.sale_date)::int,extract(month from s.sale_date)::int,
       sum(i.quantity),coalesce(sum(i.quantity*i.actual_price),0),count(*)
       FROM sale_items i JOIN sales s ON s.id=i.sale_id
       JOIN {pairs} p ON p.code=coalesce(i.code,'') AND p.article=coalesce(i.article,'')
       JOIN category_season_clients c ON c.snapshot_id=:sid AND c.name=lower(trim(coalesce(s.client,'')))
       WHERE s.sale_date<:end
       GROUP BY p.category_id,c.id,s.department,p.brand,p.manufacturer,extract(year from s.sale_date),extract(month from s.sale_date)"""
        ),
        dict(sid=sid, end=end),
    )
    # Coverage is independent of all product/client filters. O(store/month) data.
    observed = (
        await db.execute(
            select(
                Sale.department,
                func.extract("year", Sale.sale_date),
                func.extract("month", Sale.sale_date),
                func.count(func.distinct(Sale.sale_date)),
            )
            .where(Sale.sale_date < end)
            .group_by(
                Sale.department,
                func.extract("year", Sale.sale_date),
                func.extract("month", Sale.sale_date),
            )
        )
    ).all()
    counts = {(s, int(y), int(m)): int(n) for s, y, m, n in observed}
    starts = {}
    for s, y, m in counts:
        starts[s] = min(starts.get(s, date(y, m, 1)), date(y, m, 1))
    bits = defaultdict(int)
    receipts = await db.stream(
        select(
            ImportReceipt.period_start,
            ImportReceipt.period_end,
            ImportReceipt.departments,
        )
        .join(ImportBatch, ImportBatch.id == ImportReceipt.import_id)
        .where(
            ImportReceipt.status == "success",
            ImportReceipt.period_method == "DECLARED_REPORT_PERIOD",
            ImportBatch.status == "completed",
            ImportBatch.error_rows == 0,
            ImportBatch.file_size > 0,
            ImportReceipt.period_start < end,
        )
        .execution_options(yield_per=BATCH)
    )
    try:
        async for lo, hi, stores in receipts:
            ensure_headroom()
            hi = min(hi, end - timedelta(days=1))
            cursor = lo.replace(day=1)
            for store in stores:
                starts[store] = min(starts.get(store, cursor), cursor)
            while cursor <= hi:
                last = date(
                    cursor.year,
                    cursor.month,
                    calendar.monthrange(cursor.year, cursor.month)[1],
                )
                a, b = max(lo, cursor), min(hi, last)
                mask = ((1 << ((b - a).days + 1)) - 1) << (a.day - 1)
                for store in stores:
                    bits[(store, cursor.year, cursor.month)] |= mask
                cursor = last + timedelta(days=1)
    finally:
        await receipts.close()
    coverage = []
    for store, first in starts.items():
        cursor = first
        while cursor < end:
            days = calendar.monthrange(cursor.year, cursor.month)[1]
            key = (store, cursor.year, cursor.month)
            n = counts.get(key, 0)
            coverage.append(
                dict(
                    snapshot_id=sid,
                    store=store,
                    year=cursor.year,
                    month=cursor.month,
                    recorded_days=n,
                    calendar_days=days,
                    method=coverage_method(n, days, bits[key] == (1 << days) - 1),
                )
            )
            cursor = date(cursor.year, cursor.month, days) + timedelta(days=1)
            if len(coverage) >= BATCH:
                await db.execute(insert(Coverage), coverage)
                coverage = []
                ensure_headroom()
    if coverage:
        await db.execute(insert(Coverage), coverage)
    stores = sorted(starts)
    years = sorted({y for s, y, m in counts}, reverse=True)
    brands = list(
        (
            await db.scalars(
                select(Fact.brand)
                .where(Fact.snapshot_id == sid, Fact.brand != "")
                .distinct()
                .order_by(Fact.brand)
            )
        ).all()
    )
    makers = list(
        (
            await db.scalars(
                select(Fact.manufacturer)
                .where(Fact.snapshot_id == sid, Fact.manufacturer != "")
                .distinct()
                .order_by(Fact.manufacturer)
            )
        ).all()
    )
    total = await db.scalar(
        select(func.count()).select_from(Fact).where(Fact.snapshot_id == sid)
    )
    eligible = (
        await db.execute(
            select(Coverage.year, Coverage.month)
            .where(Coverage.snapshot_id == sid, Coverage.method != "excluded")
            .order_by(Coverage.year.desc(), Coverage.month.desc())
            .limit(1)
        )
    ).first()
    last_month = (
        date(
            eligible[0], eligible[1], calendar.monthrange(eligible[0], eligible[1])[1]
        ).isoformat()
        if eligible
        else None
    )
    return dict(
        last_month=last_month,
        nodes=nodes,
        years=years,
        stores=stores,
        brand=brands,
        manufacturer=makers,
        managers=labels["manager"],
        buyer_types=labels["buyer_type"],
    ), int(total)


async def rebuild(engine, *, manual=False, clock=None):
    clock = clock or now()
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with AsyncExitStack() as stack:
        try:
            owner = await stack.enter_async_context(advisory(engine, key=WORKER_LOCK))
            # Updaters and imports share these exact reviewed lock IDs.
            source_owner = await stack.enter_async_context(
                advisory(engine, key=SOURCE_LOCK)
            )
            catalog_owner = await stack.enter_async_context(
                advisory(engine, key=CATALOG_LOCK, shared=True)
            )
        except ResourceBusy:
            return {"status": "busy"}
        async with sessions() as db:
            state = await db.get(State, 1)
            if state is None:
                raise RuntimeError("snapshot_control_missing")
            await db.execute(
                update(Snapshot)
                .where(Snapshot.status == "building")
                .values(status="failed", error_code="worker_interrupted")
            )
            await db.commit()
            if not manual and (not state or not state.active_id):
                return {"status": "uninitialized"}
            if not manual and state.next_due and clock < state.next_due:
                return {"status": "current"}
            slot = previous_slot(clock).date()
            if not manual and await db.scalar(
                select(Snapshot.id).where(Snapshot.week == slot)
            ):
                return {"status": "already_attempted"}
            # A prior owner died; there cannot be a live builder after owning lock.
            await db.execute(
                update(Snapshot)
                .where(Snapshot.status == "building")
                .values(status="failed", error_code="worker_interrupted")
            )
            sid = str(uuid4())
            row = Snapshot(
                id=sid,
                status="building",
                week=None if manual else slot,
                started_at=clock,
                through=cutoff(clock) - timedelta(days=1),
                options={},
                fact_rows=0,
            )
            db.add(row)
            if not manual:
                state.next_due = next_slot(clock)
            await db.commit()
        started = time.perf_counter()
        try:
            async with heavy_operation("category_season_weekly"):
                async with sessions() as db:
                    await settings_sql(db)
                    options, count = await materialize(db, sid, cutoff(clock))
                    await verify(owner, WORKER_LOCK)
                    await verify(source_owner, SOURCE_LOCK)
                    await verify(catalog_owner, CATALOG_LOCK)
                    await db.execute(
                        update(Snapshot)
                        .where(Snapshot.id == sid)
                        .values(
                            status="published",
                            published_at=now(),
                            options=options,
                            fact_rows=count,
                            duration_ms=int((time.perf_counter() - started) * 1000),
                        )
                    )
                    await db.execute(
                        update(State)
                        .where(State.id == 1)
                        .values(active_id=sid, next_due=next_slot(clock))
                    )
                    await db.commit()  # All facts + the active pointer publish together.
            # Keep active and preceding published generation for rollback/readers.
            async with sessions() as db:
                for statement in REPORT_SETTINGS:
                    await db.execute(text(statement))
                keep = list(
                    (
                        await db.scalars(
                            select(Snapshot.id)
                            .where(Snapshot.status == "published")
                            .order_by(Snapshot.published_at.desc())
                            .limit(2)
                        )
                    ).all()
                )
                # Do not delete attempt rows: their unique week is the restart guard.
                old = list(
                    (
                        await db.scalars(
                            select(Snapshot.id).where(
                                Snapshot.status == "published", Snapshot.id.not_in(keep)
                            )
                        )
                    ).all()
                )
                for model in (Fact, Membership, Client, Coverage, Category):
                    if old:
                        await db.execute(
                            delete(model).where(model.snapshot_id.in_(old))
                        )
                if old:
                    await db.execute(
                        update(Snapshot)
                        .where(Snapshot.id.in_(old))
                        .values(status="failed", error_code="retired", options={})
                    )
                await db.commit()
            log.info(
                "category_season_snapshot published rows=%d duration_ms=%.3f memory=%s",
                count,
                (time.perf_counter() - started) * 1000,
                memory_snapshot(),
            )
            return dict(status="published", id=sid, rows=count)
        except BaseException as exc:
            # Rollback of data session prevents partial facts. Cancellation is
            # recorded with a fresh session; publication pointer remains intact.
            async def record():
                async with sessions() as db:
                    await db.execute(
                        update(Snapshot)
                        .where(Snapshot.id == sid, Snapshot.status == "building")
                        .values(
                            status="failed",
                            error_code=(
                                "cancelled"
                                if isinstance(exc, asyncio.CancelledError)
                                else type(exc).__name__
                            ),
                        )
                    )
                    await db.commit()

            task = asyncio.create_task(record())
            while not task.done():
                try:
                    await asyncio.shield(task)
                except asyncio.CancelledError:
                    pass
            task.result()
            raise


async def tick(engine):
    return await rebuild(engine)


async def metadata(db, clock=None):
    clock = clock or now()
    state = await db.get(State, 1)
    if state is None:
        from fastapi import HTTPException

        raise HTTPException(
            503, "Состояние снимка недоступно. Требуется проверка установки."
        )
    active = (
        await db.get(Snapshot, state.active_id) if state and state.active_id else None
    )
    last = await db.scalar(
        select(Snapshot).order_by(Snapshot.started_at.desc()).limit(1)
    )
    return active, dict(
        state="ready" if active else "uninitialized",
        snapshot_id=active.id if active else None,
        last_success=active.published_at if active else None,
        through=active.options.get("last_month") if active else None,
        next_update=next_slot(clock),
        updating=bool(last and last.status == "building"),
        previous_after_error=bool(active and last and last.status == "failed"),
        error_code=last.error_code if last else None,
    )


async def read_report(db, data, *, include_actual=False):
    active, status = await metadata(db)
    if not active:
        return {**status, "rows": [], "options": {}, "filters": data.model_dump()}
    sid = active.id
    where = [Fact.snapshot_id == sid]
    if data.snapshot_id and data.snapshot_id != sid:
        # Never silently mix metadata/facts from different generations.
        requested = await db.get(Snapshot, data.snapshot_id)
        if not requested or requested.status != "published":
            from fastapi import HTTPException

            raise HTTPException(409, "Снимок обновился. Обновите отчёт.")
        sid = requested.id
        where = [Fact.snapshot_id == sid]
        active = requested
        status.update(
            snapshot_id=sid,
            last_success=active.published_at,
            through=active.options.get("last_month"),
        )
    if data.years:
        where.append(Fact.year.in_(data.years))
    if data.stores:
        where.append(Fact.store.in_(data.stores))
    if data.brands:
        where.append(Fact.brand.in_(data.brands))
    if data.manufacturers:
        where.append(Fact.manufacturer.in_(data.manufacturers))
    if data.categories:
        where.append(
            Fact.category_id.in_(
                select(Category.id).where(
                    Category.snapshot_id == sid,
                    (
                        Category.key.in_(data.categories)
                        | Category.parent_key.in_(data.categories)
                    ),
                )
            )
        )
    for kind, values in [("manager", data.manager), ("buyer_type", data.buyer_type)]:
        if values:
            where.append(
                exists(
                    select(Membership.client_id).where(
                        Membership.snapshot_id == sid,
                        Membership.client_id == Fact.client_id,
                        Membership.kind == kind,
                        Membership.value.in_(values),
                    )
                )
            )
    query = (
        select(
            Fact.category_id,
            Fact.store,
            Fact.year,
            Fact.month,
            func.sum(Fact.units).label("units"),
            func.sum(Fact.revenue).label("revenue"),
            func.sum(Fact.lines).label("lines"),
        )
        .where(*where)
        .group_by(Fact.category_id, Fact.store, Fact.year, Fact.month)
    )
    grouped = defaultdict(lambda: {"units": {}, "revenue": {}})
    stream = await db.stream(query.execution_options(yield_per=BATCH))
    monthly_rows = 0
    try:
        async for cid, store, year, month, units, revenue, lines in stream:
            monthly_rows += 1
            if monthly_rows % BATCH == 0:
                ensure_headroom()
            grouped[cid]["units"][(store, year, month)] = (units, int(lines))
            grouped[cid]["revenue"][(store, year, month)] = (revenue, int(lines))
    finally:
        await stream.close()
    coverage_where = [Coverage.snapshot_id == sid]
    if data.years:
        coverage_where.append(Coverage.year.in_(data.years))
    if data.stores:
        coverage_where.append(Coverage.store.in_(data.stores))
    coverage = {
        (s, y, m): k
        for s, y, m, k in (
            await db.execute(
                select(
                    Coverage.store, Coverage.year, Coverage.month, Coverage.method
                ).where(*coverage_where)
            )
        ).all()
    }
    categories = (
        await db.scalars(
            select(Category).where(
                Category.snapshot_id == sid, Category.id.in_(grouped)
            )
        )
    ).all()
    rows = []
    for category in categories:
        ensure_headroom()
        facts = grouped[category.id]
        profiles = {
            metric: seasonal_profile(facts[metric], coverage, data.years)
            for metric in ("units", "revenue")
        }
        if not include_actual:
            for profile in profiles.values():
                profile["actual"] = []
        rows.append(
            {k: getattr(category, k) for k in ("key", "parent_key", "parent", "name")}
            | {metric: profiles[metric] for metric in ("units", "revenue")}
        )
    rows.sort(key=lambda r: (r["parent"].casefold(), r["name"].casefold()))
    return {
        **status,
        "rows": rows,
        "options": active.options,
        "filters": data.model_dump(),
        "warning": "Индекс — отклонение от собственного среднего. 90% дней с записями — ориентир регулярности, не гарантия полноты 1С. Неизвестные значения не считаются нулём.",
    }
