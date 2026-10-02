from datetime import date, datetime
from decimal import Decimal

from pydantic import BaseModel, Field, model_validator

from app.schemas.common import ItemOut


CALLTRACK_BATCH_LIMIT = 500


class CalltrackClientRef(BaseModel):
    key: str = Field(min_length=1, max_length=255)
    phone: str | None = Field(default=None, max_length=64)
    name: str | None = Field(default=None, max_length=512)

    @model_validator(mode="after")
    def require_identifier(self):
        self.key = self.key.strip()
        self.phone = self.phone.strip() if self.phone and self.phone.strip() else None
        self.name = self.name.strip() if self.name and self.name.strip() else None
        if not self.key:
            raise ValueError("Поле key не может быть пустым")
        if not self.phone and not self.name:
            raise ValueError("Укажите phone или name")
        return self


class CalltrackSalesRequest(BaseModel):
    clients: list[CalltrackClientRef] = Field(min_length=1, max_length=CALLTRACK_BATCH_LIMIT)
    date_from: date
    date_to: date

    @model_validator(mode="after")
    def validate_request(self):
        if self.date_from > self.date_to:
            raise ValueError("date_from не может быть позже date_to")
        keys = [client.key for client in self.clients]
        if len(keys) != len(set(keys)):
            raise ValueError("Поле key должно быть уникальным внутри batch")
        return self


class CalltrackSaleSummary(BaseModel):
    sale_id: int
    client_key: str
    matched_by: str
    sale_date: date
    document_number: str
    client: str | None
    phone: str | None
    manager: str | None = None
    department: str
    total_amount: Decimal


class CalltrackSalesResponse(BaseModel):
    items: list[CalltrackSaleSummary]


class CalltrackSaleDetail(BaseModel):
    id: int
    row_number: int | None
    sale_date: date
    document_number: str
    client: str | None
    manager: str | None = None
    department: str
    total_amount: Decimal
    base_amount: Decimal | None
    discount_percent: Decimal | None
    reason: str | None
    author: str | None
    price_type: str | None
    discount_card_percent: Decimal | None
    discount_card_number: str | None
    social: bool
    certificate_amount: Decimal | None
    promotion: str | None
    phone: str | None
    original_products_text: str | None
    created_at: datetime
    items: list[ItemOut]
