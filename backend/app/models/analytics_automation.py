from datetime import datetime
from sqlalchemy import Boolean, Integer, BigInteger, String, JSON, DateTime, ForeignKey, Index
from sqlalchemy.orm import Mapped, mapped_column
from .base import Base

class AnalyticsAutomationPolicy(Base):
    __tablename__='analytics_automation_policy'
    id: Mapped[int]=mapped_column(Integer,primary_key=True)
    enabled: Mapped[bool]=mapped_column(Boolean,default=False)
    approved: Mapped[bool]=mapped_column(Boolean,default=False)
    revision: Mapped[int]=mapped_column(Integer,default=1)
    settings_json: Mapped[dict]=mapped_column(JSON,default=dict)
    sequence: Mapped[int]=mapped_column(BigInteger,default=0)

class AnalyticsAutomationIdentity(Base):
    __tablename__='analytics_automation_identities'
    subject_key: Mapped[str]=mapped_column(String(64),primary_key=True)
    client_id: Mapped[str]=mapped_column(String(255))
    manager_id: Mapped[int]=mapped_column(BigInteger)
    revision: Mapped[int]=mapped_column(Integer)
    status: Mapped[str]=mapped_column(String(20),default='REVIEW')
    evidence: Mapped[dict]=mapped_column(JSON,default=dict)

class AnalyticsAutomationRelease(Base):
    __tablename__='analytics_automation_releases'
    id: Mapped[str]=mapped_column(String(36),primary_key=True)
    sequence: Mapped[int]=mapped_column(BigInteger,unique=True)
    source_run_id: Mapped[str]=mapped_column(ForeignKey('analytics_signal_runs.id'),unique=True)
    metadata_json: Mapped[dict]=mapped_column(JSON)
    proof_hash: Mapped[str]=mapped_column(String(64))
    created_at: Mapped[datetime]=mapped_column(DateTime(timezone=True))

class AnalyticsAutomationEvent(Base):
    __tablename__='analytics_automation_events'
    __table_args__=(Index('ix_automation_release_event','release_id','id'),)
    id: Mapped[int]=mapped_column(BigInteger,primary_key=True,autoincrement=True)
    release_id: Mapped[str]=mapped_column(ForeignKey('analytics_automation_releases.id'))
    payload: Mapped[dict]=mapped_column(JSON)

class AnalyticsAutomationDecision(Base):
    __tablename__='analytics_automation_decisions'
    id: Mapped[str]=mapped_column(String(36),primary_key=True)
    source_run_id: Mapped[str]=mapped_column(String(36))
    created_at: Mapped[datetime]=mapped_column(DateTime(timezone=True))
    data: Mapped[dict]=mapped_column(JSON)
