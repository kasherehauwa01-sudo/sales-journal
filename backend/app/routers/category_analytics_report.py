from datetime import date, datetime
from io import BytesIO
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from openpyxl import Workbook
from openpyxl.utils import get_column_letter
from sqlalchemy import String, and_, column, false, func, or_, select, values
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.models import Sale, SaleItem
from app.services.category_analytics import UNCATEGORIZED, aggregate_categories, chart_structure, comparable_period, report_summary
from app.services.clients_vr import ClientsVrError
from app.services.product_analytics import catalog_property
from app.services.sales_client_filters import get_sales_filter_clients
from app.services.sales_dynamics_report import default_grouping, effective_period
from app.services.vrcatalog import VrCatalogError, get_catalog_batch_info

router = APIRouter(prefix="/reports/category-analytics", tags=["Отчеты"])


def _sale_conditions(start: date, end: date, stores: list[str], clients: list[str] | None):
    result = [Sale.sale_date >= start, Sale.sale_date <= end]
    if stores: result.append(Sale.department.in_(stores))
    if clients is not None:
        names = [name.strip().casefold() for name in clients if name.strip()]
        result.append(func.lower(func.trim(Sale.client)).in_(names) if names else false())
    return result


async def _clients(manager: str | None, buyer_type: str | None):
    try: return await get_sales_filter_clients(manager, buyer_type)
    except ClientsVrError as exc: raise HTTPException(502, str(exc)) from exc


async def _product_keys(db: AsyncSession, start: date, end: date, previous_start: date, previous_end: date, stores: list[str], clients: list[str] | None):
    period = or_(and_(Sale.sale_date >= start, Sale.sale_date <= end), and_(Sale.sale_date >= previous_start, Sale.sale_date <= previous_end))
    conditions = [period]
    if stores: conditions.append(Sale.department.in_(stores))
    if clients is not None:
        names = [name.strip().casefold() for name in clients if name.strip()]
        conditions.append(func.lower(func.trim(Sale.client)).in_(names) if names else false())
    rows = (await db.execute(select(SaleItem.code, SaleItem.article, func.max(SaleItem.name).label("name")).join(Sale).where(*conditions).group_by(SaleItem.code, SaleItem.article))).all()
    return [{"code": row.code, "article": row.article, "name": row.name} for row in rows]


async def _catalog_map(products: list[dict]):
    catalog = {}
    try:
        for offset in range(0, len(products), 5000): catalog.update(await get_catalog_batch_info(products[offset:offset + 5000]))
    except VrCatalogError:
        pass  # Недоступность CatalogVR не должна скрывать продажи из «Без категории».
    mapped = []; seen = set()
    for product in products:
        code = str(product.get("code") or "").strip().casefold() or None
        article = str(product.get("article") or "").strip().casefold() or None
        stable_key = ("code", code) if code else ("article", article) if article else None
        if stable_key and stable_key in seen: continue
        if stable_key: seen.add(stable_key)
        info = catalog.get(f"code:{code}") if code else None
        if not info and article: info = catalog.get(f"article:{article}")
        category = catalog_property(info or {}, "category")
        if category == "Не заполнено": category = UNCATEGORIZED
        mapped.append((code, article, category))
    return mapped, catalog


def _mapping_cte(mapping: list[tuple[str | None, str | None, str]]):
    # VALUES передаёт в PostgreSQL только карту уникальных SKU, а не строки продаж.
    return values(column("code", String), column("article", String), column("category", String), name="catalog_categories").data(mapping or [(None, None, UNCATEGORIZED)]).alias("catalog_categories")


def _mapped_items(mapping):
    category_map = _mapping_cte(mapping)
    code = func.lower(func.trim(SaleItem.code)); article = func.lower(func.trim(SaleItem.article))
    join = or_(and_(category_map.c.code.is_not(None), code == category_map.c.code), and_(or_(SaleItem.code.is_(None), func.trim(SaleItem.code) == ""), category_map.c.code.is_(None), article == category_map.c.article))
    category = func.coalesce(category_map.c.category, UNCATEGORIZED).label("category")
    return category_map, join, category


async def _category_rows(db, start, end, stores, clients, mapping):
    category_map, join, category = _mapped_items(mapping)
    query = select(category, func.coalesce(func.sum(SaleItem.quantity * SaleItem.actual_price), 0).label("revenue"), func.coalesce(func.sum(SaleItem.quantity), 0).label("units"), func.count(func.distinct(Sale.id)).label("checks")).select_from(SaleItem).join(Sale).outerjoin(category_map, join).where(*_sale_conditions(start, end, stores, clients)).group_by(category)
    rows = (await db.execute(query)).all()
    return [{"category": row.category, "revenue": float(row.revenue), "units": float(row.units), "checks": int(row.checks)} for row in rows]


