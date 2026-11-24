"""Read-only v1 evidence over source Sale headers, never analytical run tables."""

import hashlib
import json
from datetime import datetime, timezone
from decimal import Decimal

from fastapi import HTTPException
from sqlalchemy import select

from app.models import Sale
from app.services.analytics_scope import DEPARTMENT, SCOPE_POLICY
from app.services.client_identity import analytical_name_sql, normalize_client_name
from app.services.product_analytics_runtime import ensure_headroom

# Frozen v1 field set: a new Sale column must not silently change this contract.
FIELDS = (
    "id",
    "row_number",
    "sale_date",
    "document_number",
    "client",
    "department",
    "total_amount",
    "base_amount",
    "discount_percent",
    "reason",
    "author",
    "price_type",
    "discount_card_percent",
    "discount_card_number",
    "social",
    "certificate_amount",
    "promotion",
    "phone",
    "original_products_text",
    "fingerprint",
    "import_id",
    "created_at",
    "updated_at",
)
RETURN_PREFIXES = ("ВЗВ-", "РНВ-", "ВРМ-")
BATCH_SIZE = 100


def canonical_value(value):
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ValueError("non_finite_source_number")
        if value == 0:
            return "0"
        raw = format(value, "f")
        return raw.rstrip("0").rstrip(".") if "." in raw else raw
    if isinstance(value, datetime):
        if value.tzinfo is None:
            raise ValueError("source_timestamp_without_timezone")
        return (
            value.astimezone(timezone.utc)
            .isoformat(timespec="microseconds")
            .replace("+00:00", "Z")
        )
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value


def encode(value):
    return (
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        + "\n"
    ).encode("utf-8")


def validate_key(key):
    # Consumer sends an authoritative persisted v1 key, not an arbitrary display name.
    if not key or len(key) > 512 or normalize_client_name(key, version=1) != key:
        raise HTTPException(422, "Требуется нормализованный client_key версии 1")
    return key


def source_query(key):
    return (
        select(*(getattr(Sale, name) for name in FIELDS))
        .where(Sale.department == DEPARTMENT, analytical_name_sql(Sale.client) == key)
        .order_by(Sale.id)
    )


def row_value(row):
    result = {name: canonical_value(row[name]) for name in FIELDS}
    result["sale_id"] = result.pop("id")
    return result


async def scan(db, key, *, after=0, page_size=50):
    """One bounded streaming pass computes the FULL hash and retains one page only.
    The caller holds one RR read-only transaction for this entire HTTP response.
    """
    key = validate_key(key)
    digest = hashlib.sha256()
    digest.update(
        encode(
            dict(
                version=1,
                normalization_version=1,
                department=DEPARTMENT,
                client_key=key,
                canonicalization="sale-headers-jsonl/1",
            )
        )
    )
    count = 0
    first = None
    last = None
    positive = None
    items = []
    cursor_seen = after == 0
    stream = await db.stream(source_query(key).execution_options(yield_per=BATCH_SIZE))
    try:
        async for batch in stream.mappings().partitions(BATCH_SIZE):
            ensure_headroom()
            for row in batch:
                if row["id"] <= 0:
                    raise HTTPException(
                        503, "История содержит неподдерживаемый идентификатор продажи"
                    )
                value = row_value(row)
                digest.update(encode(value))
                count += 1
                day = row["sale_date"]
                first = min(first, day) if first else day
                last = max(last, day) if last else day
                if row["total_amount"] > 0 and not row["document_number"].startswith(
                    RETURN_PREFIXES
                ):
                    positive = max(positive, day) if positive else day
                if row["id"] == after:
                    cursor_seen = True
                if row["id"] > after and len(items) < page_size + 1:
                    items.append(value)
    finally:
        await stream.close()
    if not cursor_seen:
        raise HTTPException(
            409, "Курсор не принадлежит текущей истории; начните просмотр заново"
        )
    summary = dict(
        version=1,
        department=DEPARTMENT,
        normalization_version=1,
        client_key=key,
        sale_count=count,
        history_hash=digest.hexdigest(),
        first_purchase=first.isoformat() if first else None,
        last_purchase=last.isoformat() if last else None,
        last_positive_purchase=positive.isoformat() if positive else None,
        empty=count == 0,
        scope_policy=SCOPE_POLICY,
        mode="SHADOW",
        automation_allowed=False,
    )
    more = len(items) > page_size
    items = items[:page_size]
    return summary, items, (items[-1]["sale_id"] if more else None)
