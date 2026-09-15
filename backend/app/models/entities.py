from datetime import date, datetime
from decimal import Decimal
from sqlalchemy import BigInteger, Boolean, Date, DateTime, ForeignKey, Index, Integer, Numeric, String, Text, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
from .base import Base, TimestampMixin

class ImportBatch(Base):
    __tablename__ = "import_batches"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    filename: Mapped[str] = mapped_column(String(512)); stored_path: Mapped[str | None] = mapped_column(String(1024))
    file_size: Mapped[int] = mapped_column(BigInteger); status: Mapped[str] = mapped_column(String(32), default="queued", index=True)
    uploaded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True)); finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    period_start: Mapped[date | None] = mapped_column(Date); period_end: Mapped[date | None] = mapped_column(Date)
    total_rows: Mapped[int] = mapped_column(Integer, default=0); processed_rows: Mapped[int] = mapped_column(Integer, default=0)
    added_rows: Mapped[int] = mapped_column(Integer, default=0); duplicate_rows: Mapped[int] = mapped_column(Integer, default=0)
    skipped_rows: Mapped[int] = mapped_column(Integer, default=0); error_rows: Mapped[int] = mapped_column(Integer, default=0)
    duration_ms: Mapped[int | None] = mapped_column(Integer); error_text: Mapped[str | None] = mapped_column(Text)
    log_text: Mapped[str | None] = mapped_column(Text)
    errors: Mapped[list["ImportError"]] = relationship(cascade="all, delete-orphan", lazy="selectin")

class Sale(Base, TimestampMixin):
    __tablename__ = "sales"; __table_args__ = (UniqueConstraint("fingerprint"),)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True); row_number: Mapped[int | None] = mapped_column(Integer)
    sale_date: Mapped[date] = mapped_column(Date, index=True); document_number: Mapped[str] = mapped_column(String(128), index=True)
    client: Mapped[str | None] = mapped_column(String(512), index=True); department: Mapped[str] = mapped_column(String(512), index=True)
    total_amount: Mapped[Decimal] = mapped_column(Numeric(18,2)); base_amount: Mapped[Decimal | None] = mapped_column(Numeric(18,2))
    discount_percent: Mapped[Decimal | None] = mapped_column(Numeric(8,4)); reason: Mapped[str | None] = mapped_column(Text)
    author: Mapped[str | None] = mapped_column(String(255), index=True); price_type: Mapped[str | None] = mapped_column(String(255), index=True)
    discount_card_percent: Mapped[Decimal | None] = mapped_column(Numeric(8,4)); discount_card_number: Mapped[str | None] = mapped_column(String(128), index=True)
    social: Mapped[bool] = mapped_column(Boolean, default=False, index=True); certificate_amount: Mapped[Decimal | None] = mapped_column(Numeric(18,2))
    promotion: Mapped[str | None] = mapped_column(String(512), index=True); phone: Mapped[str | None] = mapped_column(String(64), index=True)
    original_products_text: Mapped[str | None] = mapped_column(Text); fingerprint: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    import_id: Mapped[int] = mapped_column(ForeignKey("import_batches.id", ondelete="RESTRICT"), index=True)
    items: Mapped[list["SaleItem"]] = relationship(cascade="all, delete-orphan", lazy="selectin")

class SaleItem(Base):
    __tablename__ = "sale_items"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True); sale_id: Mapped[int] = mapped_column(ForeignKey("sales.id", ondelete="CASCADE"), index=True)
    article: Mapped[str | None] = mapped_column(String(255), index=True); code: Mapped[str | None] = mapped_column(String(255), index=True)
    name: Mapped[str] = mapped_column(String(1024), index=True); quantity: Mapped[Decimal] = mapped_column(Numeric(18,3), default=1)
    base_price: Mapped[Decimal | None] = mapped_column(Numeric(18,2)); actual_price: Mapped[Decimal | None] = mapped_column(Numeric(18,2))
    extra_data: Mapped[str | None] = mapped_column(Text); created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

class ImportError(Base):
    __tablename__ = "import_errors"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True); import_id: Mapped[int] = mapped_column(ForeignKey("import_batches.id", ondelete="CASCADE"), index=True)
    row_number: Mapped[int | None] = mapped_column(Integer); message: Mapped[str] = mapped_column(Text); raw_data: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
