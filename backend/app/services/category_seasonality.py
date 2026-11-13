"""On-demand category/month aggregates. No SKU profiles, jobs or persistent writes."""

import calendar
import json
import logging
import time
from collections import defaultdict
from datetime import date
from decimal import Decimal
from statistics import median
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import select, func, text, false, insert, Table, Column, MetaData, Text
from app.models import CatalogProduct, CatalogLookup, Sale, SaleItem, ImportBatch
from app.models.import_control import ImportReceipt
from app.services.catalog_attributes import identity
from app.services.product_analytics_runtime import ensure_headroom, memory_snapshot
from app.services.sales_client_filters import get_sales_filter_clients
from app.services.report_filters import client_names_condition
from app.services.clients_vr import ClientsVrError

log = logging.getLogger(__name__)
UNKNOWN = "Без данных об иерархии"
BATCH = 500
MAX_GROUPS = 5000


def branch(category_id, category, subcategory, legacy):
    section = (subcategory or legacy or "").strip()
    confirmed = (
        category_id is not None
        and bool((category or "").strip())
        and bool((subcategory or "").strip())
    )
    parent = f"category:{category_id}" if confirmed else "unknown"
    name = section or "Без сопоставления"
    return dict(
        key=json.dumps([parent, name], ensure_ascii=False, separators=(",", ":")),
        parent_key=parent,
        parent=category.strip() if confirmed else UNKNOWN,
        name=name,
    )


async def directory(db):
    rows = (
        await db.execute(
            select(
                CatalogProduct.category_id,
                CatalogProduct.category,
                CatalogProduct.subcategory,
                CatalogProduct.legacy_category,
            )
            .where(
                select(CatalogLookup.identity)
                .where(
                    CatalogLookup.product_id == CatalogProduct.product_id,
                    CatalogLookup.status == "matched",
                )
                .exists()
            )
            .distinct()
            .limit(MAX_GROUPS + 1)
        )
    ).all()
    if len(rows) > MAX_GROUPS:
        raise HTTPException(503, "Справочник слишком велик для безопасного расчёта.")
    items = {b["key"]: b for row in rows if (b := branch(*row))}
    unknown = branch(None, None, None, None)
    items.setdefault(unknown["key"], unknown)
    parents = {}
    for b in sorted(
        items.values(), key=lambda x: (x["parent"].casefold(), x["name"].casefold())
    ):
        p = parents.setdefault(
            b["parent_key"],
            {"id": b["parent_key"], "name": b["parent"], "children": []},
        )
        p["children"].append(
            {"id": b["key"], "value": b["key"], "name": b["name"], "children": []}
        )
    return items, list(parents.values())


def add_coverage(bits, years, stores, start, end, departments):
    for year in years:
        first = date(year, 1, 1)
        lo, hi = max(start, first), min(end, date(year, 12, 31))
        if lo <= hi:
            mask = ((1 << ((hi - lo).days + 1)) - 1) << ((lo - first).days)
            for store in departments:
                if store in stores:
                    bits[(store, year)] |= mask


def coverage_from_bits(bits, years, stores):
    result = {}
    for year in years:
        months = []
        for month in range(1, 13):
            mask = ((1 << calendar.monthrange(year, month)[1]) - 1) << (
                (date(year, month, 1) - date(year, 1, 1)).days
            )
            months.append(
                bool(stores)
                and all(bits.get((s, year), 0) & mask == mask for s in stores)
            )
        result[year] = months
    return result


def monthly_coverage(years, stores, intervals):
    """Successful declared-period receipts, never existence of sales, cover days."""
    bits = defaultdict(int)
    for start, end, departments in intervals:
        add_coverage(bits, years, stores, start, end, departments)
    return coverage_from_bits(bits, years, stores)


