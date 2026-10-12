import asyncio
from datetime import date

from app.routers import category_analytics_report as router
from app.services.sales_dynamics_report import previous_period
from app.services.report_periods import previous_year_period
from test_catalog_directory import database


def test_calendar_month_week_and_leap_year():
    assert previous_period(date(2026, 9, 1), date(2026, 9, 30), "previous_month") == (
        date(2026, 8, 1),
        date(2026, 8, 31),
    )
    assert previous_period(date(2026, 10, 1), date(2026, 10, 11), "current_month") == (
        date(2026, 9, 1),
        date(2026, 9, 11),
    )
    assert previous_period(date(2026, 10, 5), date(2026, 10, 7), "current_week") == (
        date(2026, 9, 28),
        date(2026, 9, 30),
    )
    assert previous_year_period(date(2024, 2, 1), date(2024, 2, 29)) == (
        date(2023, 2, 1),
        date(2023, 2, 28),
    )


def test_two_comparisons_share_catalog_and_include_year_products(monkeypatch):
    periods = []
    mappings = []
    keys = []

    async def clients(*args):
        return ["Клиент"]

    async def product_keys(*args):
        keys.append(args[-1])
        return []

    async def catalog(*args):
        mappings.append(True)
        return [], {}

    async def table(*args):
        return None

    async def rows(db, start, end, *args):
        periods.append((start, end))
        value = 100 if start.year == 2025 else 200 if start.month == 8 else 300
        return [{"category": "Посуда", "revenue": value, "units": 1, "checks": 1}]

    async def checks(*args):
        return 1

    monkeypatch.setattr(router, "_clients", clients)
    monkeypatch.setattr(router, "_product_keys", product_keys)
    monkeypatch.setattr(router, "_catalog_map", catalog)
    monkeypatch.setattr(router, "_mapping_table", table)
    monkeypatch.setattr(router, "_category_rows", rows)
    monkeypatch.setattr(router, "_scope_checks", checks)
    result = asyncio.run(
        router.report(
            date(2026, 9, 1),
            date(2026, 9, 30),
            "previous_month",
            [],
            None,
            None,
            None,
            None,
        )
    )
    assert len(mappings) == 1
    assert keys == [(date(2025, 9, 1), date(2025, 9, 30))]
    assert result["metrics"]["revenue"]["previous"] == 200
    assert result["year_comparison"]["metrics"]["revenue"]["previous"] == 100
    assert result["previous_period"] == {
        "start": date(2026, 8, 1),
        "end": date(2026, 8, 31),
    }
    assert len(periods) == 3


def test_product_sales_deduplicates_documents_and_preserves_filters(monkeypatch):
    async def clients(db, manager, buyer_type):
        assert manager == "Менеджер" and buyer_type == "HoReCa"
        return None

    monkeypatch.setattr(router, "_clients", clients)

    async def run():
        async with database([("001", "A"), ("001", "A"), ("002", "A")]) as (_, factory):
            async with factory() as db:
                kwargs = dict(
                    date_from=date(2026, 9, 1),
                    date_to=date(2026, 9, 30),
                    period_kind="previous_month",
                    code=" 001 ",
                    article="A",
                    stores=["Магазин"],
                    manager="Менеджер",
                    buyer_type="HoReCa",
                    page=1,
                    page_size=50,
                    db=db,
                )
                result = await router.product_sales(**kwargs)
                assert result["total"] == 1 and len(result["items"]) == 1
                assert result["items"][0]["document_number"] == "1"
                assert set(result["items"][0]) == {
                    "id",
                    "sale_date",
                    "document_number",
                    "client",
                    "department",
                    "total_amount",
                    "base_amount",
                    "discount_percent",
                    "author",
                    "price_type",
                }
                assert (await router.product_sales(**{**kwargs, "stores": ["Другой"]}))[
                    "total"
                ] == 0
                assert (await router.product_sales(**{**kwargs, "code": "999"}))[
                    "total"
                ] == 0
                assert (await router.product_sales(**{**kwargs, "page": 2}))[
                    "items"
                ] == []
                assert (await router.product_sales(**{**kwargs, "code": None}))[
                    "total"
                ] == 0  # article never steals a coded SKU

    asyncio.run(run())
