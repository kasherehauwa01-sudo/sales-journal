import asyncio
from datetime import date

from app.services import sales_client_filters
from app.services.store_analytics_report import calculated_store_metrics, comparable_period, compare_metrics, merge_store_rows


def test_comparable_period_has_same_length():
    assert comparable_period(date(2026, 9, 1), date(2026, 9, 22)) == (date(2026, 8, 10), date(2026, 8, 31))


def test_store_metrics_use_aggregated_source_values():
    metrics = calculated_store_metrics(1200, 3, 9, 45)
    assert metrics == {"revenue": 1200, "checks": 3, "average_check": 400, "items": 9, "items_per_check": 3, "average_discount": 15}


def test_comparison_handles_zero_previous_value():
    current = calculated_store_metrics(100, 1, 2, 10)
    previous = calculated_store_metrics(0, 0, 0, 0)
    compared = compare_metrics(current, previous)
    assert compared["revenue"]["difference"] == 100
    assert compared["revenue"]["change_percent"] is None


def test_stores_from_both_periods_are_kept():
    current = [{"store": "А", **calculated_store_metrics(100, 1, 2, 5)}]
    previous = [{"store": "Б", **calculated_store_metrics(50, 1, 1, 3)}]
    result = merge_store_rows(current, previous)
    assert [row["store"] for row in result] == ["А", "Б"]
    assert result[0]["metrics"]["revenue"]["current"] == 100
    assert result[1]["metrics"]["revenue"]["previous"] == 50


def test_store_without_name_does_not_break_report():
    current = [{"store": None, **calculated_store_metrics(100, 1, 1, 0)}]
    result = merge_store_rows(current, [])
    assert result[0]["store"] == "Без подразделения"
    assert result[0]["metrics"]["revenue"]["current"] == 100


def test_manager_and_buyer_type_clients_are_intersected(monkeypatch):
    db = object()

    async def managers(_value): return ["Клиент 1", "Клиент 2"]
    async def buyers(_db, _value): return [" клиент 2 ", "Клиент 3"]

    monkeypatch.setattr(sales_client_filters, "get_manager_clients", managers)
    monkeypatch.setattr(sales_client_filters, "get_sales_buyer_type_clients", buyers)

    assert asyncio.run(
        sales_client_filters.get_sales_filter_clients(db, "Менеджер", "Розница")
    ) == ["Клиент 2"]


def test_single_client_filter_is_returned_without_intersection(monkeypatch):
    db = object()

    async def buyers(_db, _value): return ["Клиент 1"]

    monkeypatch.setattr(sales_client_filters, "get_sales_buyer_type_clients", buyers)

    assert asyncio.run(
        sales_client_filters.get_sales_filter_clients(db, None, "Нет")
    ) == ["Клиент 1"]
