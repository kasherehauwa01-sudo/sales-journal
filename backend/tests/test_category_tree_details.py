import asyncio
from datetime import date, datetime, timezone
import sqlalchemy as sa
from app.models import CatalogProduct, CatalogLookup, SaleItem, Sale
from app.routers import category_analytics_report as report
from app.services import catalog_category_tree as tree
from app.services.catalog_attributes import identity
from app.services.vrcatalog import VrCatalogError
from test_catalog_directory import database


async def mapping(db):
    await db.execute(
        sa.text(
            "CREATE TABLE test_category_map(code text,article text,category text,lookup_key text)"
        )
    )
    await db.execute(
        sa.text(
            "INSERT INTO test_category_map VALUES ('001','dup','Чашки','c:001'),(NULL,'dup','Тарелки','a:dup'),('1','other','Нули','c:1')"
        )
    )
    return sa.table(
        "test_category_map",
        *[
            sa.column(x, sa.String)
            for x in ("code", "article", "category", "lookup_key")
        ]
    )


def test_join_identical_code_priority_zeros_null_blank_and_missing():
    async def run():
        async with database(
            [
                (" 001 ", "dup"),
                (None, " DUP "),
                (" ", "dup"),
                ("001", "other"),
                ("1", "dup"),
                ("unknown", "dup"),
                (None, None),
            ]
        ) as (_, factory):
            async with factory() as db:
                table = await mapping(db)
                code, article = sa.func.lower(
                    sa.func.trim(SaleItem.code)
                ), sa.func.lower(sa.func.trim(SaleItem.article))
                old = sa.or_(
                    sa.and_(table.c.code.is_not(None), code == table.c.code),
                    sa.and_(
                        sa.or_(
                            SaleItem.code.is_(None), sa.func.trim(SaleItem.code) == ""
                        ),
                        table.c.code.is_(None),
                        article == table.c.article,
                    ),
                )
                _, new, _ = report._mapped_items(table)

                async def result(join):
                    return list(
                        (
                            await db.execute(
                                sa.select(
                                    SaleItem.id,
                                    sa.func.coalesce(table.c.category, "Без категории"),
                                )
                                .select_from(SaleItem)
                                .outerjoin(table, join)
                                .order_by(SaleItem.id)
                            )
                        ).all()
                    )

                assert await result(old) == await result(new)
                assert [x[1] for x in await result(new)] == [
                    "Чашки",
                    "Тарелки",
                    "Тарелки",
                    "Чашки",
                    "Нули",
                    "Без категории",
                    "Без категории",
                ]
                assert (
                    await report._selected_checks(
                        db,
                        date(2026, 9, 1),
                        date(2026, 9, 30),
                        [],
                        None,
                        table,
                        ["Чашки", "Тарелки"],
                    )
                    == 1
                )

    asyncio.run(run())


def test_tree_preserves_catalog_parent_and_all_remote_values_and_errors(monkeypatch):
    async def remote():
        return [
            {
                "name": "Не синхронизировано",
                "code": "Не синхронизировано",
                "children": [],
            },
            {"name": "Чашки", "code": "Чашки", "children": []},
        ]

    monkeypatch.setattr(tree, "get_catalog_tree", remote)

    async def run():
        async with database([]) as (_, factory):
            async with factory() as db:
                for i, name in [(1, "Чашки"), (2, "Тарелки"), (3, "Устаревший")]:
                    db.add(
                        CatalogProduct(
                            product_id=i,
                            code=str(i),
                            category="Посуда",
                            category_id=7,
                            subcategory=name,
                            legacy_category=name,
                            horeca=False,
                            source_updated_at=datetime.now(timezone.utc),
                            synced_at=datetime.now(timezone.utc),
                        )
                    )
                    await db.flush()
                    if i < 3:
                        db.add(
                            CatalogLookup(
                                identity=identity(str(i), None),
                                code=str(i),
                                status="matched",
                                product_id=i,
                                matched_by="code",
                            )
                        )
                await db.commit()
                result = await tree.category_tree(db)
                parent = next(x for x in result["nodes"] if x["id"] == "category:7")
                assert parent["name"] == "Посуда"
                assert [x["value"] for x in parent["children"]] == ["Тарелки", "Чашки"]
                assert (
                    next(x for x in result["nodes"] if x["id"] == "unmapped")[
                        "children"
                    ][0]["value"]
                    == "Не синхронизировано"
                )
                assert "Устаревший" not in str(result)

                async def failed():
                    raise VrCatalogError("PRIVATE_SENTINEL")

                monkeypatch.setattr(tree, "get_catalog_tree", failed)
                fallback = await tree.category_tree(db)
                assert (
                    len(fallback["nodes"]) == 1
                    and fallback["warning"]
                    and "PRIVATE_SENTINEL" not in str(fallback)
                )

    asyncio.run(run())