def profile(values, coverage):
    suitable = {}
    annual = []
    for year, numbers in sorted(values.items()):
        complete = all(coverage.get(year, [False] * 12))
        base = sum(numbers) / Decimal(12)
        indices = (
            [(n / base - 1) * 100 for n in numbers] if complete and base > 0 else None
        )
        reason = (
            None
            if indices
            else (
                "Покрытие 12 месяцев не подтверждено"
                if not complete
                else "Нулевая или отрицательная годовая база"
            )
        )
        annual.append(
            dict(
                year=year,
                values=[float(n) for n in numbers],
                indices=[float(n) for n in indices] if indices else None,
                included=indices is not None,
                reason=reason,
                coverage=coverage.get(year, [False] * 12),
            )
        )
        if indices:
            suitable[year] = indices
    typical = (
        [median([v[m] for v in suitable.values()]) for m in range(12)]
        if suitable
        else None
    )
    peaks = []
    if typical and max(typical) >= 20:
        peak = max(typical)
        for m, v in enumerate(typical):
            if v == peak:
                count = sum(vs[m] >= 20 for vs in suitable.values())
                peaks.append(
                    dict(
                        month=m + 1,
                        count=count,
                        total=len(suitable),
                        repeating=count >= 2 and count * 3 >= len(suitable) * 2,
                    )
                )
    return dict(
        indices=[float(n) for n in typical] if typical else None,
        years=list(suitable),
        annual=annual,
        peaks=peaks,
        message=(
            "Нет пригодных лет"
            if not suitable
            else (
                "Недостаточно данных для оценки повторяемости"
                if len(suitable) == 1
                else "Нет выраженного пика" if not peaks else None
            )
        ),
    )


def filters(data, clients):
    conditions = [func.extract("year", Sale.sale_date).in_(data.years)]
    # Date ranges also allow the existing sale_date index to prune history.
    conditions += [
        Sale.sale_date >= date(min(data.years), 1, 1),
        Sale.sale_date <= date(max(data.years), 12, 31),
    ]
    if data.stores:
        conditions.append(Sale.department.in_(data.stores))
    if clients is not None:
        names = [n.strip().casefold() for n in clients if n and n.strip()]
        conditions.append(
            client_names_condition(Sale.client, names) if names else false()
        )
    return conditions


async def aggregate(db, data, groups, clients):
    """Stream DISTINCT raw identifier pairs, resolve exact existing Python identities,
    then aggregate only category/year/month in PostgreSQL. Bounded TEMP projection
    avoids PostgreSQL lower() vs Python casefold() differences and bind megabytes.
    """
    started = time.perf_counter()
    name = "category_season_" + uuid4().hex
    mapping = Table(
        name,
        MetaData(),
        Column("code", Text),
        Column("article", Text),
        Column("branch", Text),
    )
    await db.execute(
        text(
            f'CREATE TEMP TABLE "{name}" (code text NOT NULL, article text NOT NULL, branch text NOT NULL) ON COMMIT DROP'
        )
    )
    conditions = filters(data, clients)
    code, article = func.coalesce(SaleItem.code, ""), func.coalesce(
        SaleItem.article, ""
    )
    pairs = await db.stream(
        select(code.label("code"), article.label("article"))
        .select_from(SaleItem)
        .join(Sale)
        .where(*conditions)
        .distinct()
        .execution_options(yield_per=BATCH)
    )
    pair_count = 0
    try:
        async for batch in pairs.partitions(BATCH):
            ensure_headroom()
            digests = {identity(r.code, r.article) for r in batch}
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
                        CatalogLookup.identity.in_(digests),
                        CatalogLookup.status == "matched",
                    )
                )
            ).all()
            attrs = {r.identity: r for r in found}
            rows = []
            for r in batch:
                p = attrs.get(identity(r.code, r.article))
                b = (
                    branch(p.category_id, p.category, p.subcategory, p.legacy_category)
                    if p
                    else branch(None, None, None, None)
                )
                if (
                    data.categories
                    and b["key"] not in data.categories
                    and b["parent_key"] not in data.categories
                ):
                    continue
                if data.brands and (p is None or p.brand not in data.brands):
                    continue
                if data.manufacturers and (
                    p is None or p.manufacturer not in data.manufacturers
                ):
                    continue
                groups.setdefault(b["key"], b)
                rows.append({"code": r.code, "article": r.article, "branch": b["key"]})
            if rows:
                await db.execute(insert(mapping), rows)
            pair_count += len(batch)
    finally:
        await pairs.close()
    await db.execute(text(f'ANALYZE "{name}"'))
    year, month = func.extract("year", Sale.sale_date), func.extract(
        "month", Sale.sale_date
    )
    query = (
        select(
            mapping.c.branch,
            year.label("year"),
            month.label("month"),
            func.sum(SaleItem.quantity).label("units"),
            func.sum(SaleItem.quantity * SaleItem.actual_price).label("revenue"),
        )
        .select_from(SaleItem)
        .join(Sale)
        .join(mapping, (code == mapping.c.code) & (article == mapping.c.article))
        .where(*conditions)
        .group_by(mapping.c.branch, year, month)
    )
    result = defaultdict(
        lambda: {
            metric: {y: [Decimal(0)] * 12 for y in data.years}
            for metric in ("units", "revenue")
        }
    )
    stream = await db.stream(query.execution_options(yield_per=BATCH))
    count = 0
    try:
        async for row in stream:
            count += 1
            if count % BATCH == 0:
                ensure_headroom()
            if len(result) >= MAX_GROUPS and row.branch not in result:
                raise HTTPException(
                    503, "Слишком много подкатегорий для безопасного расчёта."
                )
            for metric in ("units", "revenue"):
                result[row.branch][metric][int(row.year)][int(row.month) - 1] = getattr(
                    row, metric
                ) or Decimal(0)
    finally:
        await stream.close()
    log.info(
        "category_seasonality stage=aggregate pairs=%d monthly_rows=%d elapsed_ms=%.3f memory=%s",
        pair_count,
        count,
        (time.perf_counter() - started) * 1000,
        memory_snapshot(),
    )
    return result, query


