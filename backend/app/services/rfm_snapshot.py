"""Build off the request path; immutable generations survive worker restarts."""

import asyncio
import logging
import time
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from uuid import uuid4
from zoneinfo import ZoneInfo

from sqlalchemy import and_, func, insert, literal, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.config import settings
from app.models import (
    RfmSnapshot,
    RfmSnapshotClient,
    RfmSnapshotState,
    SalesSourceState,
    ImportBatch,
)
from app.services.rfm import SegmentSettings, enrich
from app.services.rfm_data import rfm_rows
from app.services.source_control import SOURCE_LOCK

log = logging.getLogger(__name__)
RFM_LOCK = 734190222
ALGORITHM_VERSION = 1
refresh_requested = asyncio.Event()


def current_date():
    return datetime.now(ZoneInfo(settings.autoload_timezone)).date()


def snapshot_period(end: date) -> tuple[date, date]:
    # JS Date.setMonth(currentMonth - 12): Feb 29 overflows to March 1.
    start = date(end.year - 1, end.month, 1) + timedelta(days=end.day - 1)
    return start, end


def metadata(snapshot):
    return {
        name: getattr(snapshot, name)
        for name in (
            "id",
            "period_from",
            "period_to",
            "calculated_at",
            "client_count",
            "duration_ms",
            "algorithm_version",
        )
    }


async def active_snapshot(db):
    return await db.scalar(
        select(RfmSnapshot)
        .join(RfmSnapshotState, RfmSnapshotState.active_snapshot_id == RfmSnapshot.id)
        .where(RfmSnapshotState.id == 1)
    )


def client_values(row, snapshot_id):
    fields = {c.name for c in RfmSnapshotClient.__table__.columns} - {
        "snapshot_id",
        "metrics",
    }
    values = {name: row[name] for name in fields}
    values["monetary"] = Decimal(str(row.get("_monetary", row["monetary"])))
    values["average_check"] = Decimal(
        str(row.get("_average_check", row["average_check"]))
    )
    # Product metrics intentionally absent rather than falsely claiming zero.
    excluded = fields | {"purchase_dates", "units", "unique_products"}
    values.update(
        snapshot_id=snapshot_id,
        metrics={
            k: v.isoformat() if isinstance(v, date) else v
            for k, v in row.items()
            if k not in excluded and not k.startswith("_")
        },
    )
    return values


async def rebuild_snapshot(engine, *, as_of=None, coalesce=False, background=False):
    """Serialize workers and exclude source writes using the existing source lock.

    Waiting (rather than dropping a busy import trigger) guarantees that the last
    completed import is included. Overlapping requests can reuse the same build.
    No generation is visible until its rows and active pointer commit together.
    """
    started = time.monotonic()
    requested_at = datetime.now(timezone.utc)
    start, end = snapshot_period(as_of or current_date())
    version = str(uuid4())
    log.info(
        "RFM rebuild start snapshot=%s period_from=%s period_to=%s", version, start, end
    )
    try:
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        async with sessions() as db:
            async with db.begin():
                # Both locks live on this transaction's connection. No pooled
                # connections are held just to own locks during calculation.
                if engine.dialect.name == "postgresql":
                    if background:
                        await db.execute(text("SET LOCAL lock_timeout='1s'"))
                        await db.execute(text("SET LOCAL statement_timeout='300s'"))
                        for key in (RFM_LOCK, SOURCE_LOCK):
                            if not await db.scalar(
                                text("SELECT pg_try_advisory_xact_lock(:key)"),
                                {"key": key},
                            ):
                                return {"status": "busy"}
                    else:
                        await db.execute(
                            text("SELECT pg_advisory_xact_lock(:key)"),
                            {"key": RFM_LOCK},
                        )
                        await db.execute(
                            text("SELECT pg_advisory_xact_lock(:key)"),
                            {"key": SOURCE_LOCK},
                        )
                previous = await active_snapshot(db)
                signature = await source_signature(db)
                if background and not refresh_due(previous, signature, end):
                    return {
                        "status": (
                            "current"
                            if snapshot_current(previous, signature, end)
                            else "pending"
                        )
                    }
                from app.services.product_analytics_runtime import ensure_headroom

                ensure_headroom()
                if (
                    coalesce
                    and previous
                    and previous.period_from == start
                    and previous.period_to == end
                    and previous.algorithm_version == ALGORITHM_VERSION
                    and previous.calculated_at >= requested_at
                    and previous.boundaries.get("_refresh_source") == signature
                ):
                    log.info("RFM rebuild coalesced snapshot=%s", previous.id)
                    return metadata(previous)
                rows = await rfm_rows(db, start, end, None, include_products=False)
                ensure_headroom()
                from app.services.async_resources import run_blocking

                rows, boundaries = await run_blocking(
                    enrich, rows, end, SegmentSettings()
                )
                # Internal provenance only; existing R/F/M boundary keys and all
                # client metrics/periods remain untouched. Old snapshots stay readable.
                boundaries = dict(boundaries, _refresh_source=signature)
                if len({r["client_key"] for r in rows}) != len(rows):
                    raise ValueError("Duplicate RFM client keys")
                snapshot = RfmSnapshot(
                    id=version,
                    period_from=start,
                    period_to=end,
                    calculated_at=datetime.now(timezone.utc),
                    client_count=len(rows),
                    duration_ms=0,
                    algorithm_version=ALGORITHM_VERSION,
                    boundaries=boundaries,
                )
                db.add(snapshot)
                await db.flush()
                for offset in range(0, len(rows), 1000):
                    ensure_headroom()
                    await db.execute(
                        insert(RfmSnapshotClient),
                        [
                            client_values(r, version)
                            for r in rows[offset : offset + 1000]
                        ],
                    )
                actual = await db.scalar(
                    select(func.count())
                    .select_from(RfmSnapshotClient)
                    .where(RfmSnapshotClient.snapshot_id == version)
                )
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
        log.info(
            "RFM rebuild success snapshot=%s period_from=%s period_to=%s clients=%s duration_ms=%s",
            version,
            start,
            end,
            result["client_count"],
            result["duration_ms"],
        )
        return result
    except BaseException as error:
        log.warning(
            "RFM rebuild failure snapshot=%s reason=%s duration_ms=%s",
            version,
            (
                "cancelled"
                if isinstance(error, asyncio.CancelledError)
                else type(error).__name__
            ),
            round((time.monotonic() - started) * 1000),
        )
        raise


