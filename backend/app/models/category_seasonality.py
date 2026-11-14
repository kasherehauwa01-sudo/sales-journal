"""Versioned monthly facts, coverage and frozen filter memberships (not SKU profiles)."""

from datetime import date, datetime
from decimal import Decimal
from sqlalchemy import (
    Integer,
    BigInteger,
    String,
    Text,
    Date,
    DateTime,
    Numeric,
    JSON,
    ForeignKey,
    ForeignKeyConstraint,
    CheckConstraint,
    Index,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column
from .base import Base


class CategorySeasonSnapshot(Base):
    __tablename__ = "category_season_snapshots"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    status: Mapped[str] = mapped_column(String(16))
    week: Mapped[date | None] = mapped_column(Date, unique=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    through: Mapped[date] = mapped_column(Date)
    options: Mapped[dict] = mapped_column(JSON, default=dict)
    fact_rows: Mapped[int] = mapped_column(BigInteger, default=0)
    duration_ms: Mapped[int | None] = mapped_column(BigInteger)
    error_code: Mapped[str | None] = mapped_column(String(64))
    __table_args__ = (
        CheckConstraint(
            "status IN ('building','published','failed')", name="ck_cs_snapshot_status"
        ),
    )


class CategorySeasonState(Base):
    __tablename__ = "category_season_state"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    active_id: Mapped[str | None] = mapped_column(
        ForeignKey("category_season_snapshots.id", ondelete="RESTRICT")
    )
    next_due: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (CheckConstraint("id=1", name="ck_cs_state_singleton"),)


class CategorySeasonCategory(Base):
    __tablename__ = "category_season_categories"
    snapshot_id: Mapped[str] = mapped_column(
        ForeignKey("category_season_snapshots.id", ondelete="CASCADE"), primary_key=True
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    key: Mapped[str] = mapped_column(Text)
    parent_key: Mapped[str] = mapped_column(Text)
    parent: Mapped[str] = mapped_column(Text)
    name: Mapped[str] = mapped_column(Text)


class CategorySeasonClient(Base):
    __tablename__ = "category_season_clients"
    snapshot_id: Mapped[str] = mapped_column(
        ForeignKey("category_season_snapshots.id", ondelete="CASCADE"), primary_key=True
    )
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(512))
    __table_args__ = (UniqueConstraint("snapshot_id", "name"),)


class CategorySeasonMembership(Base):
    __tablename__ = "category_season_memberships"
    snapshot_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    client_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    kind: Mapped[str] = mapped_column(String(16), primary_key=True)
    value: Mapped[str] = mapped_column(String(255), primary_key=True)
    __table_args__ = (
        ForeignKeyConstraint(
            ["snapshot_id", "client_id"],
            ["category_season_clients.snapshot_id", "category_season_clients.id"],
            ondelete="CASCADE",
        ),
        CheckConstraint(
            "kind IN ('manager','buyer_type')", name="ck_cs_membership_kind"
        ),
        Index(
            "ix_category_season_membership_filter",
            "snapshot_id",
            "kind",
            "value",
            "client_id",
        ),
    )


class CategorySeasonFact(Base):
    __tablename__ = "category_season_facts"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    snapshot_id: Mapped[str] = mapped_column(String(36))
    category_id: Mapped[int] = mapped_column(Integer)
    client_id: Mapped[int] = mapped_column(Integer)
    store: Mapped[str] = mapped_column(String(512))
    brand: Mapped[str] = mapped_column(Text)
    manufacturer: Mapped[str] = mapped_column(Text)
    year: Mapped[int] = mapped_column(Integer)
    month: Mapped[int] = mapped_column(Integer)
    units: Mapped[Decimal] = mapped_column(Numeric())
    revenue: Mapped[Decimal] = mapped_column(Numeric())
    lines: Mapped[int] = mapped_column(BigInteger)
    __table_args__ = (
        ForeignKeyConstraint(
            ["snapshot_id", "category_id"],
            ["category_season_categories.snapshot_id", "category_season_categories.id"],
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["snapshot_id", "client_id"],
            ["category_season_clients.snapshot_id", "category_season_clients.id"],
            ondelete="CASCADE",
        ),
        CheckConstraint(
            "month BETWEEN 1 AND 12 AND lines>0", name="ck_cs_fact_month_lines"
        ),
        Index("ix_category_season_fact_scope", "snapshot_id", "store", "year", "month"),
        Index("ix_category_season_fact_category", "snapshot_id", "category_id"),
    )


class CategorySeasonCoverage(Base):
    __tablename__ = "category_season_coverage"
    snapshot_id: Mapped[str] = mapped_column(
        ForeignKey("category_season_snapshots.id", ondelete="CASCADE"), primary_key=True
    )
    store: Mapped[str] = mapped_column(String(512), primary_key=True)
    year: Mapped[int] = mapped_column(Integer, primary_key=True)
    month: Mapped[int] = mapped_column(Integer, primary_key=True)
    method: Mapped[str] = mapped_column(String(24))
    recorded_days: Mapped[int] = mapped_column(Integer)
    calendar_days: Mapped[int] = mapped_column(Integer)
    __table_args__ = (
        CheckConstraint(
            "method IN ('confirmed','estimated','excluded')",
            name="ck_cs_coverage_method",
        ),
        CheckConstraint(
            "month BETWEEN 1 AND 12 AND recorded_days BETWEEN 0 AND 31",
            name="ck_cs_coverage_days",
        ),
    )
