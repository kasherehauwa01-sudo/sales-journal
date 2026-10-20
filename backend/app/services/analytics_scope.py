"""Business scope for SHADOW signals; buyer kinds come from existing ClientsVR."""
from app.services.clients_vr import get_client_buyer_types
from app.services.client_identity import normalize_client_name

DEPARTMENT = "Авиаторов"
BUYER_TYPES = ("Оптовик", "Корпоративный")


async def load_analytics_scope():
    types = await get_client_buyer_types()
    allowed = {v.casefold() for v in BUYER_TYPES}
    keys = sorted({normalize_client_name(name, version=1) for name, kind in types.items()
                   if kind.strip().casefold() in allowed and name.strip()})
    return dict(department=DEPARTMENT, buyer_types=list(BUYER_TYPES), client_keys=keys,
                policy="aviatorov-wholesale-corporate/1", buyer_type_source="ClientsVR")
