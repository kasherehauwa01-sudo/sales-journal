from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Sale
from app.services.clients_vr import get_buyer_type_clients, get_manager_clients


def _normalized(values: list[str]) -> dict[str, str]:
    return {
        value.strip().casefold(): value.strip()
        for value in values
        if value and value.strip()
    }


async def get_sales_buyer_type_clients(
    db: AsyncSession,
    buyer_type: str,
) -> list[str]:
    """
    Возвращает только тех клиентов выбранного вида покупателя,
    которые реально присутствуют в Sales Journal.

    Clients VR может содержать десятки тысяч клиентов, которых нет
    в журнале продаж. Они отбрасываются до формирования SQL IN (...).
    """
    buyer_clients = await get_buyer_type_clients(buyer_type)

    if not buyer_clients:
        return []

    allowed = {
        value.strip().casefold()
        for value in buyer_clients
        if value and value.strip()
    }

    if not allowed:
        return []

    sales_clients = (
        await db.scalars(
            select(func.lower(func.trim(Sale.client)))
            .where(
                Sale.client.is_not(None),
                func.length(func.trim(Sale.client)) > 0,
            )
            .distinct()
        )
    ).all()

    return [
        client
        for client in sales_clients
        if client and client.casefold() in allowed
    ]


def selected_values(value: str | list[str] | None) -> list[str]:
    """Accept legacy single values and repeated query parameters."""
    return list(dict.fromkeys(v.strip() for v in ([value] if isinstance(value, str) else value or []) if v and v.strip()))


async def get_sales_filter_clients(
    db: AsyncSession,
    manager: str | list[str] | None,
    buyer_type: str | list[str] | None,
) -> list[str] | None:
    """OR inside each selection, AND between manager and buyer selections."""
    groups = []
    for values, loader in (
        (selected_values(manager), get_manager_clients),
        (selected_values(buyer_type), lambda value: get_sales_buyer_type_clients(db, value)),
    ):
        if values:
            combined = {}
            for value in values:
                combined.update(_normalized(await loader(value)))
            groups.append(combined)
    if not groups:
        return None
    allowed = set(groups[0])
    for group in groups[1:]:
        allowed.intersection_update(group)
    return [name for key, name in groups[0].items() if key in allowed]