def test_multiple_category_checks_and_excel_use_selected_values(monkeypatch):
    from app.services.category_analytics import aggregate_categories
    from io import BytesIO
    from openpyxl import load_workbook

    async def run():
        async with database([("001", "dup"), (None, "dup"), ("1", "dup")]) as (
            _,
            factory,
        ):
            async with factory() as db:
                table = await mapping(db)
                start, end, old_start, old_end = (
                    date(2026, 9, 1),
                    date(2026, 9, 30),
                    date(2026, 8, 1),
                    date(2026, 8, 31),
                )
                current = await report._category_rows(db, start, end, [], None, table)
                rows = aggregate_categories(current, [])

                async def scope(*args, **kwargs):
                    base = (
                        start,
                        end,
                        old_start,
                        old_end,
                        None,
                        None,
                        table,
                        {},
                        rows,
                        1,
                        0,
                    )
                    return (
                        (*base, rows, 0, date(2025, 9, 1), date(2025, 9, 30))
                        if kwargs.get("include_year")
                        else base
                    )

                monkeypatch.setattr(report, "_scope", scope)
                result = await report.report(
                    start, end, stores=[], db=db, categories=["Чашки", "Тарелки"]
                )
                assert {x["category"] for x in result["categories"]} == {
                    "Чашки",
                    "Тарелки",
                }
                assert result["metrics"]["checks"]["current"] == 1
                assert result["metrics"]["revenue"]["current"] == 24.5
                output = await report.export(
                    start, end, stores=[], db=db, categories=["Чашки"]
                )
                book = load_workbook(BytesIO(output.body), read_only=True)
                try:
                    assert list(book["Категории"].values)[1][0] == "Чашки"
                    products = list(book["Товары"].values)
                    assert len(products) == 2 and products[1][0] == "Чашки"
                finally:
                    book.close()

    asyncio.run(run())


def test_large_selection_post_contract(monkeypatch):
    from fastapi import FastAPI
    import httpx
    from app.database import get_db

    seen = []

    async def fake(**kwargs):
        seen.append(kwargs)
        return {"ok": True}

    monkeypatch.setattr(report, "report", fake)

    async def run():
        app = FastAPI()
        app.include_router(report.router)

        async def db():
            yield None

        app.dependency_overrides[get_db] = db
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            labels = ["Категория " + str(i) for i in range(300)]
            response = await client.post(
                "/reports/category-analytics",
                json={
                    "date_from": "2026-09-01",
                    "date_to": "2026-09-30",
                    "categories": labels,
                },
            )
            assert response.status_code == 200 and seen[0]["categories"] == labels
            assert seen[0]["date_from"] == date(2026, 9, 1)
            bad = await client.post(
                "/reports/category-analytics",
                json={
                    "date_from": "2026-09-01",
                    "date_to": "2026-09-30",
                    "unknown_filter": "do not ignore",
                },
            )
            assert bad.status_code == 422 and len(seen) == 1

    asyncio.run(run())


def test_detail_route_preserves_category_names_with_slashes():
    from starlette.routing import Match

    route = next(r for r in report.router.routes if r.name == "details")
    match, scope = route.matches(
        {
            "type": "http",
            "method": "GET",
            "path": "/reports/category-analytics/Посуда/Кухня/details",
            "root_path": "",
        }
    )
    assert match == Match.FULL
    assert scope["path_params"]["category_name"] == "Посуда/Кухня"
