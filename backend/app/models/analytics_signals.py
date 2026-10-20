"""Shadow-only episodes, immutable transition history and resumable run projections."""
from datetime import date, datetime
from decimal import Decimal
from sqlalchemy import (BigInteger, Boolean, CheckConstraint, Date, DateTime, ForeignKey,
                        Index, Integer, JSON, Numeric, String, Text, UniqueConstraint, text)
from sqlalchemy.orm import Mapped, mapped_column
from .base import Base


class AnalyticsSignalRun(Base):
    __tablename__ = "analytics_signal_runs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    status: Mapped[str] = mapped_column(String(20), index=True)
    phase: Mapped[str] = mapped_column(String(20))
    as_of: Mapped[date] = mapped_column(Date)
    source_revision: Mapped[int] = mapped_column(BigInteger)
    upper_sale_id: Mapped[int] = mapped_column(BigInteger)
    sale_cursor: Mapped[int] = mapped_column(BigInteger, default=0)
    client_cursor: Mapped[str] = mapped_column(String(512), default="")
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    heartbeat_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    metadata_json: Mapped[dict] = mapped_column(JSON)
    counters: Mapped[dict] = mapped_column(JSON, default=dict)
    error_count: Mapped[int] = mapped_column(Integer, default=0)
    error_code: Mapped[str | None] = mapped_column(String(64))


class AnalyticsSignalState(Base):
    __tablename__ = "analytics_signal_state"
    __table_args__ = (CheckConstraint("id = 1", name="ck_analytics_state_singleton"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    active_run_id: Mapped[str | None] = mapped_column(ForeignKey("analytics_signal_runs.id"))
    published_run_id: Mapped[str | None] = mapped_column(ForeignKey("analytics_signal_runs.id"))


class AnalyticsPurchaseDay(Base):
    __tablename__ = "analytics_purchase_days"
    run_id: Mapped[str] = mapped_column(ForeignKey("analytics_signal_runs.id", ondelete="CASCADE"), primary_key=True)
    client_key: Mapped[str] = mapped_column(String(512), primary_key=True)
    sale_date: Mapped[date] = mapped_column(Date, primary_key=True)
    display_name: Mapped[str] = mapped_column(String(512))
    checks: Mapped[int] = mapped_column(BigInteger)
    positive_checks: Mapped[int] = mapped_column(BigInteger)
    revenue: Mapped[Decimal] = mapped_column(Numeric(30, 2))


class AnalyticsObservedPhone(Base):
    __tablename__ = "analytics_observed_phones"
    run_id: Mapped[str] = mapped_column(ForeignKey("analytics_signal_runs.id", ondelete="CASCADE"), primary_key=True)
    client_key: Mapped[str] = mapped_column(String(512), primary_key=True)
    phone: Mapped[str] = mapped_column(String(64), primary_key=True)
    last_seen: Mapped[date] = mapped_column(Date)


class AnalyticsClientEvaluation(Base):
    __tablename__ = "analytics_client_evaluations"
    __table_args__ = (Index("ix_analytics_evaluation_report", "run_id", "status", "score", "client_key"),)
    run_id: Mapped[str] = mapped_column(ForeignKey("analytics_signal_runs.id", ondelete="CASCADE"), primary_key=True)
    client_key: Mapped[str] = mapped_column(String(512), primary_key=True)
    display_name: Mapped[str] = mapped_column(String(512))
    eligible: Mapped[bool] = mapped_column(Boolean)
    reason: Mapped[str] = mapped_column(String(64))
    signal_type: Mapped[str | None] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(20))
    score: Mapped[Decimal] = mapped_column(Numeric(5, 2))
    confidence: Mapped[Decimal] = mapped_column(Numeric(5, 4))
    data: Mapped[dict] = mapped_column(JSON)


class AnalyticsSignal(Base):
    __tablename__ = "analytics_signals"
    __table_args__ = (
        CheckConstraint("identity_kind IN ('normalized_name','external_id')", name="ck_analytics_identity_kind"),
        CheckConstraint("status IN ('ACTIVE','SUSPENDED','RESOLVED')", name="ck_analytics_signal_status"),
        CheckConstraint("score BETWEEN 0 AND 100 AND confidence BETWEEN 0 AND 1", name="ck_analytics_signal_scores"),
        CheckConstraint("(status = 'RESOLVED') = (resolved_at IS NOT NULL)", name="ck_analytics_resolution"),
        CheckConstraint("mode = 'SHADOW'", name="ck_analytics_shadow_only"),
        UniqueConstraint("identity_kind", "normalization_version", "analytical_client_key", "family", "episode_no", name="uq_analytics_episode"),
        Index("uq_analytics_open_episode", "identity_kind", "normalization_version", "analytical_client_key", "family", unique=True,
              postgresql_where=text("status IN ('ACTIVE','SUSPENDED')"), sqlite_where=text("status IN ('ACTIVE','SUSPENDED')")),
    )
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    identity_kind: Mapped[str] = mapped_column(String(32), default="normalized_name")
    analytical_client_key: Mapped[str] = mapped_column(String(512))
    normalization_version: Mapped[int] = mapped_column(Integer)
    display_name: Mapped[str] = mapped_column(String(512))
    known_phones: Mapped[list] = mapped_column(JSON)
    family: Mapped[str] = mapped_column(String(32), default="INACTIVITY")
    signal_type: Mapped[str] = mapped_column(String(32))
    episode_no: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(20))
    mode: Mapped[str] = mapped_column(String(16), default="SHADOW")
    severity: Mapped[int] = mapped_column(Integer)
    score: Mapped[Decimal] = mapped_column(Numeric(5, 2))
    confidence: Mapped[Decimal] = mapped_column(Numeric(5, 4))
    metrics: Mapped[dict] = mapped_column(JSON)
    metrics_schema_version: Mapped[int] = mapped_column(Integer)
    generator_version: Mapped[str] = mapped_column(String(32))
    parameters_version: Mapped[int] = mapped_column(Integer)
    source_period_start: Mapped[date] = mapped_column(Date)
    source_period_end: Mapped[date] = mapped_column(Date)
    as_of: Mapped[date] = mapped_column(Date)
    source_coverage_status: Mapped[str] = mapped_column(String(20))
    source_freshness_status: Mapped[str] = mapped_column(String(20))
    anchor_purchase_date: Mapped[date] = mapped_column(Date)
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_evaluated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolution_reason: Mapped[str | None] = mapped_column(String(64))
    revision: Mapped[int] = mapped_column(BigInteger)
    run_id: Mapped[str] = mapped_column(ForeignKey("analytics_signal_runs.id"))


class AnalyticsSignalChange(Base):
    __tablename__ = "analytics_signal_changes"
    __table_args__ = (UniqueConstraint("signal_id", "signal_revision", name="uq_analytics_signal_revision"),)
    # Audit sequence ONLY. This is deliberately not an external feed cursor.
    id: Mapped[int] = mapped_column(BigInteger().with_variant(Integer, "sqlite"), primary_key=True)
    signal_id: Mapped[str] = mapped_column(ForeignKey("analytics_signals.id"), index=True)
    signal_revision: Mapped[int] = mapped_column(BigInteger)
    event_type: Mapped[str] = mapped_column(String(20))
    changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    run_id: Mapped[str] = mapped_column(ForeignKey("analytics_signal_runs.id"))
    payload: Mapped[dict] = mapped_column(JSON)
