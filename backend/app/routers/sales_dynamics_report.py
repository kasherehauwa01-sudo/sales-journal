from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.models import Sale, SaleItem
from app.services.clients_vr import ClientsVrError, get_buyer_type_clients, get_manager_clients
from app.services.sales_dynamics_report import calculated_metrics, default_grouping, metric_comparison, previous_period

router = APIRouter(prefix="/reports/sales-dynamics", tags=["Отчеты"])


async def _client_filter(manager: str | None, buyer_type: str | None) -> list[str] | None:
    try:
        manager_clients = await get_manager_clients(manager) if manager else None
        buyer_clients = await get_buyer_type_clients(buyer_type) if buyer_type else None
    except ClientsVrError as exc:
        raise HTTPException(502, str(exc)) from exc
    if manager_clients is None:
        return buyer_clients
    if buyer_clients is None:
        return manager_clients
    allowed = {value.strip().lower() for value in buyer_clients}
    return [value for value in manager_clients if value.strip().lower() in allowed]


def _conditions(start: date, end: date, department: str | None, clients: list[str] | None):
    result = [Sale.sale_date >= start, Sale.sale_date <= end]
    if department:
        result.append(Sale.department == department)
    if clients is not None:
        normalized = [value.strip().lower() for value in clients]
        result.append(func.lower(func.trim(Sale.client)).in_(normalized) if normalized else False)
    return result


def _sales_rows(start: date, end: date, department: str | None, clients: list[str] | None):
    """Сначала агрегирует позиции только для отфильтрованных продаж, сохраняя одну строку на чек."""
    return (
        select(
            Sale.id,
            Sale.sale_date,
            Sale.total_amount,
            func.coalesce(func.sum(SaleItem.quantity), 0).label("items_count"),
        )
        .outerjoin(SaleItem, SaleItem.sale_id == Sale.id)
        .where(*_conditions(start, end, department, clients))
        .group_by(Sale.id, Sale.sale_date, Sale.total_amount)
        .subquery()
    )


async def _metrics(db: AsyncSession, start: date, end: date, department: str | None, clients: list[str] | None):
    sales = _sales_rows(start, end, department, clients)
    row = (await db.execute(
        select(
            func.coalesce(func.sum(sales.c.total_amount), 0),
            func.count(sales.c.id),
            func.coalesce(func.sum(sales.c.items_count), 0),
        )
    )).one()
    return calculated_metrics(float(row[0] or 0), int(row[1] or 0), float(row[2] or 0))


async def _chart(db: AsyncSession, start: date, end: date, group_by: str, department: str | None, clients: list[str] | None):
    sales = _sales_rows(start, end, department, clients);bucket = func.date_trunc(group_by, sales.c.sale_date).label("period")
    rows = (await db.execute(
        select(
            bucket,
            func.coalesce(func.sum(sales.c.total_amount), 0).label("revenue"),
            func.count(sales.c.id).label("sales_count"),
            func.coalesce(func.sum(sales.c.items_count), 0).label("items_count"),
        ).group_by(bucket).order_by(bucket)
    )).all()
    return [{"period": row.period.date(), **calculated_metrics(float(row.revenue or 0), int(row.sales_count or 0), float(row.items_count or 0))} for row in rows]


@router.get("")
async def report(
    date_from: date,
    date_to: date,
    period_kind: str = Query("custom", pattern="^(today|yesterday|current_week|previous_week|current_month|previous_month|custom)$"),
    group_by: str | None = Query(None, pattern="^(day|week|month)$"),
    department: str | None = None,
    manager: str | None = None,
    buyer_type: str | None = None,
    db: AsyncSession = Depends(get_db),
):
    if date_from > date_to:
        raise HTTPException(422, "Дата начала не может быть позже даты окончания")
    previous_from, previous_to = previous_period(date_from, date_to, period_kind)
    grouping = group_by or default_grouping(date_from, date_to)
    clients = await _client_filter(manager, buyer_type)
    current = await _metrics(db, date_from, date_to, department, clients)
    previous = await _metrics(db, previous_from, previous_to, department, clients)
    current_points = await _chart(db, date_from, date_to, grouping, department, clients)
    previous_points = await _chart(db, previous_from, previous_to, grouping, department, clients)
    points = []
    for index in range(max(len(current_points), len(previous_points))):
        current_point = current_points[index] if index < len(current_points) else None
        previous_point = previous_points[index] if index < len(previous_points) else None
        points.append({"index": index + 1, "period": current_point["period"] if current_point else None, "previous_period": previous_point["period"] if previous_point else None, "current": current_point, "previous": previous_point})
    return {
        "period": {"start": date_from, "end": date_to},
        "previous_period": {"start": previous_from, "end": previous_to},
        "metrics": {key: metric_comparison(current[key], previous[key]) for key in current},
        "chart": {"group_by": grouping, "points": points},
    }
