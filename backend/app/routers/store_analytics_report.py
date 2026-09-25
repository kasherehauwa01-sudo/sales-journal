from datetime import date, datetime
from io import BytesIO
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from openpyxl import Workbook
from openpyxl.utils import get_column_letter
from sqlalchemy import false, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.models import Sale, SaleItem
from app.services.clients_vr import ClientsVrError
from app.services.sales_client_filters import get_sales_filter_clients
from app.services.sales_dynamics_report import default_grouping, effective_period
from app.services.store_analytics_report import METRIC_KEYS, calculated_store_metrics, comparable_period, compare_metrics, merge_store_rows

router = APIRouter(prefix="/reports/store-analytics", tags=["Отчеты"])


async def _clients(db: AsyncSession, manager: str | None, buyer_type: str | None):
    try:
        return await get_sales_filter_clients(db, manager, buyer_type)
    except ClientsVrError as exc:
        raise HTTPException(502, str(exc)) from exc


def _conditions(start: date, end: date, stores: list[str], clients: list[str] | None):
    conditions = [Sale.sale_date >= start, Sale.sale_date <= end]
    if stores:
        conditions.append(Sale.department.in_(stores))
    if clients is not None:
        normalized = [value.strip().casefold() for value in clients if value.strip()]
        conditions.append(func.lower(func.trim(Sale.client)).in_(normalized) if normalized else false())
    return conditions


def _sales(start: date, end: date, stores: list[str], clients: list[str] | None):
    # Сначала ограничиваем продажи периодом и фильтрами. Это важно для большой
    # таблицы sale_items: PostgreSQL агрегирует позиции только нужных чеков, а
    # не строит сумму по всей истории перед применением периода.
    filtered_sales = select(
        Sale.id, Sale.sale_date, Sale.document_number, Sale.client,
        func.coalesce(Sale.department, "Без подразделения").label("store"),
        Sale.total_amount, func.coalesce(Sale.discount_percent, 0).label("discount"),
    ).where(*_conditions(start, end, stores, clients)).cte("filtered_store_sales")
    item_totals = select(
        SaleItem.sale_id, func.coalesce(func.sum(SaleItem.quantity), 0).label("items"),
    ).join(filtered_sales, filtered_sales.c.id == SaleItem.sale_id).group_by(SaleItem.sale_id).subquery()
    return select(
        filtered_sales.c.id, filtered_sales.c.sale_date, filtered_sales.c.document_number,
        filtered_sales.c.client, filtered_sales.c.store, filtered_sales.c.total_amount,
        filtered_sales.c.discount, func.coalesce(item_totals.c.items, 0).label("items"),
    ).outerjoin(item_totals, item_totals.c.sale_id == filtered_sales.c.id).subquery()


async def _aggregates(db: AsyncSession, start: date, end: date, stores: list[str], clients: list[str] | None):
    sales = _sales(start, end, stores, clients)
    rows = (await db.execute(select(
        sales.c.store, func.coalesce(func.sum(sales.c.total_amount), 0).label("revenue"),
        func.count(sales.c.id).label("checks"), func.coalesce(func.sum(sales.c.items), 0).label("items"),
        func.coalesce(func.sum(sales.c.discount), 0).label("discount_sum"),
    ).group_by(sales.c.store))).all()
    return [{"store": row.store, **calculated_store_metrics(float(row.revenue), int(row.checks), float(row.items), float(row.discount_sum))} for row in rows]


def _total(rows: list[dict]):
    return calculated_store_metrics(sum(x["revenue"] for x in rows), sum(x["checks"] for x in rows), sum(x["items"] for x in rows), sum(x["average_discount"] * x["checks"] for x in rows))


async def _scope(date_from: date, date_to: date, period_kind: str, stores: list[str], manager: str | None, buyer_type: str | None, db: AsyncSession):
    if date_from > date_to:
        raise HTTPException(422, "Дата начала не может быть позже даты окончания")
    today = datetime.now(ZoneInfo(settings.autoload_timezone)).date()
    current_from, current_to, warning = effective_period(date_from, date_to, period_kind, today)
    if current_from > current_to:
        raise HTTPException(422, warning or "В выбранном периоде пока нет данных")
    previous_from, previous_to = comparable_period(current_from, current_to)
    clients = await _clients(db, manager, buyer_type)
    current = await _aggregates(db, current_from, current_to, stores, clients)
    previous = await _aggregates(db, previous_from, previous_to, stores, clients)
    return current_from, current_to, previous_from, previous_to, warning, clients, current, previous


