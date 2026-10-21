"""Publication order is serialized in the SAME transaction as the run pointer."""
from sqlalchemy import BigInteger, ForeignKey, Integer
from sqlalchemy.orm import Mapped, mapped_column
from .base import Base


class AnalyticsDeliveryHead(Base):
    __tablename__ = "analytics_delivery_head"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    sequence: Mapped[int] = mapped_column(BigInteger, default=0)


class AnalyticsDeliveryPublication(Base):
    __tablename__ = "analytics_delivery_publications"
    sequence: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    run_id: Mapped[str] = mapped_column(ForeignKey("analytics_signal_runs.id"), unique=True)
