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
