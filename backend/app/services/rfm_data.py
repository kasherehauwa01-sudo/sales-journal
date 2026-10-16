"""Shared RFM inputs; product metrics are optional, scoring semantics unchanged."""
from datetime import date
from sqlalchemy import distinct, func, literal, select
from sqlalchemy.ext.asyncio import AsyncSession
from app.models import Sale, SaleItem


async def rfm_rows(db: AsyncSession, start: date, end: date, departments: list[str] | None, *, include_products: bool = True) -> list[dict]:
    item_stats = (select(
        SaleItem.sale_id.label("sale_id"), func.coalesce(func.sum(SaleItem.quantity), 0).label("units"),
        func.count(distinct(func.coalesce(SaleItem.code, SaleItem.article, SaleItem.name))).label("products"),
    ).group_by(SaleItem.sale_id).subquery())
    client_key = func.lower(func.trim(Sale.client))
    conditions = [Sale.sale_date.between(start, end), Sale.client.is_not(None), func.length(func.trim(Sale.client)) > 0]
    if departments: conditions.append(Sale.department.in_(departments))
    query = (select(
        client_key.label("client_key"), func.min(Sale.client).label("client"),
        func.min(Sale.sale_date).label("period_first"), func.max(Sale.sale_date).label("last_purchase"),
        func.count(Sale.id).label("frequency"), func.sum(Sale.total_amount).label("monetary"),
        func.avg(Sale.total_amount).label("average_check"), func.max(Sale.total_amount).label("max_check"),
        func.min(Sale.total_amount).label("min_check"), (func.coalesce(func.sum(item_stats.c.units), 0) if include_products else literal(0)).label("units"),
        (func.coalesce(func.sum(item_stats.c.products), 0) if include_products else literal(0)).label("unique_products"),
        func.count(distinct(Sale.department)).label("departments_count"),
        func.array_agg(distinct(Sale.sale_date)).label("purchase_dates"),
    ).select_from(Sale).where(*conditions).group_by(client_key))
    if include_products:
        query = query.outerjoin(item_stats, item_stats.c.sale_id == Sale.id)
    current = (await db.execute(query)).mappings().all()
    if not current: return []
    # Первая известная покупка намеренно ищется за всю историю до конца среза.
    history = dict((await db.execute(select(client_key, func.min(Sale.sale_date)).where(
        Sale.sale_date <= end, Sale.client.is_not(None), func.length(func.trim(Sale.client)) > 0
    ).group_by(client_key))).all())
    months = max(1, (end - start).days + 1) / 30.4375
    result = []
    for source in current:
        dates = sorted(source["purchase_dates"] or [])
        gaps = [(b - a).days for a, b in zip(dates, dates[1:])]
        interval = sum(gaps) / len(gaps) if gaps else None
        recency = (end - source["last_purchase"]).days
        result.append({**dict(source), "first_purchase": history[source["client_key"]],
                       "recency_days": recency, "purchases_per_month": source["frequency"] / months,
                       "average_interval": interval, "cycle_overdue": recency / interval if interval and interval > 0 else None,
                       "monetary": float(source["monetary"] or 0), "average_check": float(source["average_check"] or 0),
                       "max_check": float(source["max_check"] or 0), "min_check": float(source["min_check"] or 0),
                       "units": float(source["units"] or 0)})
    if not include_products:
        for row, source in zip(result, current):
            row["_monetary"] = source["monetary"] or 0
            row["_average_check"] = source["average_check"] or 0
    return result
