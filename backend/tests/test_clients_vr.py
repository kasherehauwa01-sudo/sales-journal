import asyncio

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