async def source_signature(db):
    # Small existing metadata, NEVER a scan of sales/sale_items. added_rows is
    # committed with each inserted sale, including a crash before import finish.
    revision = await db.scalar(
        select(SalesSourceState.source_revision).where(SalesSourceState.id == 1)
    )
    total, upper = (
        await db.execute(
            select(
                func.coalesce(func.sum(ImportBatch.added_rows), 0),
                func.coalesce(func.max(ImportBatch.id), 0),
            )
        )
    ).one()
    return {
        "revision": int(revision or 0),
        "added_rows": int(total),
        "upper_batch": int(upper),
    }


def snapshot_current(previous, signature, end):
    return bool(
        previous
        and previous.period_to == end
        and previous.period_from == snapshot_period(end)[0]
        and previous.algorithm_version == ALGORITHM_VERSION
        and previous.boundaries.get("_refresh_source") == signature
    )


def refresh_due(previous, signature, end, *, now=None):
    if snapshot_current(previous, signature, end):
        return False
    if previous is None:
        return True
    calculated = previous.calculated_at
    if calculated.tzinfo is None:
        calculated = calculated.replace(tzinfo=timezone.utc)
    return (
        (now or datetime.now(timezone.utc)) - calculated
    ).total_seconds() >= settings.rfm_refresh_min_seconds


async def rebuild_after_sales_update(engine):
    # Existing import/deletion transactions already persist the source change.
    # A wake-up is only an optimisation; restart/multiple workers use DB provenance.
    refresh_requested.set()


async def refresh_tick(engine):
    from app.services.product_analytics_runtime import heavy_operation

    sessions = async_sessionmaker(engine, expire_on_commit=False)
    async with sessions() as db:
        if engine.dialect.name == "postgresql":
            await db.execute(text("SET LOCAL statement_timeout='5s'"))
            await db.execute(text("SET LOCAL lock_timeout='1s'"))
        previous = await active_snapshot(db)
        signature = await source_signature(db)
        due = refresh_due(previous, signature, current_date())
    if not due:
        return {
            "status": (
                "current"
                if snapshot_current(previous, signature, current_date())
                else "pending"
            )
        }
    # Preserve the existing global process memory guard; locks inside the builder
    # are nonblocking so an active import cannot hold this lane/DB transaction open.
    async with heavy_operation():
        async with asyncio.timeout(settings.rfm_refresh_timeout_seconds):
            result = await rebuild_snapshot(engine, coalesce=True, background=True)
            return (
                {"status": "current", "snapshot": result} if "id" in result else result
            )


async def refresh_status(db):
    previous = await active_snapshot(db)
    signature = await source_signature(db)
    rebuilding = False
    if db.bind.dialect.name == "postgresql":
        rebuilding = bool(
            await db.scalar(
                text(
                    "SELECT EXISTS(SELECT 1 FROM pg_locks WHERE locktype='advisory' AND database=(SELECT oid FROM pg_database WHERE datname=current_database()) AND classid=0 AND objsubid=1 AND objid=:key AND mode='ExclusiveLock' AND granted)"
                ),
                {"key": RFM_LOCK},
            )
        )
    return {
        "state": (
            "rebuilding"
            if rebuilding
            else (
                "current"
                if snapshot_current(previous, signature, current_date())
                else "pending"
            )
        ),
        "calculated_at": previous.calculated_at if previous else None,
        "duration_ms": previous.duration_ms if previous else None,
    }


async def lookup_client(db, name):
    started = time.monotonic()
    # Use the exact same PostgreSQL normalization as the population query.
    key = func.lower(func.trim(literal(name)))
    result = (
        await db.execute(
            select(RfmSnapshot, RfmSnapshotClient)
            .join(
                RfmSnapshotState, RfmSnapshotState.active_snapshot_id == RfmSnapshot.id
            )
            .outerjoin(
                RfmSnapshotClient,
                and_(
                    RfmSnapshotClient.snapshot_id == RfmSnapshot.id,
                    RfmSnapshotClient.client_key == key,
                ),
            )
            .where(RfmSnapshotState.id == 1)
        )
    ).first()
    log.info(
        "RFM lookup status=%s duration_ms=%.3f",
        "unavailable" if result is None else "hit" if result[1] else "miss",
        (time.monotonic() - started) * 1000,
    )
    return result if result else (None, None)
