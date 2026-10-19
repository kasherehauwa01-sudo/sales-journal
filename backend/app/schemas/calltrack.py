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


class CalltrackInsightsRequest(BaseModel):
    name: str = Field(min_length=1, max_length=512)

    @model_validator(mode="after")
    def validate_name(self):
        self.name = self.name.strip()
        if not self.name:
            raise ValueError("Укажите name клиента")
        return self


class RfmSnapshotMetadata(BaseModel):
    id: str
    calculated_at: datetime
    period_from: date
    period_to: date
    age_seconds: int
    stale: bool


class CalltrackRfmInsight(BaseModel):
    segment: str
    rfm_code: str
    tags: list[str]
    r_score: int
    f_score: int
    m_score: int
    recency_days: int
    frequency: int
    monetary: Decimal
    average_check: Decimal
    first_purchase: date
    last_purchase: date
    purchases_per_month: float
    average_interval: float | None
    cycle_overdue: float | None


class CalltrackCategoryInsight(BaseModel):
    category: str
    quantity: Decimal
    revenue: Decimal


class CalltrackSaleQuantity(BaseModel):
    sale_id: int
    quantity: Decimal


class CalltrackInsightsResponse(BaseModel):
    status: str  # ready | client_not_in_snapshot
    client_key: str
    rfm: CalltrackRfmInsight | None
    snapshot: RfmSnapshotMetadata
    unique_products: int
    top_categories: list[CalltrackCategoryInsight]
    sale_quantities: list[CalltrackSaleQuantity]


class CalltrackBuyersRequest(BaseModel):
    search: str = Field(default="", max_length=200)
    manager: str | None = Field(default=None, max_length=255)
    client_key: str | None = Field(default=None, min_length=1, max_length=512)
    activity: str = Field(default="all", pattern="^(all|today|yesterday|7|30|inactive)$")
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=50, ge=1, le=100)


class CalltrackBuyer(BaseModel):
    client_key: str
    name: str
    phones: list[str]
    unique_phones: list[str]
    manager: str | None
    last_purchase_at: date
    purchase_count: int


class CalltrackBuyersResponse(BaseModel):
    items: list[CalltrackBuyer]
    total: int
    page: int
    page_size: int


class CalltrackCategoryDetailsRequest(BaseModel):
    names: list[str] = Field(min_length=1, max_length=500)
    category: str = Field(min_length=1, max_length=512)
    subcategory: str | None = Field(default=None, min_length=1, max_length=512)

    @model_validator(mode="after")
    def valid_names(self):
        if any(not name.strip() or len(name) > 512 for name in self.names):
            raise ValueError("Некорректные имена клиентов")
        return self


class CalltrackCategoryDetailItem(BaseModel):
    key: str
    name: str
    revenue: Decimal
    quantity: Decimal
    code: str | None = None
    article: str | None = None
    product_id: int | None = None


class CalltrackCategoryDetailsResponse(BaseModel):
    level: str
    category: str
    subcategory: str | None
    items: list[CalltrackCategoryDetailItem]
    total_count: int
    parent_revenue: Decimal
    parent_quantity: Decimal


class CalltrackDashboardSalesRequest(BaseModel):
    year: int = Field(ge=2000, le=2100)
    managers: list[str] = Field(max_length=500)
