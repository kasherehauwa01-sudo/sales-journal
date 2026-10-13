import asyncio

import pytest
from fastapi import HTTPException

from app.routers import sales_dynamics_report
from app.services.clients_vr import ClientsVrError


def test_client_filter_uses_sales_database_intersection(monkeypatch):
    db = object()
    calls = []

    async def clients(actual_db, manager, buyer_type):
        calls.append((actual_db, manager, buyer_type))
        return ["Розничный клиент"]

    monkeypatch.setattr(sales_dynamics_report, "get_sales_filter_clients", clients)

    result = asyncio.run(
        sales_dynamics_report._client_filter(db, "Менеджер", "Розница")
    )

    assert result == ["Розничный клиент"]
    assert calls == [(db, "Менеджер", "Розница")]


def test_client_filter_returns_controlled_clients_vr_error(monkeypatch):
    async def clients(_db, _manager, _buyer_type):
        raise ClientsVrError("clients_vr недоступен")

    monkeypatch.setattr(sales_dynamics_report, "get_sales_filter_clients", clients)

    with pytest.raises(HTTPException) as caught:
        asyncio.run(sales_dynamics_report._client_filter(object(), None, "Розница"))

    assert caught.value.status_code == 502
    assert caught.value.detail == "clients_vr недоступен"


def test_multiple_selected_clients_intersect_manager_for_both_periods(monkeypatch):
    from datetime import date

    calls = []

    async def allowed(*args):
        return [" Клиент А ", "Клиент Б"]

    async def metrics(db, start, end, department, clients, client):
        calls.append((start, end, clients))
        return {
            "revenue": 1,
            "sales_count": 1,
            "items_count": 1,
            "average_check": 1,
            "average_items_per_sale": 1,
        }

    async def chart(*args):
        return []

    monkeypatch.setattr(sales_dynamics_report, "_client_filter", allowed)
    monkeypatch.setattr(sales_dynamics_report, "_metrics", metrics)
    monkeypatch.setattr(sales_dynamics_report, "_chart", chart)
    result = asyncio.run(
        sales_dynamics_report.report(
            date(2026, 9, 1),
            date(2026, 9, 30),
            period_kind="previous_month",
            group_by="day",
            manager="М",
            comparison_mode="previous_year",
            db=None,
            selected_clients=["КЛИЕНТ А", "Клиент В"],
        )
    )
    assert all(call[2] == ["клиент а"] for call in calls)
    assert result["previous_period"] == {
        "start": date(2025, 9, 1),
        "end": date(2025, 9, 30),
    }


def test_both_comparisons_share_current_calculation_and_filters(monkeypatch):
    from datetime import date

    calls = []

    async def allowed(*args):
        return ["Клиент А"]

    async def metrics(db, start, end, department, clients, client):
        calls.append(("metrics", start, end, clients))
        value = 50 if start.year == 2025 else 20 if start.month == 8 else 100
        return {
            "revenue": value,
            "sales_count": 1,
            "items_count": 2,
            "average_check": value,
            "average_items_per_sale": 2,
        }

    async def chart(db, start, end, grouping, department, clients, client):
        calls.append(("chart", start, end, clients))
        return []

    monkeypatch.setattr(sales_dynamics_report, "_client_filter", allowed)
    monkeypatch.setattr(sales_dynamics_report, "_metrics", metrics)
    monkeypatch.setattr(sales_dynamics_report, "_chart", chart)
    result = asyncio.run(
        sales_dynamics_report.report(
            date(2026, 9, 1),
            date(2026, 9, 30),
            period_kind="previous_month",
            group_by="day",
            comparison_mode="previous_period",
            include_both=True,
            db=None,
            selected_clients=["Клиент А"],
        )
    )
    comparisons = result["comparisons"]
    assert comparisons["previous_period"]["period"] == {
        "start": date(2026, 8, 1),
        "end": date(2026, 8, 31),
    }
    assert comparisons["previous_year"]["period"] == {
        "start": date(2025, 9, 1),
        "end": date(2025, 9, 30),
    }
    assert comparisons["previous_period"]["metrics"]["revenue"]["previous"] == 20
    assert comparisons["previous_year"]["metrics"]["revenue"]["previous"] == 50
    assert len(calls) == 6 and all(call[3] == ["клиент а"] for call in calls)
