from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories.sales import get_sale_with_items
from app.schemas import SaleOut
from app.services.clients_vr import ClientsVrError, get_client_manager


async def load_sale_detail(db: AsyncSession, sale_id: int) -> SaleOut | None:
    """Возвращает карточку, общую для UI и read-only интеграций."""
    sale = await get_sale_with_items(db, sale_id)
    if not sale:
        return None
    try:
        manager = await get_client_manager(sale.client)
    except ClientsVrError:
        manager = None
    return SaleOut.model_validate(sale).model_copy(update={"manager": manager})
