from datetime import date, datetime
from decimal import Decimal
from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Integer,
    JSON,
    Numeric,
    String,
    Text,
    Index,
)
from sqlalchemy.orm import Mapped, mapped_column
from .base import Base


class SeasonalityMonth(Base):
    __tablename__ = "seasonality_months"
    generation: Mapped[int] = mapped_column(Integer, primary_key=True)
    identity: Mapped[str] = mapped_column(String(64), primary_key=True)
    year: Mapped[int] = mapped_column(Integer, primary_key=True)
    month: Mapped[int] = mapped_column(Integer, primary_key=True)
    incomplete_source: Mapped[bool] = mapped_column(Boolean, default=False)
    units: Mapped[Decimal] = mapped_column(Numeric(30, 3))
    positive_lines: Mapped[int] = mapped_column(BigInteger)
    first_sale: Mapped[date] = mapped_column(Date)
    last_sale: Mapped[date] = mapped_column(Date)


class SeasonalityStoreMonth(Base):
    __tablename__ = "seasonality_store_months"
    generation: Mapped[int] = mapped_column(Integer, primary_key=True)
    identity: Mapped[str] = mapped_column(String(64), primary_key=True)
    year: Mapped[int] = mapped_column(Integer, primary_key=True)
    month: Mapped[int] = mapped_column(Integer, primary_key=True)
    store: Mapped[str] = mapped_column(Text, primary_key=True)
    incomplete_source: Mapped[bool] = mapped_column(Boolean, default=False)
    units: Mapped[Decimal] = mapped_column(Numeric(30, 3))
    positive_lines: Mapped[int] = mapped_column(BigInteger)
    first_sale: Mapped[date] = mapped_column(Date)
    last_sale: Mapped[date] = mapped_column(Date)


class SeasonalityProfile(Base):
    __tablename__ = "seasonality_profiles"
    __table_args__ = (
        Index("ix_seasonality_pending", "generation", "dirty", "identity"),
    )
    generation: Mapped[int] = mapped_column(Integer, primary_key=True)
    identity: Mapped[str] = mapped_column(String(64), primary_key=True)
    product_key: Mapped[str] = mapped_column(Text)
    name_min: Mapped[str | None] = mapped_column(String(64))
    name_max: Mapped[str | None] = mapped_column(String(64))
    coverage: Mapped[int] = mapped_column(BigInteger, default=-1)
    dirty: Mapped[bool] = mapped_column(Boolean, default=True)
    data: Mapped[dict | None] = mapped_column(JSON)
    calculated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SeasonalityState(Base):
    __tablename__ = "seasonality_state"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    stop_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    failure_count: Mapped[int] = mapped_column(Integer, default=0)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    active_generation: Mapped[int] = mapped_column(Integer, default=0)
    generation: Mapped[int] = mapped_column(Integer, default=0)
    algorithm_version: Mapped[int] = mapped_column(Integer, default=0)
    first_year: Mapped[int | None] = mapped_column(Integer)
    phase: Mapped[str] = mapped_column(String(16), default="idle")
    cursor: Mapped[int] = mapped_column(BigInteger, default=0)
    source_cursor: Mapped[int] = mapped_column(BigInteger, default=0)
    upper_id: Mapped[int] = mapped_column(BigInteger, default=0)
    coverage: Mapped[int] = mapped_column(BigInteger, default=0)
    source_revision: Mapped[int] = mapped_column(BigInteger, default=0)
    job_revision: Mapped[int] = mapped_column(BigInteger, default=0)
    rebuild_required: Mapped[bool] = mapped_column(Boolean, default=True)
    calculated: Mapped[int] = mapped_column(BigInteger, default=0)
    last_checked: Mapped[date | None] = mapped_column(Date)
    last_success: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_code: Mapped[str | None] = mapped_column(String(32))
