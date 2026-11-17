"""Bounded client predicates and item filters against resolved local catalog pairs."""
from sqlalchemy import String, any_, bindparam, cast, func, select
from sqlalchemy.dialects.postgresql import ARRAY
from app.models import Sale, SaleItem, CatalogLookup, CatalogProduct


def array_membership(column, values, *, parameter_name="report_values"):
    """One typed PostgreSQL array bind regardless of selection size.

    Normalization is explicit at the caller so case-sensitive fields such as
    department names retain their existing semantics. ANY never duplicates rows.
    """
    if not values:
        return False
    return column == any_(cast(
        bindparam(parameter_name, value=list(values), unique=True), ARRAY(String)
    ))


def client_names_condition(column, clients):
    names = sorted({v.strip().lower() for v in clients if v and v.strip()})
    return array_membership(func.lower(func.trim(column)), names, parameter_name="report_clients")


def report_item_condition(brands=None, categories=None):
    if not brands and not categories:
        return True
    catalog_conditions = []
    if brands:
        catalog_conditions.append(array_membership(
            func.lower(func.trim(CatalogProduct.brand)),
            sorted({b.strip().lower() for b in brands}),
            parameter_name="report_brands",
        ))
    if categories:
        # The tree exposes the legacy value, falling back to the section.
        category = func.trim(func.coalesce(
            func.nullif(CatalogProduct.legacy_category, ""), CatalogProduct.subcategory, ""
        ))
        catalog_conditions.append(array_membership(
            category, sorted({c.strip() for c in categories if c.strip()}),
            parameter_name="report_categories",
        ))
    norm = lambda column: func.lower(func.trim(func.coalesce(column, "")))
    # Match the entire resolved pair, never an ambiguous article alone.
    return select(CatalogLookup.identity).join(
        CatalogProduct, CatalogProduct.product_id == CatalogLookup.product_id
    ).where(
        CatalogLookup.status == "matched",
        norm(CatalogLookup.code) == norm(SaleItem.code),
        norm(CatalogLookup.article) == norm(SaleItem.article),
        *catalog_conditions,
    ).correlate(SaleItem).exists()


def report_sale_conditions(brands=None, categories=None):
    if not brands and not categories:
        return []
    return [select(SaleItem.id).where(
        SaleItem.sale_id == Sale.id, report_item_condition(brands, categories)
    ).correlate(Sale).exists()]


def brand_item_condition(brands):
    return report_item_condition(brands)


def brand_sale_conditions(brands):
    return report_sale_conditions(brands)


def report_sale_amount(brands=None, categories=None):
    if not brands and not categories:
        return Sale.total_amount
    return select(func.coalesce(func.sum(SaleItem.quantity * SaleItem.actual_price), 0)).where(
        SaleItem.sale_id == Sale.id, report_item_condition(brands, categories)
    ).correlate(Sale).scalar_subquery()
