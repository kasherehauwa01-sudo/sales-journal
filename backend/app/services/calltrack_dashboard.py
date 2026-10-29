"""Bounded monthly sales aggregates, independent of communication history."""
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo
from sqlalchemy import String, any_, bindparam, cast, select, func
from sqlalchemy.dialects.postgresql import ARRAY
from app.models import Sale, SaleItem
from app.services.clients_vr import get_client_managers
from app.services.calltrack_buyers import manager_key
from app.services.product_analytics_runtime import heavy_operation, ensure_headroom
from app.services.report_execution import report_transaction


def dashboard_client_condition(names):
    """Keep the existing normalized client identity; bind the complete set once."""
    identity = func.lower(func.trim(func.regexp_replace(Sale.client, r"\s+", " ", "g")))
    if not names:
        return False
    selected = cast(bindparam("dashboard_clients", value=list(dict.fromkeys(names)), type_=ARRAY(String), unique=True), ARRAY(String))
    return identity == any_(selected)


def monthly_sales_query(year, names, *, compare=False, as_of=None):
    start = date(year - 1 if compare else year, 1, 1)
    end = min(date(year + 1, 1, 1), as_of + timedelta(days=1)) if as_of else date(year + 1, 1, 1)
    # Both monthly aggregates reuse this narrow, filtered receipt set. Client
    # normalization and large array membership appear only once in the SQL.
    sales = select(Sale.id, Sale.sale_date, Sale.total_amount).where(
        Sale.sale_date >= start, Sale.sale_date < end, dashboard_client_condition(names)
    ).cte("dashboard_scope").prefix_with("MATERIALIZED", dialect="postgresql")
    sale_year = func.extract("year", sales.c.sale_date)
    month = func.extract("month", sales.c.sale_date)
    headers = select(
        sale_year.label("year"), month.label("month"),
        func.sum(sales.c.total_amount).label("revenue"),
        func.count(sales.c.id).label("checks"),
    ).group_by(sale_year, month).subquery("dashboard_month_headers")
    # Group quantities directly into at most 24 months instead of maintaining a
    # per-receipt aggregate for every selected receipt. Do not join raw items to
    # the header aggregate: that would multiply revenue and check counts.
    units = select(
        sale_year.label("year"), month.label("month"),
        func.sum(SaleItem.quantity).label("units"),
    ).select_from(sales).join(SaleItem, SaleItem.sale_id == sales.c.id).group_by(
        sale_year, month
    ).subquery("dashboard_month_units")
    return select(
        headers.c.year, headers.c.month, headers.c.revenue, headers.c.checks,
        func.coalesce(units.c.units, 0).label("units"),
    ).select_from(headers).outerjoin(
        units, (units.c.year == headers.c.year) & (units.c.month == headers.c.month)
    ).order_by(headers.c.year, headers.c.month)


def business_today():
    return datetime.now(ZoneInfo("Europe/Volgograd")).date()


async def dashboard_sales(db, payload):
    today = business_today()
    years = {year: [{"month": n, "revenue": 0, "checks": 0, "units": 0} for n in range(1, 13)]
             for year in (payload.year, payload.year - 1)}
    if payload.managers:
        # Serialize directory loading and calculation with the other expensive
        # API reports. No DB connection is needed for empty/unmatched selections.
        async with heavy_operation("calltrack_dashboard"):
            mapping = await get_client_managers()
            selected = {manager_key(value) for value in payload.managers}
            names = [name for name, owner in mapping.items() if manager_key(owner) in selected]
            if names:
                ensure_headroom()
                async with report_transaction(db) as session:
                    query = monthly_sales_query(payload.year, names, compare=True, as_of=today)
                    for record in (await session.execute(query)).mappings():
                        row = dict(record)
                        year = int(row.pop("year"))
                        years[year][int(row["month"]) - 1] = row
    # items stays numeric and bounded for existing consumers. New renderers use as_of
    # to distinguish future months from real zero-sales months.
    return {"items": years[payload.year], "previous_items": years[payload.year - 1],
            "year": payload.year, "previous_year": payload.year - 1, "as_of": today}