async def _global_checks(db, start, end, stores, clients):
    return int(await db.scalar(select(func.count(func.distinct(Sale.id))).join(SaleItem).where(*_sale_conditions(start, end, stores, clients))) or 0)


async def _scope(date_from, date_to, period_kind, stores, manager, buyer_type, category_filter, db):
    if date_from > date_to: raise HTTPException(422, "Дата начала не может быть позже даты окончания")
    today = datetime.now(ZoneInfo(settings.autoload_timezone)).date()
    start, end, warning = effective_period(date_from, date_to, period_kind, today)
    if start > end: raise HTTPException(422, warning or "В выбранном периоде пока нет данных")
    previous_start, previous_end = comparable_period(start, end)
    clients = await _clients(manager, buyer_type)
    products = await _product_keys(db, start, end, previous_start, previous_end, stores, clients)
    mapping, catalog = await _catalog_map(products)
    current = await _category_rows(db, start, end, stores, clients, mapping)
    previous = await _category_rows(db, previous_start, previous_end, stores, clients, mapping)
    rows = aggregate_categories(current, previous)
    if category_filter: rows = [row for row in rows if row["category"].casefold() == category_filter.strip().casefold()]
    if category_filter:
        current_checks = next((row["checks"] for row in current if row["category"].casefold() == category_filter.strip().casefold()), 0)
        previous_checks = next((row["checks"] for row in previous if row["category"].casefold() == category_filter.strip().casefold()), 0)
    else:
        current_checks = await _global_checks(db, start, end, stores, clients)
        previous_checks = await _global_checks(db, previous_start, previous_end, stores, clients)
    return start, end, previous_start, previous_end, warning, clients, mapping, catalog, rows, current_checks, previous_checks


@router.get("")
async def report(date_from: date, date_to: date, period_kind: str = "custom", stores: list[str] = Query(default=[]), manager: str | None = None, buyer_type: str | None = None, category: str | None = None, db: AsyncSession = Depends(get_db)):
    start, end, old_start, old_end, warning, _, _, _, all_rows, checks, old_checks = await _scope(date_from, date_to, period_kind, stores, manager, buyer_type, None, db)
    options = sorted(row["category"] for row in all_rows)
    rows = [row for row in all_rows if not category or row["category"].casefold() == category.strip().casefold()]
    if category:
        checks = rows[0]["current_checks"] if rows else 0; old_checks = rows[0]["previous_checks"] if rows else 0
    public_rows = [{key: value for key, value in row.items() if key != "products"} for row in rows]
    return {"period": {"start": start, "end": end}, "previous_period": {"start": old_start, "end": old_end}, "warning": warning, "metrics": report_summary(rows, checks, old_checks), "categories": public_rows, "category_options": options, "structure": chart_structure(rows), "growth_drivers": sorted((row for row in public_rows if row["revenue_change"] > 0), key=lambda row: row["revenue_change"], reverse=True)[:5], "decline_drivers": sorted((row for row in public_rows if row["revenue_change"] < 0), key=lambda row: row["revenue_change"])[:5]}


async def _product_details(db, start, end, old_start, old_end, stores, clients, mapping, category_name):
    category_map, join, category = _mapped_items(mapping)
    async def period_rows(period_start, period_end):
        conditions = _sale_conditions(period_start, period_end, stores, clients)
        if category_name: conditions.append(category == category_name)
        rows = (await db.execute(select(category, SaleItem.article, SaleItem.code, func.max(SaleItem.name).label("name"), func.sum(SaleItem.quantity * SaleItem.actual_price).label("revenue"), func.sum(SaleItem.quantity).label("units"), func.count(func.distinct(Sale.id)).label("checks")).select_from(SaleItem).join(Sale).outerjoin(category_map, join).where(*conditions).group_by(category, SaleItem.article, SaleItem.code))).all()
        return {(row.category, row.code or row.article or row.name): row for row in rows}
    current, previous = await period_rows(start, end), await period_rows(old_start, old_end)
    total = sum(float(row.revenue or 0) for row in current.values())
    result = []
    for key in current.keys() | previous.keys():
        now, old = current.get(key), previous.get(key); revenue = float(now.revenue or 0) if now else 0; old_revenue = float(old.revenue or 0) if old else 0
        result.append({"category": (now or old).category, "article": (now or old).article, "code": (now or old).code, "name": (now or old).name, "revenue": revenue, "previous_revenue": old_revenue, "revenue_change": revenue - old_revenue, "units": float(now.units or 0) if now else 0, "checks": int(now.checks) if now else 0, "share": revenue / total * 100 if total else 0})
    return sorted(result, key=lambda row: row["revenue"], reverse=True)


