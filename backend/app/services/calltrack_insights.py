"""Client-scoped product analytics and indexed persistent RFM lookup only."""
from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy import func, literal, select

from app.models import Sale, SaleItem
from app.schemas.calltrack import CalltrackInsightsResponse
from app.services.catalog_attributes import local_catalog
from app.services.rfm_snapshot import current_date, lookup_client


async def client_products(db, name, start, end):
    # Group before catalog enrichment; never load Sale.items or query per sale.
    rows = (await db.execute(select(
        Sale.id.label("sale_id"), SaleItem.code, SaleItem.article, SaleItem.name,
        func.coalesce(func.sum(SaleItem.quantity), 0).label("quantity")
    ).select_from(Sale).outerjoin(SaleItem, SaleItem.sale_id == Sale.id).where(
        func.lower(func.trim(Sale.client)) == func.lower(func.trim(literal(name))),
        Sale.sale_date.between(start, end)
    ).group_by(Sale.id, SaleItem.code, SaleItem.article, SaleItem.name))).mappings().all()
    products, quantities = set(), defaultdict(Decimal)
    catalog_rows = []
    for i, row in enumerate(rows):
        quantities[row["sale_id"]] += row["quantity"]
        if row["name"] is not None:
            products.add(next(v for v in (row["code"], row["article"], row["name"]) if v is not None))
            catalog_rows.append({"key": i, "code": row["code"], "article": row["article"]})
    catalog = await local_catalog(catalog_rows, db) if catalog_rows else {}
    categories = defaultdict(Decimal)
    for i, row in enumerate(rows):
        category = catalog.get(i, {}).get("catalog_category")
        if category:
            categories[category] += row["quantity"]
    return {"unique_products": len(products),
            "top_categories": [{"category": key, "quantity": value} for key, value in
                               sorted(categories.items(), key=lambda pair: (-pair[1], pair[0]))[:10]],
            "sale_quantities": [{"sale_id": key, "quantity": value} for key, value in sorted(quantities.items())]}


async def client_insights(db, name):
    snapshot, client = await lookup_client(db, name)
    if snapshot is None:
        raise HTTPException(503, detail={"code": "rfm_snapshot_unavailable",
                            "message": "RFM snapshot ещё не построен"})
    calculated = snapshot.calculated_at
    if calculated.tzinfo is None:
        calculated = calculated.replace(tzinfo=timezone.utc)
    age = max(0, int((datetime.now(timezone.utc) - calculated).total_seconds()))
    products = await client_products(db, name, snapshot.period_from, snapshot.period_to)
    rfm = None
    if client is not None:
        fields = ("segment", "rfm_code", "tags", "r_score", "f_score", "m_score", "recency_days",
                  "frequency", "monetary", "average_check", "first_purchase", "last_purchase")
        rfm = {key: getattr(client, key) for key in fields}
        rfm.update({key: client.metrics.get(key) for key in (
            "purchases_per_month", "average_interval", "cycle_overdue")})
    return CalltrackInsightsResponse(
        status="ready" if client else "client_not_in_snapshot",
        client_key=client.client_key if client else name.strip().casefold(), rfm=rfm,
        snapshot={"id": snapshot.id, "calculated_at": calculated, "period_from": snapshot.period_from,
                  "period_to": snapshot.period_to, "age_seconds": age,
                  "stale": snapshot.period_to < current_date()}, **products)
