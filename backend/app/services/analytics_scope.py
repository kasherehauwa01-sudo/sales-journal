"""Versioned SHADOW scope; complete specialized ClientsVR lists only."""
from app.services.clients_vr import get_complete_buyer_type_names, ClientsVrError
from app.services.client_identity import normalize_client_name

DEPARTMENT = "Авиаторов"
BUYER_TYPES = ("Оптовик", "Корпоративный")
SCOPE_POLICY = "aviatorov-wholesale-corporate/2"


async def load_analytics_scope():
    groups = await get_complete_buyer_type_names(BUYER_TYPES)
    if not isinstance(groups, dict) or set(groups) != set(BUYER_TYPES):
        raise ClientsVrError("Incomplete analytical buyer-type bundle")
    kinds_by_key = {}
    for kind, names in groups.items():
        if not isinstance(names, (list, tuple)):
            raise ClientsVrError("Invalid analytical buyer-type names")
        for name in names:
            if not isinstance(name, str) or not name.strip():
                raise ClientsVrError("Invalid analytical client name")
            key = normalize_client_name(name, version=1)
            if key:
                kinds_by_key.setdefault(key, set()).add(kind)
    # Retail membership is irrelevant: exactly one target kind wins over retail;
    # both target kinds are ambiguous and excluded, irrespective of duplicates.
    keys = sorted(key for key, kinds in kinds_by_key.items() if len(kinds) == 1)
    return dict(department=DEPARTMENT, buyer_types=list(BUYER_TYPES), client_keys=keys,
                policy=SCOPE_POLICY, buyer_type_source="ClientsVR")
