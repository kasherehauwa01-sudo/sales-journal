"""Authenticated, bounded immutable run export. SHADOW never authorizes tasks.

The audit sequence is a cursor ONLY within one SUCCEEDED frozen run. Poll latest
again after completing pagination; consumers compare per-signal revisions.
"""
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, func, text
from sqlalchemy.ext.asyncio import AsyncSession
from app.database import get_db
from app.models import AnalyticsSignalRun, AnalyticsSignalState, AnalyticsSignalChange, AnalyticsDeliveryPublication
from app.routers.calltrack_integration import require_calltrack_token
from app.services.inactivity import json_value
from app.services.churn_pilot import ALGORITHM_POLICY
from app.services.analytics_release_guard import automation_available

router = APIRouter(prefix="/integrations/calltrack/analytics", tags=["Analytics delivery"],
                   dependencies=[Depends(require_calltrack_token)])


def public_run(run):
    metadata = run.metadata_json
    scope = {k: v for k, v in metadata.get("scope", {}).items() if k != "client_keys"}
    decision = {k: v for k, v in metadata.get("source_decision", {}).items() if k != "evidence"}
    return json_value(dict(id=run.id, status=run.status, as_of=run.as_of,
        source_revision=run.source_revision, finished_at=run.finished_at,
        scope=scope, source_decision=decision, mode="SHADOW", automation_allowed=False))


@router.get("/latest")
async def latest(db: AsyncSession = Depends(get_db)):
    state = await db.get(AnalyticsSignalState, 1)
    run = await db.get(AnalyticsSignalRun, state.published_run_id) if state and state.published_run_id else None
    if not run or run.status != "SUCCEEDED":
        return dict(run=None, mode="SHADOW", automation_allowed=False)
    return dict(run=public_run(run), mode="SHADOW", automation_allowed=False)


@router.get("/runs/{run_id}/changes")
async def changes(run_id: str, after: int = Query(0, ge=0),
                  page_size: int = Query(50, ge=1, le=100), db: AsyncSession = Depends(get_db)):
    run = await db.get(AnalyticsSignalRun, run_id)
    if not run or run.status != "SUCCEEDED":
        raise HTTPException(404, "Completed run not found")
    rows = list((await db.scalars(select(AnalyticsSignalChange).where(
        AnalyticsSignalChange.run_id == run_id, AnalyticsSignalChange.id > after
    ).order_by(AnalyticsSignalChange.id).limit(page_size + 1))).all())
    more = len(rows) > page_size
    rows = rows[:page_size]
    items = []
    for row in rows:
        s = row.payload
        # Never export observed phone lists or customer display names for automated matching.
        metrics = s.get("metrics", {})
        allowed = ("last_purchase_date", "median_interval", "mean_interval", "q75", "mad",
                   "days_without_purchase", "overdue_threshold", "churn_threshold", "deviation_ratio",
                   "distinct_purchase_days", "history_span_days", "reference_revenue", "source_period_end")
        items.append(dict(source="sales-journal", signal_id=row.signal_id, revision=row.signal_revision,
            event_type=row.event_type, status=s["status"], family=s["family"], signal_type=s["signal_type"],
            subject=dict(kind=s["identity_kind"], version=s["normalization_version"], key=s["analytical_client_key"]),
            score=s["score"], confidence=s["confidence"], last_purchase=s["anchor_purchase_date"],
            analytical_eligible=s["status"] == "ACTIVE", generator_version=s["generator_version"],
            as_of=s["as_of"], calculated_at=row.changed_at.isoformat(),
            metrics={k: metrics[k] for k in allowed if k in metrics},
            mode="SHADOW", automation_allowed=False, run=public_run(run)))
    return dict(run=public_run(run), items=items, next_after=rows[-1].id if more else None,
                automation_allowed=False)


@router.get("/publications")
async def publications(after: int = Query(0, ge=0), page_size: int = Query(50, ge=1, le=100),
                       db: AsyncSession = Depends(get_db)):
    rows = list((await db.scalars(select(AnalyticsDeliveryPublication).where(
        AnalyticsDeliveryPublication.sequence > after
    ).order_by(AnalyticsDeliveryPublication.sequence).limit(page_size + 1))).all())
    more = len(rows) > page_size
    rows = rows[:page_size]
    return dict(items=[dict(sequence=r.sequence, run_id=r.run_id) for r in rows],
                next_after=rows[-1].sequence if more else None, automation_allowed=False)

