"""Bounded client predicates and brand filters against resolved local catalog pairs."""
from sqlalchemy import String, any_, bindparam, cast, func, select
from sqlalchemy.dialects.postgresql import ARRAY
from app.models import Sale, SaleItem, CatalogLookup, CatalogProduct


def client_names_condition(column, clients):
    names = sorted({v.strip().lower() for v in clients if v and v.strip()})
    if not names:
        return False
    return func.lower(func.trim(column)) == any_(cast(
        bindparam("report_clients", value=names, unique=True), ARRAY(String)
    ))


def brand_item_condition(brands):
    if not brands:
        return True
    norm = lambda column: func.lower(func.trim(func.coalesce(column, "")))
    # Match the entire resolved pair, never an ambiguous article alone.
    return select(CatalogLookup.identity).join(
        CatalogProduct, CatalogProduct.product_id == CatalogLookup.product_id
    ).where(
        CatalogLookup.status == "matched",
        norm(CatalogLookup.code) == norm(SaleItem.code),
        norm(CatalogLookup.article) == norm(SaleItem.article),
        func.lower(func.trim(CatalogProduct.brand)).in_([b.strip().lower() for b in brands]),
    ).correlate(SaleItem).exists()


def brand_sale_conditions(brands):
    if not brands:
        return []
    return [select(SaleItem.id).where(
        SaleItem.sale_id == Sale.id, brand_item_condition(brands)
    ).correlate(Sale).exists()]


def report_sale_amount(brands):
    if not brands:
        return Sale.total_amount
    return select(func.coalesce(func.sum(SaleItem.quantity * SaleItem.actual_price), 0)).where(
        SaleItem.sale_id == Sale.id, brand_item_condition(brands)
    ).correlate(Sale).scalar_subquery()
