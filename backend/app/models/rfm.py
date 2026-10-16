"""Immutable RFM generations and one transactionally switched active pointer."""
from datetime import date, datetime
from decimal import Decimal
from sqlalchemy import JSON, CheckConstraint, Date, DateTime, ForeignKey, Integer, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column
from .base import Base


class RfmSnapshot(Base):
    __tablename__ = "rfm_snapshots"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    period_from: Mapped[date] = mapped_column(Date)
    period_to: Mapped[date] = mapped_column(Date)
    calculated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    client_count: Mapped[int] = mapped_column(Integer)
    duration_ms: Mapped[int] = mapped_column(Integer)
    algorithm_version: Mapped[int] = mapped_column(Integer)
    boundaries: Mapped[dict] = mapped_column(JSON)


class RfmSnapshotClient(Base):
    __tablename__ = "rfm_snapshot_clients"
    snapshot_id: Mapped[str] = mapped_column(ForeignKey("rfm_snapshots.id", ondelete="CASCADE"), primary_key=True)
    client_key: Mapped[str] = mapped_column(String(512), primary_key=True)
    client: Mapped[str] = mapped_column(String(512))
    r_score: Mapped[int] = mapped_column(Integer)
    f_score: Mapped[int] = mapped_column(Integer)
    m_score: Mapped[int] = mapped_column(Integer)
    rfm_code: Mapped[str] = mapped_column(String(3))
    segment: Mapped[str] = mapped_column(String(64))
    tags: Mapped[list] = mapped_column(JSON)
    recency_days: Mapped[int] = mapped_column(Integer)
    frequency: Mapped[int] = mapped_column(Integer)
    monetary: Mapped[Decimal] = mapped_column(Numeric(30, 2))
    average_check: Mapped[Decimal] = mapped_column(Numeric(30, 6))
    first_purchase: Mapped[date] = mapped_column(Date)
    last_purchase: Mapped[date] = mapped_column(Date)
    # Additional report inputs (cycle, rates, min/max check, etc.), without products.
    metrics: Mapped[dict] = mapped_column(JSON)


class RfmSnapshotState(Base):
    __tablename__ = "rfm_snapshot_state"
    __table_args__ = (CheckConstraint("id = 1", name="ck_rfm_singleton"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    active_snapshot_id: Mapped[str] = mapped_column(ForeignKey("rfm_snapshots.id", ondelete="RESTRICT"))
