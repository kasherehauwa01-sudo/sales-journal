import asyncio
from datetime import date
from io import BytesIO
from openpyxl import load_workbook
from app.routers import promotion_candidates_report as router
from benchmarks.benchmark_promotion_memory import sales_rows


def test_minimum_filters_table_kpis_and_export_not_only_status(monkeypatch):
    stocks = [63, 199, 200, 201, None, float("inf")]

    async def sales(*args):
        return list(sales_rows(len(stocks)))

    async def catalog(*args):
        return {
            f"code:{i:07}": {
                "category": "Посуда",
                "brand": "Бренд",
                "manufacturer": "Завод",
                "stock": 10000,
                "aviatorov_stock": stock,
            }
            for i, stock in enumerate(stocks)
        }, True

    async def clients(*args):
        return None

    async def seasons(rows, *args, **kwargs):
        for row in rows:
            row["seasonality"] = {"status": "Недостаточно данных", "priority": 0}

    async def metadata(*args):
        return {}

    monkeypatch.setattr(router, "_sales", sales)
    monkeypatch.setattr(router, "_catalog", catalog)
    monkeypatch.setattr(router, "get_sales_filter_clients", clients)
    monkeypatch.setattr(router, "attach", seasons)
    monkeypatch.setattr(router, "metadata", metadata)
    monkeypatch.setattr(router, "ensure_headroom", lambda: None)

    async def run():
        data = router.Request(
            date_from=date(2026, 1, 1),
            date_to=date(2026, 9, 30),
            criteria={"min_stock": 200},
        )
        result = await router.report(data, None)
        assert result["total"] == 2
        assert [row["aviatorov_stock"] for row in result["items"]] == [200, 201]
        assert result["summary"]["candidates"] == 2
        assert result["summary"]["excess"] == 2
        output = await router.export(data, None)
        book = load_workbook(BytesIO(output.body), read_only=True)
        try:
            rows = list(book["Кандидаты"].values)
            assert len(rows) == 3
            assert [row[23] for row in rows[1:]] == [200, 201]
        finally:
            book.close()
        data.criteria.min_stock = None
        unfiltered = await router.report(data, None)
        assert unfiltered["total"] == len(stocks)

    asyncio.run(run())
