from sqlalchemy.ext.asyncio import AsyncSession

from app.repositories.sales import find_client_sales
from app.schemas.calltrack import CalltrackSalesRequest, CalltrackSaleSummary
from app.services.client_identity import normalize_client_name, normalize_phone
from app.services.clients_vr import ClientsVrError, get_client_managers


class AmbiguousClientReference(ValueError):
    pass


def _client_maps(payload: CalltrackSalesRequest) -> tuple[dict[str, str], dict[str, str]]:
    phones: dict[str, str] = {}
    names: dict[str, str] = {}
    for client in payload.clients:
        phone = normalize_phone(client.phone)
        name = normalize_client_name(client.name)
        for mapping, value, field in ((phones, phone, "phone"), (names, name, "name")):
            if not value:
                continue
            owner = mapping.get(value)
            if owner and owner != client.key:
                raise AmbiguousClientReference(
                    f"Одинаковый {field} указан для разных client key"
                )
            mapping[value] = client.key
    return phones, names


async def find_sales_for_calltrack(
    db: AsyncSession,
    payload: CalltrackSalesRequest,
) -> list[CalltrackSaleSummary]:
    """Сопоставляет batch одним SQL-запросом и дедуплицирует по Sale.id."""
    phone_keys, name_keys = _client_maps(payload)
    sales = await find_client_sales(
        db,
        phones=set(phone_keys),
        names=set(name_keys),
        date_from=payload.date_from,
        date_to=payload.date_to,
    )
    try:
        managers = await get_client_managers()
    except ClientsVrError:
        managers = {}

    result: list[CalltrackSaleSummary] = []
    seen: set[int] = set()
    for sale in sales:
        phone = normalize_phone(sale.phone)
        name = normalize_client_name(sale.client)
        if phone and phone in phone_keys:
            client_key, matched_by = phone_keys[phone], "phone"
        elif name and name in name_keys:
            client_key, matched_by = name_keys[name], "name"
        else:
            continue
        if sale.id in seen:
            continue
        seen.add(sale.id)
        result.append(CalltrackSaleSummary(
            sale_id=sale.id,
            client_key=client_key,
            matched_by=matched_by,
            sale_date=sale.sale_date,
            document_number=sale.document_number,
            client=sale.client,
            phone=sale.phone,
            manager=managers.get(name or ""),
            department=sale.department,
            total_amount=sale.total_amount,
        ))
    return result
