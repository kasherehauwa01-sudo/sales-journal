import asyncio
import io
import sys
import types
from urllib.error import HTTPError

import pytest

from app.services import clients_vr


def setup_function():
    clients_vr.cache.clear()


def test_manager_filter_resolves_several_clients_and_keeps_other_filters():
    async def loader(manager: str):
        assert manager == "Пашута М.С."
        return ["Клиент 1", "Клиент 2"]

    filters = asyncio.run(
        clients_vr.resolve_manager_filter(
            {"manager": "Пашута М.С.", "price_type": "Розничная"}, loader
        )
    )

    assert filters == {
        "clients": ["Клиент 1", "Клиент 2"],
        "price_type": "Розничная",
    }


def test_manager_without_clients_produces_empty_client_filter():
    async def loader(_manager: str):
        return []

    filters = asyncio.run(
        clients_vr.resolve_manager_filter({"manager": "Без клиентов"}, loader)
    )

    assert filters == {"clients": []}


def test_clients_vr_error_is_not_converted_to_empty_list():
    async def loader(_manager: str):
        raise clients_vr.ClientsVrError("clients_vr недоступен")

    with pytest.raises(clients_vr.ClientsVrError, match="недоступен"):
        asyncio.run(clients_vr.resolve_manager_filter({"manager": "Менеджер"}, loader))


def test_managers_endpoint_service_still_returns_unique_sorted_names(monkeypatch):
    monkeypatch.setattr(
        clients_vr,
        "_request",
        lambda _paths: [
            {"manager": "Пашута М.С."},
            {"manager": "СОТРУДНИК АРБУЗ"},
            {"manager": "Родина"},
            {"manager": "Пашута М.С."},
        ],
    )

    assert asyncio.run(clients_vr.get_managers()) == [
        "Пашута М.С.",
        "Родина",
        "СОТРУДНИК АРБУЗ",
    ]


def test_client_manager_is_resolved_with_normalized_client_name(monkeypatch):
    monkeypatch.setattr(
        clients_vr,
        "_request",
        lambda _paths: [
            {"client": "  Компания Ромашка  ", "manager": "Пашута М.С."},
        ],
    )

    assert asyncio.run(clients_vr.get_client_manager("КОМПАНИЯ РОМАШКА")) == "Пашута М.С."


def test_buyer_types_are_unique_and_sorted(monkeypatch):
    monkeypatch.setattr(
        clients_vr,
        "_request",
        lambda _paths: [
            {"client": "ИП Альфа", "buyer_type": "Розница"},
            {"client": "ООО Бета", "buyer_type": "HoReCa"},
            {"client": "ИП Гамма", "buyer_type": "Розница"},
        ],
    )

    assert asyncio.run(clients_vr.get_buyer_types()) == ["HoReCa", "Розница"]


def test_buyer_type_returns_only_matching_clients_and_uses_cache(monkeypatch):
    calls = 0

    def request(_paths):
        nonlocal calls
        calls += 1
        return [
            {"client": " ИП Альфа ", "buyer_type": " Розница "},
            {"client": "ООО Бета", "buyer_type": "HoReCa"},
        ]

    monkeypatch.setattr(clients_vr, "_request", request)

    assert asyncio.run(clients_vr.get_buyer_type_clients("розница")) == ["ИП Альфа"]
    assert asyncio.run(clients_vr.get_buyer_type_clients("розница")) == ["ИП Альфа"]
    assert calls == 1


def test_public_fallback_is_used_after_unauthorized_when_token_is_not_configured(monkeypatch):
    calls = []
    settings = types.SimpleNamespace(
        clients_vr_api_url="https://clients.test/api", clients_vr_api_token=""
    )
    monkeypatch.setitem(sys.modules, "app.config", types.SimpleNamespace(settings=settings))

    class Response(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            self.close()

    def urlopen(request, timeout):
        calls.append(request.full_url)
        if request.full_url.endswith("/integration/managers"):
            raise HTTPError(request.full_url, 401, "Unauthorized", {}, None)
        return Response(b'[{"manager":"Manager"}]')

    monkeypatch.setattr(clients_vr, "urlopen", urlopen)

    assert clients_vr._request(["/integration/managers", "/managers"]) == [{"manager": "Manager"}]
    assert calls[-1].endswith("/managers")


def test_invalid_configured_token_does_not_bypass_integration_auth(monkeypatch):
    settings = types.SimpleNamespace(
        clients_vr_api_url="https://clients.test/api",
        clients_vr_api_token="wrong-token",
    )
    monkeypatch.setitem(sys.modules, "app.config", types.SimpleNamespace(settings=settings))

    def urlopen(request, timeout):
        raise HTTPError(request.full_url, 401, "Unauthorized", {}, None)

    monkeypatch.setattr(clients_vr, "urlopen", urlopen)

    with pytest.raises(HTTPError) as error:
        clients_vr._request(["/integration/managers", "/managers"])
    assert error.value.code == 401
