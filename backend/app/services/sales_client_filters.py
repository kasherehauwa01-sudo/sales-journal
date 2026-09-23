from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Sale
from app.services.clients_vr import get_buyer_type_clients


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
