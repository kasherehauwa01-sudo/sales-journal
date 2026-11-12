"""Small configuration/exclusions/delivery metadata; never copies sales."""

from datetime import date, datetime
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Integer,
    String,
    Text,
    JSON,
    UniqueConstraint,
    Index,
    ForeignKey,
)
from sqlalchemy.orm import Mapped, mapped_column
from .base import Base


class SalesControlSettings(Base):
    __tablename__ = "sales_control_settings"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, server_default="false")
    send_time: Mapped[str] = mapped_column(String(5), server_default="09:00")
    recipients: Mapped[list] = mapped_column(JSON, server_default="[]")
    not_before: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revision: Mapped[int] = mapped_column(Integer, server_default="1")
    __table_args__ = (
        CheckConstraint("id=1", name="ck_sales_control_singleton"),
        CheckConstraint(
            "send_time ~ '^([01][0-9]|2[0-3]):[0-5][0-9]$'",
            name="ck_sales_control_time",
        ).ddl_if(dialect="postgresql"),
    )


class SalesControlExclusion(Base):
    __tablename__ = "sales_control_exclusions"
    department: Mapped[str] = mapped_column(String(512), primary_key=True)
    sales_date: Mapped[date] = mapped_column(Date, primary_key=True)
    hidden_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    actor: Mapped[str] = mapped_column(String(255))
    __table_args__ = (Index("ix_sales_control_exclusions_date", "sales_date"),)


class SalesControlAudit(Base):
    __tablename__ = "sales_control_audit"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    actor: Mapped[str] = mapped_column(String(255))
    action: Mapped[str] = mapped_column(String(32))
    details: Mapped[dict] = mapped_column(JSON)


class SalesControlDelivery(Base):
    __tablename__ = "sales_control_deliveries"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    settings_id: Mapped[int] = mapped_column(
        ForeignKey("sales_control_settings.id", ondelete="RESTRICT"), server_default="1"
    )
    sales_date: Mapped[date] = mapped_column(Date)
    recipients_hash: Mapped[str] = mapped_column(String(64))
    checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    missing_count: Mapped[int] = mapped_column(Integer, server_default="0")
    status: Mapped[str] = mapped_column(String(20))
    attempted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    next_attempt: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    attempts: Mapped[int] = mapped_column(Integer, server_default="0")
    error_code: Mapped[str | None] = mapped_column(String(48))
    manual_retry: Mapped[bool] = mapped_column(Boolean, server_default="false")
    __table_args__ = (
        UniqueConstraint(
            "sales_date", "recipients_hash", name="uq_sales_control_delivery"
        ),
        CheckConstraint(
            "status IN ('sending','sent','failed','uncertain','not_required')",
            name="ck_sales_control_delivery_status",
        ),
        CheckConstraint(
            "attempts>=0 AND missing_count>=0", name="ck_sales_control_counts"
        ),
        Index("ix_sales_control_delivery_checked", "checked_at"),
    )