# Separate stream: historical SHADOW rows/publications are never rewritten.


@router.get('/automation/publications')
async def automation_publications(after:int=Query(0,ge=0),page_size:int=Query(50,ge=1,le=100),db:AsyncSession=Depends(get_db)):
    from app.models import AnalyticsAutomationRelease as Release
    policy=await automation_available(db)
    rows=list((await db.scalars(select(Release).where(Release.sequence>after,Release.metadata_json['policy_revision'].as_integer()==policy.revision,Release.metadata_json['algorithm_policy'].as_string()==ALGORITHM_POLICY).order_by(Release.sequence).limit(page_size+1))).all())
    more=len(rows)>page_size;rows=rows[:page_size]
    return dict(items=[dict(sequence=r.sequence,run_id=r.id) for r in rows],next_after=rows[-1].sequence if more else None,automation_allowed=True)


@router.get('/automation/runs/{run_id}/changes')
async def automation_changes(run_id:str,after:int=Query(0,ge=0),page_size:int=Query(50,ge=1,le=100),db:AsyncSession=Depends(get_db)):
    from app.models import AnalyticsAutomationRelease as Release, AnalyticsAutomationEvent as Event
    from app.services.analytics_release_guard import current_release
    release=await current_release(db,run_id)
    rows=list((await db.scalars(select(Event).where(Event.release_id==run_id,Event.id>after).order_by(Event.id).limit(page_size+1))).all())
    more=len(rows)>page_size;rows=rows[:page_size]
    return dict(run=release.metadata_json['run'],items=[r.payload for r in rows],next_after=rows[-1].id if more else None,automation_allowed=True)


# Source-history evidence is independent of frozen analytical runs/releases.
from contextlib import asynccontextmanager
import logging
from sqlalchemy.exc import DBAPIError
from app.services import calltrack_identity_history as identity_source
from app.services.product_analytics_runtime import heavy_operation
from app.services.report_execution import REPORT_SETTINGS


@asynccontextmanager
async def identity_history_transaction(db):
    async with heavy_operation("calltrack_identity_history"):
        try:
            await db.connection(
                execution_options={"isolation_level": "REPEATABLE READ"}
            )
            await db.execute(text("SET TRANSACTION READ ONLY"))
            for statement in REPORT_SETTINGS:
                await db.execute(text(statement))
            yield
        except DBAPIError as exc:
            logging.getLogger(__name__).warning(
                "calltrack_identity_history failed sqlstate=%s",
                getattr(exc.orig, "sqlstate", None)
                or getattr(exc.orig, "pgcode", None)
                or "unknown",
            )
            # Never serialize driver exceptions (SQL, parameters or client identity).
            raise HTTPException(
                503, "Не удалось прочитать полную историю; подтверждение недоступно"
            ) from None
        finally:
            await db.rollback()


@router.get("/identity-history")
async def identity_history(
    client_key: str = Query(..., min_length=1, max_length=512),
    db: AsyncSession = Depends(get_db),
):
    identity_source.validate_key(client_key)
    async with identity_history_transaction(db):
        summary, _, _ = await identity_source.scan(db, client_key)
        return summary


@router.get("/identity-history/rows")
async def identity_history_rows(
    client_key: str = Query(..., min_length=1, max_length=512),
    history_hash: str = Query(..., pattern="^[a-f0-9]{64}$"),
    after: int = Query(0, ge=0, le=9223372036854775807),
    page_size: int = Query(50, ge=1, le=100),
    db: AsyncSession = Depends(get_db),
):
    identity_source.validate_key(client_key)
    async with identity_history_transaction(db):
        summary, items, next_after = await identity_source.scan(
            db, client_key, after=after, page_size=page_size
        )
        if summary["history_hash"] != history_hash:
            raise HTTPException(
                409,
                "История продаж изменилась; получите сводку и начните просмотр заново",
            )
        return dict(**summary, items=items, next_after=next_after)
