import asyncio
from datetime import date

from sqlalchemy.dialects import postgresql

from app.routers.store_analytics_report import _aggregates, _sales


def test_sales_query_uses_one_grouped_row_per_check():
    query = _sales(date(2026, 9, 1), date(2026, 9, 30), [], None)
    sql = str(query.select().compile(dialect=postgresql.dialect()))

    assert "LEFT OUTER JOIN sale_items" in sql
    assert "GROUP BY sales.id" in sql
    assert "filtered_store_sales" not in sql


def test_sales_query_keeps_store_and_client_filters():
    query = _sales(
        date(2026, 9, 1),
        date(2026, 9, 30),
        ["Авиаторов"],
        ["Розничный клиент"],
    )
    compiled = query.select().compile(dialect=postgresql.dialect())
    sql = str(compiled)

    assert "sales.department IN" in sql
    assert "lower(trim(sales.client)) IN" in sql
    assert date(2026, 9, 1) in compiled.params.values()
    assert date(2026, 9, 30) in compiled.params.values()


def test_aggregates_do_not_multiply_revenue_by_sale_items():
    class Row:
        def __init__(self, store, **values):
            self.store = store
            self.__dict__.update(values)

    class Result:
        def __init__(self, rows):
            self.rows = rows

        def all(self):
            return self.rows

    class Db:
        def __init__(self):
            self.calls = 0

        async def execute(self, _query):
            self.calls += 1
            if self.calls == 1:
                return Result([Row("Магазин", revenue=1000, checks=2, discount_sum=20)])
            return Result([Row("Магазин", items=7)])

    rows = asyncio.run(_aggregates(Db(), date(2026, 9, 1), date(2026, 9, 30), [], None))

    assert rows == [
        {
            "store": "Магазин",
            "revenue": 1000,
            "checks": 2,
            "average_check": 500,
            "items": 7,
            "items_per_check": 3.5,
            "average_discount": 10,
        }
    ]


def test_both_comparisons_reuse_current_and_client_filter(monkeypatch):
    from app.routers import store_analytics_report as router
    from app.services.store_analytics_report import calculated_store_metrics

    calls = []

    async def clients(*args):
        calls.append("clients")
        return ["Клиент"]

    async def aggregates(db, start, end, stores, selected_clients):
        calls.append(start)
        assert selected_clients == ["Клиент"]
        value = 50 if start.year == 2025 else 20 if start.month == 8 else 100
        return [{"store": "Магазин", **calculated_store_metrics(value, 1, 2, 0)}]

    monkeypatch.setattr(router, "_clients", clients)
    monkeypatch.setattr(router, "_aggregates", aggregates)
    result = asyncio.run(
        router.report(
            date(2026, 9, 1),
            date(2026, 9, 30),
            period_kind="previous_month",
            comparison_mode="previous_period",
            stores=[],
            db=None,
            include_both=True,
        )
    )
    assert calls == ["clients", date(2026, 9, 1), date(2026, 8, 1), date(2025, 9, 1)]
    assert (
        result["comparisons"]["previous_year"]["metrics"]["revenue"]["previous"] == 50
    )
    assert (
        result["comparisons"]["previous_period"]["metrics"]["revenue"]["previous"] == 20
    )
    assert result["metrics"] == result["comparisons"]["previous_period"]["metrics"]
