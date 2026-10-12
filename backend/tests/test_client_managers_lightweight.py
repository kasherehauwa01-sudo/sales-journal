import asyncio
import io
import json
import sys
from datetime import date
from types import SimpleNamespace
from urllib.error import HTTPError

import pytest
from fastapi import HTTPException

from app.services import clients_vr
from app.services.product_analytics import clients_for_managers


@pytest.fixture(autouse=True)
def clear_cache():
    clients_vr.cache.clear()
    yield
    clients_vr.cache.clear()


def test_mapping_matches_legacy_duplicates_and_missing(monkeypatch):
    old = [
        {"client": "  Компания   Дубль ", "manager": "Первый"},
        {"client": "КОМПАНИЯ ДУБЛЬ", "manager": " Второй "},
        {"client": "Компания Дубль", "manager": None},
        {"client": "Без менеджера", "manager": " "},
        {"client": "Straße", "manager": "Иванов"},
        {"client": "Петров клиент", "manager": "Петров"},
    ]
    expected = clients_vr._client_managers(old)
    calls = []

    def request(paths):
        calls.append(paths)
        return expected

    monkeypatch.setattr(clients_vr, "_request", request)
    result = asyncio.run(clients_vr.get_client_managers())
    assert result == expected
    assert result["компания дубль"] == "Второй"
    assert "без менеджера" not in result
    assert calls == [["/integration/client-managers"]]
    names = ["компания дубль", "strasse", "петров клиент", "без менеджера"]
    assert clients_for_managers(names, result, ["Иванов", "Петров"]) == names[1:3]
    assert clients_for_managers(names, result, ["Нет менеджера"]) == names[3:]
    assert clients_for_managers(names, result, ["Второй"]) == names[:1]


def test_bearer_and_timeout_and_no_full_request(monkeypatch):
    settings = SimpleNamespace(
        clients_vr_api_url="https://clients.test/api",
        clients_vr_api_token="test-secret",
    )
    monkeypatch.setitem(sys.modules, "app.config", SimpleNamespace(settings=settings))
    calls = []

    def open_response(request, timeout):
        calls.append(request.full_url)
        assert request.get_header("Authorization") == "Bearer test-secret"
        assert timeout == 15
        return io.BytesIO(json.dumps({" Клиент ": " Менеджер "}).encode())

    monkeypatch.setattr(clients_vr, "urlopen", open_response)
    assert asyncio.run(clients_vr.get_client_managers()) == {"клиент": "Менеджер"}
    assert calls == ["https://clients.test/api/integration/client-managers"]


def test_ttl_is_300_and_reloads_at_boundary(monkeypatch):
    now = [100.0]
    monkeypatch.setattr(clients_vr.time, "monotonic", lambda: now[0])
    calls = []
    monkeypatch.setattr(
        clients_vr,
        "_request",
        lambda paths: calls.append(paths) or {"клиент": "Менеджер"},
    )
    assert clients_vr.CACHE_TTL_SECONDS == 300
    first = asyncio.run(clients_vr.get_client_managers())
    now[0] = 399.999
    assert asyncio.run(clients_vr.get_client_managers()) is first
    now[0] = 400.0
    assert asyncio.run(clients_vr.get_client_managers()) == first
    assert len(calls) == 2


@pytest.mark.parametrize(
    "payload",
    [
        [],
        {"items": []},
        {"клиент": None},
        {"клиент": " "},
        {"": "Менеджер"},
        {"клиент": {"name": "Менеджер"}},
    ],
)
def test_malformed_response_is_not_cached(monkeypatch, payload):
    monkeypatch.setattr(clients_vr, "_request", lambda paths: payload)
    with pytest.raises(clients_vr.ClientsVrError, match="структуру"):
        asyncio.run(clients_vr.get_client_managers())
    assert "client-managers" not in clients_vr.cache


@pytest.mark.parametrize(
    "error",
    [
        TimeoutError("SECRET"),
        HTTPError("SECRET", 404, "SECRET", {}, None),
        HTTPError("SECRET", 401, "SECRET", {}, None),
        ValueError("SECRET"),
    ],
)
def test_failure_no_fallback_no_secret_or_stale_success(monkeypatch, error):
    clients_vr.cache["client-managers"] = (0, {"старый": "Менеджер"})
    monkeypatch.setattr(clients_vr.time, "monotonic", lambda: 301)
    calls = []

    def request(paths):
        calls.append(paths)
        raise error

    monkeypatch.setattr(clients_vr, "_request", request)
    with pytest.raises(clients_vr.ClientsVrError) as exc:
        asyncio.run(clients_vr.get_client_managers())
    assert "SECRET" not in str(exc.value)
    assert calls == [["/integration/client-managers"]]
    assert clients_vr.cache["client-managers"][1] == {"старый": "Менеджер"}


def test_metadata_does_not_overwrite_lightweight_cache(monkeypatch):
    def request(paths):
        if paths == ["/integration/client-managers"]:
            return {"клиент": "Новый"}
        return [{"client": "Клиент", "manager": "Старый", "buyer_type": "HoReCa"}]

    monkeypatch.setattr(clients_vr, "_request", request)
    asyncio.run(clients_vr.get_client_managers())
    assert asyncio.run(clients_vr.get_client_metadata()) == (
        {"клиент": "Старый"},
        {"клиент": "HoReCa"},
    )
    assert clients_vr.get_cached_client_managers() == {"клиент": "Новый"}


def test_empty_map_is_valid(monkeypatch):
    monkeypatch.setattr(clients_vr, "_request", lambda paths: {})
    assert asyncio.run(clients_vr.get_client_managers()) == {}
    assert "client-managers" in clients_vr.cache


def test_manager_report_propagates_unavailability(monkeypatch):
    from app.routers import product_reports

    async def conditions(data):
        return []

    async def unavailable():
        raise clients_vr.ClientsVrError("API менеджеров Clients VR недоступен")

    class Database:
        async def execute(self, query):
            return SimpleNamespace(all=lambda: [("Клиент", 100, 2, 1)])

    monkeypatch.setattr(product_reports, "_conditions", conditions)
    monkeypatch.setattr(product_reports, "get_client_managers", unavailable)
    data = product_reports.ReportRequest(
        date_from=date(2026, 9, 1), date_to=date(2026, 9, 30)
    )
    with pytest.raises(HTTPException) as exc:
        asyncio.run(product_reports.managers(data, Database()))
    assert exc.value.status_code == 502
    assert "недоступен" in exc.value.detail
