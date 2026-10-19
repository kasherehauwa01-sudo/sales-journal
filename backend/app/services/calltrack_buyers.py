"""Paged buyers from real sales. No full Clients catalogue or sale/item ORM hydration."""
import re
from datetime import date

from sqlalchemy import String, any_, bindparam, cast, false, func, select
from sqlalchemy.dialects.postgresql import ARRAY

from app.models import Sale
from app.services.clients_vr import get_client_managers, normalize_client_name


def manager_key(value: str) -> str:
    return " ".join(sorted(str(value).lower().split()))


def buyers_query(*, search="", manager=None, managers=None, client_key=None, activity="all", today=None):
    identity = func.lower(func.trim(Sale.client))
    scope_name = func.lower(func.trim(func.regexp_replace(Sale.client, r"\s+", " ", "g")))
    q = select(identity.label("client_key"), func.min(Sale.client).label("name"),
               func.max(Sale.sale_date).label("last_purchase_at"), func.count(Sale.id).label("purchase_count"))
    q = q.where(Sale.client.is_not(None), func.length(func.trim(Sale.client)) > 0)
    if manager:
        allowed = [name for name, owner in (managers or {}).items() if manager_key(owner) == manager_key(manager)]
        q = q.where(scope_name == any_(cast(bindparam("buyer_scope_names", allowed), ARRAY(String))) if allowed else false())
    if client_key is not None:
        q = q.where(identity == client_key)
    q = q.group_by(identity)
    if search:
        # Search chooses whole buyers, never filters the rows used to compute their last purchase.
        digits = re.sub(r"\D", "", search) if re.fullmatch(r"[+\d\s().-]+", search) else ""
        if len(digits) >= 10:
            digits = digits[-10:]
        matching = select(identity).where(func.strpos(identity, search.lower()) > 0)
        if digits:
            phone = func.right(func.regexp_replace(func.coalesce(Sale.phone, ""), r"\D", "", "g"), 10)
            matching = select(identity).where((func.strpos(identity, search.lower()) > 0) | (func.strpos(phone, digits) > 0))
        q = q.having(identity.in_(matching))
    if activity != "all":
        from datetime import timedelta
        today = today or date.today()
        last = func.max(Sale.sale_date)
        if activity == "today":
            q = q.having(last == today)
        elif activity == "yesterday":
            q = q.having(last == today - timedelta(days=1))
        elif activity in {"7", "30"}:
            q = q.having(last.between(today - timedelta(days=int(activity) - 1), today))
        elif activity == "inactive":
            q = q.having(last <= today - timedelta(days=30))
    return q


async def load_buyers(db, payload):
    from datetime import datetime
    from zoneinfo import ZoneInfo
    # Complete lightweight name->manager map; errors must not become an unscoped result.
    managers = await get_client_managers()
    q = buyers_query(search=payload.search.strip(), manager=payload.manager, managers=managers,
                     client_key=payload.client_key, activity=payload.activity,
                     today=datetime.now(ZoneInfo("Europe/Volgograd")).date())
    total = int(await db.scalar(select(func.count()).select_from(q.subquery())) or 0)
    page = min(payload.page, max(1, (total + payload.page_size - 1) // payload.page_size))
    buyers = q.subquery()
    rows = (await db.execute(select(buyers).order_by(buyers.c.last_purchase_at.desc(), buyers.c.client_key)
                             .limit(payload.page_size).offset((page - 1) * payload.page_size))).mappings().all()
    keys = [row["client_key"] for row in rows]
    phones = {}
    unique_phones = {}
    if keys:
        identity = func.lower(func.trim(Sale.client))
        phone = func.right(func.regexp_replace(func.coalesce(Sale.phone, ""), r"\D", "", "g"), 10)
        owners = (select(phone.label("phone"), func.count(func.distinct(identity)).label("owners"))
                  .where(Sale.client.is_not(None), func.length(func.trim(Sale.client)) > 0)
                  .group_by(phone).subquery())
        # Stream distinct phone rows for this page, with a bounded response per buyer.
        result = await db.stream(select(identity, phone, owners.c.owners).select_from(Sale).join(owners, owners.c.phone == phone)
                                 .where(identity.in_(keys), func.length(phone) == 10).distinct())
        async for key, number, count in result:
            values = phones.setdefault(key, [])
            if len(values) >= 100:
                raise ValueError("Слишком много телефонов одного покупателя")
            values.append(number)
            if count == 1:
                unique_phones.setdefault(key, []).append(number)
    return {"items": [{**dict(row), "manager": managers.get(normalize_client_name(row["name"])),
                       "phones": sorted(phones.get(row["client_key"], [])),
                       "unique_phones": sorted(unique_phones.get(row["client_key"], []))} for row in rows],
            "total": total, "page": page, "page_size": payload.page_size}
