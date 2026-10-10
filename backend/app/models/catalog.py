"""Current CatalogVR attributes; sales rows are never rewritten."""

from datetime import datetime
from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column
from .base import Base


class CatalogProduct(Base):
    __tablename__ = "catalog_products"
    product_id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=False
    )
    code: Mapped[str] = mapped_column(String(128))
    article: Mapped[str | None] = mapped_column(String(255))
    manufacturer: Mapped[str | None] = mapped_column(Text)
    brand: Mapped[str | None] = mapped_column(Text)
    category: Mapped[str | None] = mapped_column(Text)
    subcategory: Mapped[str | None] = mapped_column(Text)
    category_id: Mapped[int | None] = mapped_column(Integer)
    legacy_category: Mapped[str | None] = mapped_column(Text)
    material: Mapped[str | None] = mapped_column(Text)
    image_url: Mapped[str | None] = mapped_column(Text)
    horeca: Mapped[bool] = mapped_column(Boolean)
    source_updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class CatalogLookup(Base):
    __tablename__ = "catalog_lookups"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending','matched','not_found','ambiguous','invalid')",
            name="ck_catalog_lookup_status",
        ),
        CheckConstraint(
            "status != 'matched' OR (product_id IS NOT NULL AND matched_by IS NOT NULL)",
            name="ck_catalog_lookup_matched",
        ),
    )
    # Hash of the normalized pair, NOT an article or a code uniqueness constraint.
    identity: Mapped[str] = mapped_column(String(64), primary_key=True)
    code: Mapped[str | None] = mapped_column(Text)
    article: Mapped[str | None] = mapped_column(Text)
    product_id: Mapped[int | None] = mapped_column(
        ForeignKey("catalog_products.product_id"), index=True
    )
    status: Mapped[str] = mapped_column(String(16), default="pending", index=True)
    matched_by: Mapped[str | None] = mapped_column(String(16))
    checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    synced_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), index=True
    )
    cycle: Mapped[int] = mapped_column(BigInteger, default=0)


class CatalogSyncState(Base):
    __tablename__ = "catalog_sync_state"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    revision: Mapped[int] = mapped_column(BigInteger, default=0)
    cycle: Mapped[int] = mapped_column(BigInteger, default=0)
    cursor: Mapped[int] = mapped_column(BigInteger, default=0)
    upper_id: Mapped[int] = mapped_column(BigInteger, default=0)
    completed: Mapped[bool] = mapped_column(Boolean, default=True)
    status: Mapped[str] = mapped_column(String(16), default="idle")
    processed: Mapped[int] = mapped_column(BigInteger, default=0)
    matched: Mapped[int] = mapped_column(BigInteger, default=0)
    not_found: Mapped[int] = mapped_column(BigInteger, default=0)
    ambiguous: Mapped[int] = mapped_column(BigInteger, default=0)
    errors: Mapped[int] = mapped_column(BigInteger, default=0)
    invalid: Mapped[int] = mapped_column(BigInteger, default=0)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_full_success_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    error_code: Mapped[str | None] = mapped_column(String(32))
