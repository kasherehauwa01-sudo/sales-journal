"""Bounded read-only client for ANALYTICS_LOOKUP_API.md schema_version=1."""

import asyncio
import json
import logging
from datetime import datetime
from typing import Literal
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StrictInt,
    StrictStr,
    ValidationError,
    model_validator,
    field_validator,
)
from app.config import settings
from app.services.product_analytics_runtime import run_cpu

BATCH_SIZE = 250
TIMEOUT_SECONDS = 15
MAX_ATTEMPTS = 3
MAX_RESPONSE_BYTES = 8 * 1024 * 1024
log = logging.getLogger(__name__)
_slot = asyncio.Semaphore(1)


class LookupError(RuntimeError):
    def __init__(self, code, retryable=False, retry_after=None):
        self.retry_after = retry_after
        self.code = code
        self.retryable = retryable
        super().__init__(code)  # Fixed code only; never include response/URL/payload.


class LookupInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    code: StrictStr | None = Field(None, max_length=128)
    article: StrictStr | None = Field(None, max_length=255)
    product_id: StrictInt | None = Field(None, ge=1, le=2147483647)

    @field_validator("code", "article", mode="before")
    @classmethod
    def trimmed(cls, value):
        return value.strip() or None if isinstance(value, str) else value

    @model_validator(mode="after")
    def has_identifier(self):
        if (
            self.product_id is None
            and not (self.code and self.code.strip())
            and not (self.article and self.article.strip())
        ):
            raise ValueError("identifier required")
        return self


class LookupProduct(BaseModel):
    model_config = ConfigDict(extra="forbid")
    product_id: StrictInt = Field(ge=1, le=2147483647)
    code: StrictStr = Field(max_length=128)
    article: StrictStr | None = Field(max_length=255)
    manufacturer: StrictStr | None
    brand: StrictStr | None
    category_id: StrictInt | None
    category: StrictStr | None
    subcategory: StrictStr | None
    legacy_category: StrictStr | None
    material: StrictStr | None
    horeca: StrictBool
    updated_at: datetime

    @model_validator(mode="after")
    def timestamp(self):
        if self.updated_at.tzinfo is None:
            raise ValueError("timezone required")
        return self


class LookupResult(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_index: StrictInt = Field(ge=0)
    status: Literal["matched", "not_found", "ambiguous"]
    matched_by: Literal["product_id", "code", "article"] | None
    product: LookupProduct | None

    @model_validator(mode="after")
    def consistent(self):
        if (self.status == "matched") != (
            self.product is not None and self.matched_by is not None
        ):
            raise ValueError("inconsistent result")
        if self.status != "matched" and (
            self.product is not None or self.matched_by is not None
        ):
            raise ValueError("unexpected product")
        return self


class LookupResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: StrictInt
    items: list[LookupResult]


def _request(items):
    if not settings.vrcatalog_api_token:
        raise LookupError("token_not_configured")
    request = Request(
        settings.vrcatalog_api_url.rstrip("/")
        + "/integration/products/analytics/lookup",
        data=json.dumps({"items": items}).encode(),
        method="POST",
        headers={
            "Authorization": "Bearer " + settings.vrcatalog_api_token,
            "Accept": "application/json",
            "Content-Type": "application/json",
        },
    )
    try:
        with urlopen(request, timeout=TIMEOUT_SECONDS) as response:
            raw = response.read(MAX_RESPONSE_BYTES + 1)
        if len(raw) > MAX_RESPONSE_BYTES:
            raise LookupError("response_too_large")
        return json.loads(raw)
    except HTTPError as exc:
        retry_after = None
        if exc.code == 429:
            try:
                retry_after = max(0, float(exc.headers.get("Retry-After", "0")))
            except (ValueError, AttributeError):
                pass
        # Long rate-limit waits are left to the operator, not held in this job.
        retryable = exc.code in {429, 500, 502, 503, 504} and (
            retry_after is None or retry_after <= 5
        )
        raise LookupError("http_" + str(exc.code), retryable, retry_after) from None
    except (URLError, TimeoutError, OSError):
        raise LookupError("transport_error", True) from None
    except (ValueError, UnicodeError):
        raise LookupError("invalid_response") from None


async def lookup_products(items):
    if not 1 <= len(items) <= BATCH_SIZE:
        raise LookupError("invalid_batch_size")
    try:
        validated = [
            LookupInput.model_validate(item).model_dump(exclude_none=True)
            for item in items
        ]
    except ValidationError:
        raise LookupError("invalid_identifiers") from None
    async with _slot:
        for attempt in range(MAX_ATTEMPTS):
            try:
                payload = await run_cpu(_request, validated)
                result = LookupResponse.model_validate(payload)
                if (
                    result.schema_version != 1
                    or len(result.items) != len(items)
                    or any(
                        item.request_index != index
                        for index, item in enumerate(result.items)
                    )
                ):
                    raise LookupError("invalid_response")
                for request, item in zip(validated, result.items):
                    if item.status == "matched":
                        if (
                            item.matched_by == "product_id"
                            and request.get("product_id") != item.product.product_id
                        ):
                            raise LookupError("invalid_response")
                        if "product_id" in request and item.matched_by != "product_id":
                            raise LookupError("invalid_response")
                        if item.matched_by not in request:
                            raise LookupError("invalid_response")
                return result.items
            except ValidationError:
                raise LookupError("invalid_response") from None
            except LookupError as exc:
                log.warning(
                    "catalog_lookup failure code=%s attempt=%s", exc.code, attempt + 1
                )
                if not exc.retryable or attempt + 1 == MAX_ATTEMPTS:
                    raise
                await asyncio.sleep(max(0.5 * (2**attempt), exc.retry_after or 0))
