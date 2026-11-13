"""Shared sales revision in the existing table; legacy columns retain schema compatibility.

Only source_revision is used by active services. No seasonal profiles are computed.
"""

from datetime import date, datetime
from sqlalchemy import BigInteger, Boolean, Date, DateTime, Integer, String
from sqlalchemy.orm import Mapped, mapped_column
from .base import Base


class SalesSourceState(Base):
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
