import asyncio
from datetime import date
from io import BytesIO
import pytest
from openpyxl import load_workbook
from app.routers import promotion_candidates_report as router
from benchmarks.benchmark_promotion_memory import sales_rows


def test_enabled_priority_disabled_legacy_order_filters_and_excel(monkeypatch):
    async def sales(*args):
        return list(sales_rows(2))

    async def clients(*args):
        return None

    async def catalog(*args):
        return {
            f"code:{i:07}": {
                "category": "Посуда",
                "brand": "Бренд",
                "manufacturer": "Завод",
                "stock": 1000 + i,
            }
            for i in range(2)
        }, True

    async def seasonal(rows, *args, **kwargs):
        for row in rows:
            row["seasonality"] = dict(
                status=("Скоро сезон" if row["code"] == "0000000" else "Вне сезона"),
                priority=80 if row["code"] == "0000000" else 0,
                index=2,
                confidence=80,
                period="10–12; пик 11",
                action="Подготовить продвижение",
                reason="Повторяющийся сезон",
            )

    monkeypatch.setattr(router, "_sales", sales)
    monkeypatch.setattr(router, "_catalog", catalog)
    monkeypatch.setattr(router, "get_sales_filter_clients", clients)
    monkeypatch.setattr(router, "attach", seasonal)

    async def run():
        base = dict(date_from=date(2026, 1, 1), date_to=date(2026, 9, 30))
        enabled, _ = await router._dataset(router.Request(**base), None)
        disabled, _ = await router._dataset(
            router.Request(**base, use_seasonality=False), None
        )
        assert enabled[0]["code"] == "0000000"
        assert disabled[0]["code"] == "0000001"
        selected, _ = await router._dataset(
            router.Request(**base, seasonality_status="Скоро сезон"), None
        )
        assert len(selected) == 1 and selected[0]["code"] == "0000000"
        book = load_workbook(BytesIO(router._excel(enabled).body), read_only=True)
        try:
            values = list(book["Кандидаты"].values)
            assert len(values[0]) == 23 and values[0][-7:] == (
                "Статус сезонности",
                "Сезонный индекс",
                "Сезонный приоритет",
                "Уверенность, баллы",
                "Ожидаемый период спроса",
                "Рекомендуемое действие",
                "Причина рекомендации",
            )
            assert values[1][-7:] == (
                "Скоро сезон",
                2,
                80,
                80,
                "10–12; пик 11",
                "Подготовить продвижение",
                "Повторяющийся сезон",
            )
        finally:
            book.close()

    asyncio.run(run())
