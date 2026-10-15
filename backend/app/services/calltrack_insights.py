"""Lifetime product facts and the existing population-based RFM classification."""
import asyncio
import time
from dataclasses import asdict
from datetime import date
from decimal import Decimal, ROUND_HALF_UP

from sqlalchemy import func, select

from app.models import Sale, SaleItem
from app.services.catalog_attributes import local_catalog
from app.services.client_identity import normalize_client_name
from app.services.rfm import SegmentSettings, enrich
from app.services.rfm_data import rfm_rows

_rfm_lock = asyncio.Lock()
_rfm_snapshot = None


async def population_rfm(db, as_of):
    # All clients are scored together, never just the requested client. Reuse the
    # snapshot for five minutes, including across different client cards.
    global _rfm_snapshot
    async with _rfm_lock:
        if _rfm_snapshot and _rfm_snapshot[0] == as_of and time.monotonic() - _rfm_snapshot[1] < 300:
            return _rfm_snapshot[2]
        rows = await rfm_rows(db, date.min, as_of, None)
        rows, _ = enrich(rows, as_of, SegmentSettings())
        result = {normalize_client_name(row['client']): {
            'segment': row['segment'], 'code': row['rfm_code'], 'tags': row['tags'],
        } for row in rows}
        _rfm_snapshot = (as_of, time.monotonic(), result)
        return result


def product_key(row, info):
    if info.get('product_id') is not None:
        return ('product', info['product_id'])
    for field in ('code', 'article', 'name'):
        value = str(row.get(field) or '').strip().casefold()
        if value:
            return (field, value)
    return ('unknown', row['key'])


def product_summary(rows, catalog):
    products, categories = set(), {}
    missing = set()
    for row in rows:
        info = catalog.get(row['key'], {})
        identity = product_key(row, info)
        products.add(identity)
        category = info.get('catalog_category') or info.get('category')
        if not category:
            missing.add(identity)
            category = 'Без категории'
        entry = categories.setdefault(category, {'category': category, 'revenue': Decimal(0), 'quantity': Decimal(0)})
        entry['revenue'] += row['revenue'] or Decimal(0)
        entry['quantity'] += row['quantity'] or Decimal(0)
    top = sorted(categories.values(), key=lambda row: (-row['revenue'], row['category']))[:20]
    for row in top:
        row['revenue'] = row['revenue'].quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    return {'unique_products': len(products), 'top_categories': top, 'uncategorized_products': len(missing)}


async def client_insights(db, name, as_of):
    key = normalize_client_name(name)
    conditions = [func.lower(func.trim(Sale.client)) == key]
    # Quantities are grouped in SQL, not one detail HTTP/SQL request per sale.
    quantities = (await db.execute(select(
        Sale.id.label('sale_id'), func.coalesce(func.sum(SaleItem.quantity), 0).label('quantity'),
    ).outerjoin(SaleItem, SaleItem.sale_id == Sale.id).where(*conditions).group_by(Sale.id))).mappings().all()
    products = (await db.execute(select(
        SaleItem.code, SaleItem.article, func.max(SaleItem.name).label('name'),
        func.sum(SaleItem.quantity).label('quantity'),
        func.coalesce(func.sum(SaleItem.quantity * SaleItem.actual_price), 0).label('revenue'),
    ).join(Sale, Sale.id == SaleItem.sale_id).where(*conditions)
        .group_by(SaleItem.code, SaleItem.article, func.lower(func.trim(SaleItem.name))))).mappings().all()
    rows = [dict(row, key=str(index)) for index, row in enumerate(products)]
    catalog = await local_catalog(rows, db)
    summary = product_summary(rows, catalog)
    population = await population_rfm(db, as_of)
    return {**summary, 'name': name, 'as_of': as_of, 'rfm': population.get(key),
            'sale_quantities': [dict(row) for row in quantities],
            'rfm_settings': asdict(SegmentSettings())}
