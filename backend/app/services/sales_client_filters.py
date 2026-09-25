from app.services.clients_vr import get_buyer_type_clients, get_manager_clients


def _normalized(values: list[str]) -> dict[str, str]:
    return {value.strip().casefold(): value.strip() for value in values if value and value.strip()}


async def get_sales_buyer_type_clients(buyer_type: str) -> list[str]:
    """Получает уже отфильтрованный список из специализированного API Clients VR."""
    return await get_buyer_type_clients(buyer_type)


async def get_sales_filter_clients(manager: str | None, buyer_type: str | None) -> list[str] | None:
    manager_clients = await get_manager_clients(manager) if manager else None
    buyer_clients = await get_sales_buyer_type_clients(buyer_type) if buyer_type else None
    if manager_clients is None:
        return buyer_clients
    if buyer_clients is None:
        return manager_clients
    allowed = _normalized(buyer_clients)
    return [value for key, value in _normalized(manager_clients).items() if key in allowed]