@router.get("/{category_name}/details")
async def details(category_name: str, date_from: date, date_to: date, period_kind: str = "custom", stores: list[str] = Query(default=[]), manager: str | None = None, buyer_type: str | None = None, group_by: str | None = Query(None, pattern="^(day|week|month)$"), db: AsyncSession = Depends(get_db)):
    start, end, old_start, old_end, _, clients, mapping, _, rows, _, _ = await _scope(date_from, date_to, period_kind, stores, manager, buyer_type, category_name, db)
    if not rows: raise HTTPException(404, "Категория не найдена")
    products = await _product_details(db, start, end, old_start, old_end, stores, clients, mapping, category_name)
    category_map, join, category = _mapped_items(mapping); grouping = group_by or default_grouping(start, end); bucket = func.date_trunc(grouping, Sale.sale_date).label("period")
    points = (await db.execute(select(bucket, func.sum(SaleItem.quantity * SaleItem.actual_price).label("revenue"), func.sum(SaleItem.quantity).label("units")).select_from(SaleItem).join(Sale).outerjoin(category_map, join).where(*_sale_conditions(start, end, stores, clients), category == category_name).group_by(bucket).order_by(bucket))).all()
    return {"category": {key: value for key, value in rows[0].items() if key != "products"}, "products": products, "group_by": grouping, "points": [{"period": row.period.date(), "revenue": float(row.revenue or 0), "units": float(row.units or 0)} for row in points]}


def _sheet(book, title, headers, rows):
    sheet = book.create_sheet(title); sheet.append(headers)
    for row in rows: sheet.append(row)
    sheet.freeze_panes = "A2"
    for index, cells in enumerate(sheet.columns, 1): sheet.column_dimensions[get_column_letter(index)].width = min(45, max(12, max(len(str(cell.value or "")) for cell in cells) + 2))


@router.get("/export/xlsx")
async def export(date_from: date, date_to: date, period_kind: str = "custom", stores: list[str] = Query(default=[]), manager: str | None = None, buyer_type: str | None = None, category: str | None = None, db: AsyncSession = Depends(get_db)):
    start, end, old_start, old_end, _, clients, mapping, _, rows, checks, old_checks = await _scope(date_from, date_to, period_kind, stores, manager, buyer_type, category, db)
    metrics = report_summary(rows, checks, old_checks); products = await _product_details(db, start, end, old_start, old_end, stores, clients, mapping, None)
    book = Workbook(); book.remove(book.active)
    _sheet(book, "Итоги", ["Показатель", "Текущий", "Предыдущий", "Изменение", "Изменение, %"], [[name, value["current"], value["previous"], value["difference"], value["change_percent"]] for name, value in metrics.items()] + [["Период", str(start), str(end), str(old_start), str(old_end)]])
    headers = ["Категория", "Выручка", "Доля, %", "Продано", "Чеков", "Средний чек", "Прошлая выручка", "Изменение", "Изменение, %", "Изменение доли, п.п.", "Вклад, %"]
    category_rows = [[row["category"], row["current_revenue"], row["current_share"], row["current_units"], row["current_checks"], row["current_average_check"], row["previous_revenue"], row["revenue_change"], row["revenue_change_percent"], row["share_change_pp"], row["contribution_percent"]] for row in rows]
    _sheet(book, "Категории", headers, category_rows); _sheet(book, "Драйверы роста", headers, [row for row in category_rows if row[7] > 0]); _sheet(book, "Категории с падением", headers, [row for row in category_rows if row[7] < 0]); _sheet(book, "Товары", ["Категория", "Артикул", "Код", "Наименование", "Выручка", "Прошлая выручка", "Изменение", "Продано", "Чеков", "Доля"], [[row[key] for key in ("category", "article", "code", "name", "revenue", "previous_revenue", "revenue_change", "units", "checks", "share")] for row in products])
    output = BytesIO(); book.save(output)
    return Response(output.getvalue(), media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", headers={"Content-Disposition": "attachment; filename=category-analytics.xlsx"})
