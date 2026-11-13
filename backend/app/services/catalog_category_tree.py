"""Small hierarchy projection, not a full product-directory download.

Catalog SectionTree groups section names under canonical category IDs. Report
filters still use exact legacy/section names, never canonical IDs as names.
"""

from sqlalchemy import select, text
from app.models import CatalogProduct, CatalogLookup
from app.services.vrcatalog import get_catalog_tree, VrCatalogError
from fastapi import HTTPException


async def category_tree(db):
    # Fetch directory metadata before opening a PostgreSQL transaction.
    warning = None
    try:
        flat = await get_catalog_tree()
    except VrCatalogError:
        flat = []
        warning = "CatalogVR недоступен. Показана сохранённая локальная иерархия."
    if db.bind.dialect.name == "postgresql":
        for sql in (
            "SET LOCAL max_parallel_workers_per_gather=0",
            "SET LOCAL work_mem='4MB'",
            "SET LOCAL statement_timeout='5s'",
            "SET LOCAL lock_timeout='1s'",
        ):
            await db.execute(text(sql))
    matched = (
        select(CatalogLookup.identity)
        .where(
            CatalogLookup.product_id == CatalogProduct.product_id,
            CatalogLookup.status == "matched",
        )
        .exists()
    )
    rows = (
        await db.execute(
            select(
                CatalogProduct.category_id,
                CatalogProduct.category,
                CatalogProduct.subcategory,
                CatalogProduct.legacy_category,
            )
            .where(matched)
            .distinct()
            .limit(10001)
        )
    ).all()
    if len(rows) > 10000:
        raise HTTPException(503, "Справочник категорий превышает безопасный размер.")
    parents = {}
    represented = set()
    seen = set()
    for category_id, name, section, legacy in rows:
        value = str(legacy or section or "").strip()
        if not value:
            continue
        key = f"category:{category_id}" if category_id is not None else "uncategorized"
        node = parents.setdefault(
            key, {"id": key, "name": name or "Без категории", "children": []}
        )
        if (key, value) not in seen:
            seen.add((key, value))
            node["children"].append(
                {
                    "id": key + ":" + value,
                    "name": section or value,
                    "value": value,
                    "section": section or "",
                    "children": [],
                }
            )
        represented.add(value)
    stack = list(flat)
    extra = set()
    visited = 0
    while stack:
        visited += 1
        if visited > 10000:
            raise HTTPException(
                503, "Справочник категорий превышает безопасный размер."
            )
        item = stack.pop()
        value = str(item.get("code") or item.get("name") or "").strip()
        if value and value not in represented:
            extra.add(value)
        stack.extend(item.get("children") or [])
    if extra:
        parents["unmapped"] = {
            "id": "unmapped",
            "name": "Разделы без данных об иерархии",
            "children": [
                {
                    "id": "unmapped:" + x,
                    "name": x,
                    "value": x,
                    "section": x,
                    "children": [],
                }
                for x in sorted(extra, key=str.casefold)
            ],
        }
    nodes = sorted(parents.values(), key=lambda x: x["name"].casefold())
    for node in nodes:
        node["children"].sort(key=lambda x: x["name"].casefold())
    return {"nodes": nodes, "warning": warning}
