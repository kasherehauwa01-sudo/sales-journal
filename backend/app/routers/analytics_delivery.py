"""Authenticated, bounded immutable run export. SHADOW never authorizes tasks.

The audit sequence is a cursor ONLY within one SUCCEEDED frozen run. Poll latest
again after completing pagination; consumers compare per-signal revisions.
"""
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.database import get_db
from app.models import AnalyticsSignalRun, AnalyticsSignalState, AnalyticsSignalChange, AnalyticsDeliveryPublication
from app.routers.calltrack_integration import require_calltrack_token
from app.services.inactivity import json_value

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
