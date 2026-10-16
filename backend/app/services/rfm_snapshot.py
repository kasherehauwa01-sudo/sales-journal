"""Build off the request path; immutable generations survive worker restarts."""
import logging
import time
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4
from zoneinfo import ZoneInfo

from sqlalchemy import and_, func, insert, literal, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.config import settings
from app.models import RfmSnapshot, RfmSnapshotClient, RfmSnapshotState
from app.services.rfm import SegmentSettings, enrich
from app.services.rfm_data import rfm_rows
from app.services.seasonality_worker import SOURCE_LOCK

log = logging.getLogger(__name__)
RFM_LOCK = 734190222
ALGORITHM_VERSION = 1


def current_date():
    return datetime.now(ZoneInfo(settings.autoload_timezone)).date()


def snapshot_period(end: date) -> tuple[date, date]:
    # JS Date.setMonth(currentMonth - 12): Feb 29 overflows to March 1.
    start = date(end.year - 1, end.month, 1) + timedelta(days=end.day - 1)
    return start, end


def metadata(snapshot):
    return {name: getattr(snapshot, name) for name in (
        "id", "period_from", "period_to", "calculated_at", "client_count",
        "duration_ms", "algorithm_version")}


async def active_snapshot(db):
    return await db.scalar(select(RfmSnapshot).join(
        RfmSnapshotState, RfmSnapshotState.active_snapshot_id == RfmSnapshot.id
    ).where(RfmSnapshotState.id == 1))


def client_values(row, snapshot_id):
    fields = {c.name for c in RfmSnapshotClient.__table__.columns} - {"snapshot_id", "metrics"}
    values = {name: row[name] for name in fields}
    values["monetary"] = Decimal(str(row.get("_monetary", row["monetary"])))
    values["average_check"] = Decimal(str(row.get("_average_check", row["average_check"])))
    # Product metrics intentionally absent rather than falsely claiming zero.
    excluded = fields | {"purchase_dates", "units", "unique_products"}
    values.update(snapshot_id=snapshot_id, metrics={
        k: v.isoformat() if isinstance(v, date) else v
        for k, v in row.items() if k not in excluded and not k.startswith("_")
    })
    return values


async def rebuild_snapshot(engine, *, as_of=None, coalesce=False):
    """Serialize workers and exclude source writes using the existing source lock.

    Waiting (rather than dropping a busy import trigger) guarantees that the last
    completed import is included. Overlapping requests can reuse the same build.
    No generation is visible until its rows and active pointer commit together.
    """
    started = time.monotonic()
    requested_at = datetime.now(timezone.utc)
    start, end = snapshot_period(as_of or current_date())
    version = str(uuid4())
    log.info("RFM rebuild start snapshot=%s period_from=%s period_to=%s", version, start, end)
    try:
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        async with sessions() as db:
            async with db.begin():
                # Both locks live on this transaction's connection. No pooled
                # connections are held just to own locks during calculation.
                if engine.dialect.name == "postgresql":
                    await db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": RFM_LOCK})
                    await db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": SOURCE_LOCK})
                previous = await active_snapshot(db)
                if (coalesce and previous and previous.period_from == start
                        and previous.period_to == end and previous.algorithm_version == ALGORITHM_VERSION
                        and previous.calculated_at >= requested_at):
                    log.info("RFM rebuild coalesced snapshot=%s", previous.id)
                    return metadata(previous)
                rows = await rfm_rows(db, start, end, None, include_products=False)
                rows, boundaries = enrich(rows, end, SegmentSettings())
                if len({r["client_key"] for r in rows}) != len(rows):
                    raise ValueError("Duplicate RFM client keys")
                snapshot = RfmSnapshot(
                    id=version, period_from=start, period_to=end,
                    calculated_at=datetime.now(timezone.utc), client_count=len(rows),
                    duration_ms=0, algorithm_version=ALGORITHM_VERSION, boundaries=boundaries)
                db.add(snapshot)
                await db.flush()
                for offset in range(0, len(rows), 1000):
                    await db.execute(insert(RfmSnapshotClient), [
                        client_values(r, version) for r in rows[offset:offset + 1000]])
                actual = await db.scalar(select(func.count()).select_from(RfmSnapshotClient).where(
                    RfmSnapshotClient.snapshot_id == version))
                if actual != len(rows):
                    raise ValueError("Incomplete RFM snapshot")
                state = await db.get(RfmSnapshotState, 1)
                if state:
                    state.active_snapshot_id = version
                else:
                    db.add(RfmSnapshotState(id=1, active_snapshot_id=version))
                snapshot.calculated_at = datetime.now(timezone.utc)
                snapshot.duration_ms = round((time.monotonic() - started) * 1000)
            result = metadata(snapshot)
        log.info("RFM rebuild success snapshot=%s period_from=%s period_to=%s clients=%s duration_ms=%s",
                 version, start, end, result["client_count"], result["duration_ms"])
        return result
    except BaseException:
        log.exception("RFM rebuild failure snapshot=%s period_from=%s period_to=%s duration_ms=%s",
                      version, start, end, round((time.monotonic() - started) * 1000))
        raise


async def rebuild_after_sales_update(engine):
    # Import status and sales have already committed. Failure must not turn a
    # successful import into a failed import or discard the last good snapshot.
    try:
        return await rebuild_snapshot(engine, coalesce=True)
    except Exception:
        log.exception("RFM refresh after committed sales update failed; previous snapshot retained")


async def lookup_client(db, name):
    started = time.monotonic()
    # Use the exact same PostgreSQL normalization as the population query.
    key = func.lower(func.trim(literal(name)))
    result = (await db.execute(select(RfmSnapshot, RfmSnapshotClient).join(
        RfmSnapshotState, RfmSnapshotState.active_snapshot_id == RfmSnapshot.id
    ).outerjoin(RfmSnapshotClient, and_(
        RfmSnapshotClient.snapshot_id == RfmSnapshot.id, RfmSnapshotClient.client_key == key
    )).where(RfmSnapshotState.id == 1))).first()
    log.info("RFM lookup status=%s duration_ms=%.3f", "unavailable" if result is None else
             "hit" if result[1] else "miss", (time.monotonic() - started) * 1000)
    return result if result else (None, None)
