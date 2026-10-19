"""Bounded monthly sales aggregates, independent of communication history."""
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo
from sqlalchemy import select, func
from app.models import Sale, SaleItem
from app.services.clients_vr import get_client_managers
from app.services.calltrack_buyers import manager_key


def monthly_sales_query(year, names, *, compare=False, as_of=None):
    start = date(year - 1 if compare else year, 1, 1)
    end = min(date(year + 1, 1, 1), as_of + timedelta(days=1)) if as_of else date(year + 1, 1, 1)
    identity = func.lower(func.trim(func.regexp_replace(Sale.client, r"\s+", " ", "g")))
    # Aggregate items before joining: header revenue/check count must not multiply.
    units = (select(SaleItem.sale_id, func.sum(SaleItem.quantity).label("units"))
             .join(Sale, Sale.id == SaleItem.sale_id)
             .where(Sale.sale_date >= start, Sale.sale_date < end, identity.in_(names))
             .group_by(SaleItem.sale_id).subquery())
    month = func.extract("month", Sale.sale_date)
    sale_year = func.extract("year", Sale.sale_date)
    return (select(sale_year.label("year"), month.label("month"), func.sum(Sale.total_amount).label("revenue"),
                   func.count(Sale.id).label("checks"), func.coalesce(func.sum(units.c.units), 0).label("units"))
            .outerjoin(units, units.c.sale_id == Sale.id)
            .where(Sale.sale_date >= start, Sale.sale_date < end, identity.in_(names)).group_by(sale_year, month).order_by(sale_year, month))


def business_today():
    return datetime.now(ZoneInfo("Europe/Volgograd")).date()


async def dashboard_sales(db, payload):
    today = business_today()
    years = {year: [{"month": n, "revenue": 0, "checks": 0, "units": 0} for n in range(1, 13)]
             for year in (payload.year, payload.year - 1)}
    if payload.managers:
        mapping = await get_client_managers()
        selected = {manager_key(value) for value in payload.managers}
        names = [name for name, owner in mapping.items() if manager_key(owner) in selected]
        if names:
            query = monthly_sales_query(payload.year, names, compare=True, as_of=today)
            for record in (await db.execute(query)).mappings():
                row = dict(record)
                year = int(row.pop("year"))
                years[year][int(row["month"]) - 1] = row
    # items stays numeric and bounded for existing consumers. New renderers use as_of
    # to distinguish future months from real zero-sales months.
    return {"items": years[payload.year], "previous_items": years[payload.year - 1],
            "year": payload.year, "previous_year": payload.year - 1, "as_of": today}