@router.get("")
async def report(date_from: date, date_to: date, period_kind: str = "custom", stores: list[str] = Query(default=[]), manager: str | None = None, buyer_type: str | None = None, db: AsyncSession = Depends(get_db)):
    start, end, previous_start, previous_end, warning, _, current, previous = await _scope(date_from, date_to, period_kind, stores, manager, buyer_type, db)
    return {"period": {"start": start, "end": end}, "previous_period": {"start": previous_start, "end": previous_end}, "warning": warning, "metrics": compare_metrics(_total(current), _total(previous)), "stores": merge_store_rows(current, previous)}


@router.get("/stores/{store}/dynamics")
async def dynamics(store: str, date_from: date, date_to: date, group_by: str | None = Query(None, pattern="^(day|week|month)$"), manager: str | None = None, buyer_type: str | None = None, db: AsyncSession = Depends(get_db)):
    clients = await _clients(db, manager, buyer_type); grouping = group_by or default_grouping(date_from, date_to); sales = _sales(date_from, date_to, [store], clients); bucket = func.date_trunc(grouping, sales.c.sale_date).label("period")
    rows = (await db.execute(select(bucket, func.sum(sales.c.total_amount).label("revenue"), func.count(sales.c.id).label("checks")).group_by(bucket).order_by(bucket))).all()
    return {"group_by": grouping, "points": [{"period": x.period.date(), "revenue": float(x.revenue or 0), "checks": x.checks, "average_check": float(x.revenue or 0) / x.checks if x.checks else 0} for x in rows]}


@router.get("/stores/{store}/sales")
async def store_sales(store: str, date_from: date, date_to: date, manager: str | None = None, buyer_type: str | None = None, page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=200), db: AsyncSession = Depends(get_db)):
    clients = await _clients(db, manager, buyer_type); sales = _sales(date_from, date_to, [store], clients); total = await db.scalar(select(func.count()).select_from(sales)) or 0
    rows = (await db.execute(select(sales).order_by(sales.c.sale_date.desc(), sales.c.id.desc()).offset((page - 1) * page_size).limit(page_size))).all()
    return {"items": [{**dict(x._mapping), "total_amount": float(x.total_amount), "items": float(x.items), "discount": float(x.discount)} for x in rows], "total": total, "page": page, "pages": max(1, (total + page_size - 1) // page_size)}


def _sheet(book, title, headers, rows):
    sheet = book.create_sheet(title); sheet.append(headers)
    for row in rows: sheet.append(row)
    sheet.freeze_panes = "A2"
    for index, column in enumerate(sheet.columns, 1): sheet.column_dimensions[get_column_letter(index)].width = min(42, max(12, max(len(str(cell.value or "")) for cell in column) + 2))


@router.get("/export")
async def export(date_from: date, date_to: date, period_kind: str = "custom", stores: list[str] = Query(default=[]), manager: str | None = None, buyer_type: str | None = None, db: AsyncSession = Depends(get_db)):
    start, end, previous_start, previous_end, _, clients, current, previous = await _scope(date_from, date_to, period_kind, stores, manager, buyer_type, db); merged = merge_store_rows(current, previous); totals = compare_metrics(_total(current), _total(previous)); sales = _sales(start, end, stores, clients); details = (await db.execute(select(sales).order_by(sales.c.store, sales.c.sale_date))).all(); book = Workbook(); book.remove(book.active)
    names = {"revenue": "Выручка", "checks": "Количество чеков", "average_check": "Средний чек", "items": "Продано товаров", "items_per_check": "Товаров в чеке", "average_discount": "Средняя скидка"}
    headers = ["Магазин"] + [name for key in METRIC_KEYS for name in (f"{names[key]}: текущий", f"{names[key]}: предыдущий", f"{names[key]}: изменение, %")]
    _sheet(book, "Магазины", headers, [[row["store"]] + [value for key in METRIC_KEYS for value in (row["metrics"][key]["current"], row["metrics"][key]["previous"], row["metrics"][key]["change_percent"])] for row in merged])
    _sheet(book, "Итого", ["Показатель", "Текущий", "Предыдущий", "Изменение", "Изменение, %"], [[names[key], value["current"], value["previous"], value["difference"], value["change_percent"]] for key, value in totals.items()] + [["Текущий период", str(start), str(end), None, None], ["Предыдущий период", str(previous_start), str(previous_end), None, None]])
    _sheet(book, "Детализация", ["Магазин", "Дата", "Документ", "Клиент", "Сумма", "Количество товаров", "Скидка, %"], [[x.store, x.sale_date, x.document_number, x.client, float(x.total_amount), float(x.items), float(x.discount)] for x in details]); output = BytesIO(); book.save(output)
    return Response(output.getvalue(), media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", headers={"Content-Disposition": "attachment; filename=store-analytics.xlsx"})
