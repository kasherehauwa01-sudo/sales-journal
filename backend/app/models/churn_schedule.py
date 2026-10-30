"""Permanent control and durable delivery ledger; pilot records remain untouched."""
from datetime import datetime, date
from sqlalchemy import Boolean, Integer, BigInteger, String, JSON, DateTime, Date, ForeignKey, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column
from .base import Base


class ChurnSchedule(Base):
    __tablename__ = 'churn_schedule'
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    revision: Mapped[int] = mapped_column(Integer, default=0)
    approved: Mapped[bool] = mapped_column(Boolean, default=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    settings_json: Mapped[dict] = mapped_column(JSON, default=dict)


class ChurnJob(Base):
    __tablename__ = 'churn_jobs'
    __table_args__ = (UniqueConstraint('business_date', 'kind', name='uq_churn_job_day_kind'),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    business_date: Mapped[date] = mapped_column(Date)
    kind: Mapped[str] = mapped_column(String(16))
    policy_revision: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(32))
    run_id: Mapped[str | None] = mapped_column(String(36))
    release_id: Mapped[str | None] = mapped_column(ForeignKey('analytics_automation_releases.id'))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    counters: Mapped[dict] = mapped_column(JSON, default=dict)
    error_code: Mapped[str | None] = mapped_column(String(64))


class ChurnReceipt(Base):
    __tablename__ = 'churn_receipts'
    __table_args__ = (UniqueConstraint('job_id', 'event_id', name='uq_churn_receipt_event'),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    job_id: Mapped[str] = mapped_column(ForeignKey('churn_jobs.id'))
    event_id: Mapped[int] = mapped_column(BigInteger)
    event_hash: Mapped[str] = mapped_column(String(64))
    decision: Mapped[str] = mapped_column(String(32))
    created_for_release: Mapped[bool] = mapped_column(Boolean, default=False)
    task_id: Mapped[str | None] = mapped_column(String(64))
    reasons: Mapped[list] = mapped_column(JSON, default=list)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
