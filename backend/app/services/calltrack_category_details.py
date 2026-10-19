"""Lazy, all-history drilldown for the exact selected Calltrack client names."""
from decimal import Decimal
import json

from sqlalchemy import func, select
from app.models import Sale, SaleItem
from app.services.catalog_attributes import local_catalog


def aggregate_details(rows, catalog, category, subcategory=None):
    """Aggregate all selected clients together before ranking; keep Decimal arithmetic."""
    groups = {}
    parent_revenue = Decimal(0)
    parent_quantity = Decimal(0)
    for row in rows:
        info = catalog.get(row["key"], {})
        row_category = info.get("catalog_category") or info.get("category") or "Без категории"
        if row_category != category:
            continue
        section = str(info.get("section") or "").strip() or "Без подкатегории"
        if subcategory is not None and section != subcategory:
            continue
        revenue = row["revenue"] or Decimal(0)
        quantity = row["quantity"] or Decimal(0)
        parent_revenue += revenue
        parent_quantity += quantity
        if subcategory is None:
            key, name = section, section
        else:
            identity = (["product", info["product_id"]] if info.get("product_id") is not None else
                        ["source", row.get("code"), row.get("article"), str(row.get("name") or "").strip().casefold()])
            key = json.dumps(identity, ensure_ascii=False)
            name = row.get("name") or row.get("article") or row.get("code") or "Товар без названия"
        if key not in groups:
            groups[key] = {"key": key, "name": name, "revenue": Decimal(0), "quantity": Decimal(0)}
            if subcategory is not None:
                groups[key].update(code=row.get("code"), article=row.get("article"), product_id=info.get("product_id"))
        group = groups[key]
        # Deterministic label/metadata when several source identities map to one catalog product.
        if str(name) < str(group["name"]):
            group["name"] = name
            if subcategory is not None:
                group.update(code=row.get("code"), article=row.get("article"))
        group["revenue"] += revenue
        group["quantity"] += quantity
    return groups, parent_revenue, parent_quantity


async def category_details(db, payload):
    names = sorted({name.strip().lower() for name in payload.names if name.strip()})
    query = (select(SaleItem.code, SaleItem.article, func.max(SaleItem.name).label("name"),
                    func.coalesce(func.sum(SaleItem.quantity), 0).label("quantity"),
                    func.coalesce(func.sum(SaleItem.quantity * SaleItem.actual_price), 0).label("revenue"))
             .join(Sale, Sale.id == SaleItem.sale_id)
             .where(func.lower(func.trim(Sale.client)).in_(names))
             .group_by(SaleItem.code, SaleItem.article, func.lower(func.trim(SaleItem.name))))
    # Bounded catalog lookups, not a request/query for each sale or product.
    groups, total, quantity = {}, Decimal(0), Decimal(0)
    result = await db.stream(query)
    index = 0
    async for batch in result.mappings().partitions(1000):
        rows = [dict(row, key=index + offset) for offset, row in enumerate(batch)]
        index += len(rows)
        catalog = await local_catalog(rows, db)
        part, revenue, units = aggregate_details(rows, catalog, payload.category, payload.subcategory)
        total += revenue
        quantity += units
        for key, row in part.items():
            if key not in groups:
                groups[key] = row
            else:
                existing = groups[key]
                existing["revenue"] += row["revenue"]
                existing["quantity"] += row["quantity"]
                if row["name"] < existing["name"]:
                    existing.update({k: v for k, v in row.items() if k not in {"revenue", "quantity"}})
    limit = 10 if payload.subcategory is None else 20
    items = sorted(groups.values(), key=lambda row: (-row["revenue"], row["name"], row["key"]))[:limit]
    return {"level": "subcategories" if payload.subcategory is None else "products",
            "category": payload.category, "subcategory": payload.subcategory,
            "items": items, "total_count": len(groups), "parent_revenue": total, "parent_quantity": quantity}
