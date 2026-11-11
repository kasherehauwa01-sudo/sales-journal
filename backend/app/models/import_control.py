"""Import metadata only: never stores source file bytes or changes sales."""

from datetime import date, datetime
from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Index,
    JSON,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column
from .base import Base, TimestampMixin


class ImportControlConfig(Base, TimestampMixin):
    __tablename__ = "import_control_config"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    enabled: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false"
    )
    automatic: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false"
    )
    email_enabled: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false"
    )
    start_date: Mapped[date | None] = mapped_column(Date)
    day_offset: Mapped[int | None] = mapped_column(Integer)
    marker_template: Mapped[str | None] = mapped_column(String(512))
    recipients: Mapped[str] = mapped_column(Text, default="", server_default="")
    revision: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    __table_args__ = (
        CheckConstraint("id=1"),
        CheckConstraint("day_offset IS NULL OR day_offset BETWEEN 0 AND 7"),
    )


class ImportDepartment(Base, TimestampMixin):
    __tablename__ = "import_departments"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    code: Mapped[str] = mapped_column(String(128), unique=True)
    name: Mapped[str] = mapped_column(String(255))


class ImportRule(Base, TimestampMixin):
    __tablename__ = "import_expectation_rules"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    department_id: Mapped[int] = mapped_column(
        ForeignKey("import_departments.id", ondelete="RESTRICT"), index=True
    )
    start_date: Mapped[date] = mapped_column(Date, index=True)
    end_date: Mapped[date | None] = mapped_column(Date)
    filename_template: Mapped[str] = mapped_column(String(512))
    date_format: Mapped[str] = mapped_column(
        String(8), default="dmy", server_default="dmy"
    )
    display_name: Mapped[str] = mapped_column(String(255))
    active: Mapped[bool] = mapped_column(Boolean, default=True, server_default="true")
    controlled: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default="true"
    )
    __table_args__ = (
        CheckConstraint("end_date IS NULL OR end_date>=start_date"),
        CheckConstraint("date_format IN ('dmy','iso')"),
    )


class ImportCycle(Base):
    __tablename__ = "import_control_cycles"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    sales_date: Mapped[date | None] = mapped_column(Date, index=True)
    status: Mapped[str] = mapped_column(String(24), index=True)
    source_confirmed: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false"
    )
    config_revision: Mapped[int | None] = mapped_column(Integer)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_code: Mapped[str | None] = mapped_column(String(64))


class ImportReceipt(Base):
    __tablename__ = "import_control_receipts"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    import_id: Mapped[int] = mapped_column(
        ForeignKey("import_batches.id", ondelete="RESTRICT"), unique=True
    )
    cycle_id: Mapped[str | None] = mapped_column(
        ForeignKey("import_control_cycles.id", ondelete="RESTRICT"), index=True
    )
    filename: Mapped[str] = mapped_column(String(512), index=True)
    sha256: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(32), index=True)
    period_start: Mapped[date | None] = mapped_column(Date, index=True)
    period_end: Mapped[date | None] = mapped_column(Date)
    period_method: Mapped[str | None] = mapped_column(String(64))
    departments: Mapped[list] = mapped_column(JSON, default=list)
    error_code: Mapped[str | None] = mapped_column(String(64))
    duplicate_of: Mapped[int | None] = mapped_column(
        ForeignKey("import_control_receipts.id", ondelete="RESTRICT")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ImportDayCheck(Base):
    __tablename__ = "import_day_checks"
    sales_date: Mapped[date] = mapped_column(Date, primary_key=True)
    status: Mapped[str] = mapped_column(String(32))
    expected: Mapped[int] = mapped_column(Integer)
    successful: Mapped[int] = mapped_column(Integer)
    missing: Mapped[int] = mapped_column(Integer)
    errors: Mapped[int] = mapped_column(Integer)
    checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    config_revision: Mapped[int] = mapped_column(Integer)
    error_code: Mapped[str | None] = mapped_column(String(64))


class ImportIssue(Base):
    __tablename__ = "import_control_issues"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    sales_date: Mapped[date] = mapped_column(Date, index=True)
    department_id: Mapped[int] = mapped_column(
        ForeignKey("import_departments.id", ondelete="RESTRICT"), index=True
    )
    rule_id: Mapped[int] = mapped_column(
        ForeignKey("import_expectation_rules.id", ondelete="RESTRICT")
    )
    expected_filename: Mapped[str] = mapped_column(String(512))
    department_name: Mapped[str] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(24), index=True)
    error_code: Mapped[str | None] = mapped_column(String(64))
    import_id: Mapped[int | None] = mapped_column(
        ForeignKey("import_batches.id", ondelete="RESTRICT")
    )
    detected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    closure_reason: Mapped[str | None] = mapped_column(Text)
    closure_method: Mapped[str | None] = mapped_column(String(32))
    __table_args__ = (
        UniqueConstraint(
            "sales_date", "department_id", name="uq_import_issue_day_department"
        ),
    )


class ImportControlAudit(Base):
    __tablename__ = "import_control_audit"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    entity: Mapped[str] = mapped_column(String(32), index=True)
    entity_id: Mapped[str] = mapped_column(String(64))
    actor: Mapped[str] = mapped_column(String(255))
    effective_date: Mapped[date | None] = mapped_column(Date)
    changes: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class ImportNotice(Base):
    __tablename__ = "import_control_notices"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    sales_date: Mapped[date] = mapped_column(Date, index=True)
    dedupe_key: Mapped[str] = mapped_column(String(64), unique=True)
    status: Mapped[str] = mapped_column(String(24), index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    recipients: Mapped[str] = mapped_column(Text)
    subject: Mapped[str] = mapped_column(String(255))
    body: Mapped[str] = mapped_column(Text)
    error_code: Mapped[str | None] = mapped_column(String(64))
    next_attempt: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ImportHistoryJob(Base):
    """One explicitly requested, bounded, resumable reconciliation."""

    __tablename__ = "import_history_job"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    status: Mapped[str] = mapped_column(String(24), server_default="paused")
    plan: Mapped[list] = mapped_column(JSON)
    rules_hash: Mapped[str] = mapped_column(String(64))
    position: Mapped[int] = mapped_column(Integer, server_default="0")
    next_date: Mapped[date | None] = mapped_column(Date)
    checked: Mapped[int] = mapped_column(Integer, server_default="0")
    total: Mapped[int] = mapped_column(Integer)
    error_code: Mapped[str | None] = mapped_column(String(64))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    __table_args__ = (
        CheckConstraint("id=1"),
        CheckConstraint("status IN ('running','paused','completed','error')"),
        CheckConstraint("position>=0 AND checked>=0 AND total>=checked"),
    )


class ImportHistoryResult(Base):
    __tablename__ = "import_history_results"
    sales_date: Mapped[date] = mapped_column(Date, primary_key=True)
    department_id: Mapped[int] = mapped_column(
        ForeignKey("import_departments.id", ondelete="RESTRICT"), primary_key=True
    )
    rule_id: Mapped[int | None] = mapped_column(
        ForeignKey("import_expectation_rules.id", ondelete="RESTRICT")
    )
    expected_filename: Mapped[str | None] = mapped_column(String(512), index=True)
    status: Mapped[str] = mapped_column(String(24), index=True)
    error_code: Mapped[str | None] = mapped_column(String(64))
    checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    __table_args__ = (
        CheckConstraint(
            "status IN ('success','missing','error','unavailable','waiting','not_expected')"
        ),
        Index(
            "ix_import_history_results_department_date", "department_id", "sales_date"
        ),
    )
