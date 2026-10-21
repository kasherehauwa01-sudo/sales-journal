"""Business scope for SHADOW signals; buyer kinds come from existing ClientsVR."""
from app.services.clients_vr import get_client_buyer_types, ClientsVrError
from app.services.client_identity import normalize_client_name

DEPARTMENT = "Авиаторов"
BUYER_TYPES = ("Оптовик", "Корпоративный")


async def load_analytics_scope():
    types = await get_client_buyer_types()
    allowed = {v.casefold() for v in BUYER_TYPES}
    # Several legacy name keys can collapse into one v1 analytical key. An
    # allowed row must not silently override a retail/unknown row at that key.
    if not isinstance(types, dict):
        raise ClientsVrError("Invalid buyer-type catalogue")
    kinds_by_key = {}
    for name, kind in types.items():
        if not isinstance(name, str) or not isinstance(kind, str):
            raise ClientsVrError("Invalid buyer-type catalogue row")
        key = normalize_client_name(name, version=1)
        if key:
            kinds_by_key.setdefault(key, set()).add(kind.strip().casefold())
    keys = sorted(key for key, kinds in kinds_by_key.items() if kinds and kinds <= allowed)
    return dict(department=DEPARTMENT, buyer_types=list(BUYER_TYPES), client_keys=keys,
                policy="aviatorov-wholesale-corporate/1", buyer_type_source="ClientsVR")