async def build(db, data, today):
    if not data.years or any(y >= today.year or y < 1900 for y in data.years):
        raise HTTPException(422, "Выберите завершённые календарные годы с данными.")
    data.years = sorted(set(data.years))
    try:
        clients = await get_sales_filter_clients(db, data.manager, data.buyer_type)
    except ClientsVrError:
        raise HTTPException(
            502,
            "Не удалось получить справочник клиентов для выбранных фильтров. Повторите вручную.",
        ) from None
    groups, _ = await directory(db)
    rows, _ = await aggregate(db, data, groups, clients)
    # Independent of all characteristic/client filters. Include every observed
    # department in the chosen years, not just those with matching products.
    stores = data.stores or list(
        (
            await db.scalars(
                select(Sale.department)
                .where(func.extract("year", Sale.sale_date).in_(data.years))
                .distinct()
            )
        ).all()
    )
    receipts = await db.stream(
        select(
            ImportReceipt.period_start,
            ImportReceipt.period_end,
            ImportReceipt.departments,
        )
        .join(ImportBatch, ImportReceipt.import_id == ImportBatch.id)
        .where(
            ImportReceipt.status == "success",
            ImportReceipt.period_method == "DECLARED_REPORT_PERIOD",
            ImportBatch.status == "completed",
            ImportBatch.error_rows == 0,
            ImportBatch.file_size > 0,
            ImportReceipt.period_start <= date(max(data.years), 12, 31),
            ImportReceipt.period_end >= date(min(data.years), 1, 1),
        )
        .execution_options(yield_per=BATCH)
    )
    try:
        # Merge covered dates as bounded year/store bitsets, not receipt history.
        coverage_sets = defaultdict(int)
        async for start, end, departments in receipts:
            ensure_headroom()
            if not data.stores:
                stores = list(dict.fromkeys([*stores, *departments]))
            add_coverage(coverage_sets, data.years, stores, start, end, departments)
    finally:
        await receipts.close()
    coverage = coverage_from_bits(coverage_sets, data.years, stores)
    output = []
    for key, values in rows.items():
        ensure_headroom()
        output.append(
            {
                **groups[key],
                **{m: profile(values[m], coverage) for m in ("units", "revenue")},
            }
        )
    output.sort(key=lambda x: (x["parent"].casefold(), x["name"].casefold()))
    return {
        "rows": output,
        "years": data.years,
        "filters": data.model_dump(),
        "category_labels": [
            next(
                (b["parent"] for b in groups.values() if b["parent_key"] == k),
                (
                    groups[k]["parent"] + " → " + groups[k]["name"]
                    if k in groups
                    else "Неизвестная ветка"
                ),
            )
            for k in data.categories
        ],
        "coverage": coverage,
        "coverage_stores": stores,
        "coverage_method": "successful_declared_receipt_intervals",
        "warning": "Покрытие подтверждается только успешными файлами с объявленным периодом и подразделением. Наличие продаж не доказывает полноту. Это проверка обработки заявленного периода, а не независимая гарантия полноты выгрузки 1С. Исторические остатки и полный реестр открытия магазинов недоступны; отсутствие файла исключает год из профиля, а не означает нулевой спрос.",
    }
