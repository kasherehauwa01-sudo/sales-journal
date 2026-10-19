"""Bounded monthly sales aggregates, independent of communication history."""
from datetime import date
from sqlalchemy import select, func
from app.models import Sale, SaleItem
from app.services.clients_vr import get_client_managers
from app.services.calltrack_buyers import manager_key


def monthly_sales_query(year, names):
    # Aggregate items before joining: header revenue/check count must not multiply.
    units = (select(SaleItem.sale_id, func.sum(SaleItem.quantity).label("units"))
             .join(Sale, Sale.id == SaleItem.sale_id)
             .where(Sale.sale_date >= date(year, 1, 1), Sale.sale_date < date(year + 1, 1, 1))
             .group_by(SaleItem.sale_id).subquery())
    month = func.extract("month", Sale.sale_date)
    identity = func.lower(func.trim(func.regexp_replace(Sale.client, r"\s+", " ", "g")))
    return (select(month.label("month"), func.sum(Sale.total_amount).label("revenue"),
                   func.count(Sale.id).label("checks"), func.coalesce(func.sum(units.c.units), 0).label("units"))
            .outerjoin(units, units.c.sale_id == Sale.id)
            .where(Sale.sale_date >= date(year, 1, 1), Sale.sale_date < date(year + 1, 1, 1),
                   identity.in_(names)).group_by(month).order_by(month))


async def dashboard_sales(db, payload):
    rows = [{"month": n, "revenue": 0, "checks": 0, "units": 0} for n in range(1, 13)]
    if not payload.managers:
        return {"items": rows}
    mapping = await get_client_managers()
    selected = {manager_key(value) for value in payload.managers}
    names = [name for name, owner in mapping.items() if manager_key(owner) in selected]
    if names:
        for row in (await db.execute(monthly_sales_query(payload.year, names))).mappings():
            rows[int(row["month"]) - 1] = dict(row)
    return {"items": rows}
