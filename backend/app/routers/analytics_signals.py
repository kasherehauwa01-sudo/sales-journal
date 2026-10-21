"""Read-only shadow diagnostics. GET never starts/rebuilds analytics."""
from typing import Literal
import base64
import hashlib
import json
from decimal import Decimal, InvalidOperation
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import and_, case, func, or_, select, text
from sqlalchemy.ext.asyncio import AsyncSession
from app.database import get_db
from app.config import settings
from app.models import AnalyticsClientEvaluation, AnalyticsSignalRun, AnalyticsSignalState
from app.services.analytics_signal_worker import source_revision
from app.services.settings_auth import require_settings_admin
from app.services.inactivity import json_value

router = APIRouter(prefix="/reports/analytics-signals", tags=["Shadow аналитика"],
                   dependencies=[Depends(require_settings_admin)])


@router.get("")
async def report(search: str = Query("", max_length=200),
                 signal_type: Literal["PURCHASE_OVERDUE", "CHURN_RISK"] | None = None,
                 status: Literal["ACTIVE", "SUSPENDED", "RESOLVED", "NORMAL"] | None = None,
                 eligible: bool | None = None, reason: str | None = Query(None, max_length=64),
                 min_score: float = Query(0, ge=0, le=100), min_confidence: float = Query(0, ge=0, le=1),
                 after: str | None = Query(None, max_length=4096), page_size: int = Query(50, ge=1, le=100),
                 db: AsyncSession = Depends(get_db)):
    if db.bind.dialect.name == "postgresql":
        await db.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"))
        await db.execute(text("SET LOCAL statement_timeout = '10s'"))
    control = await db.get(AnalyticsSignalState, 1)
    run = await db.get(AnalyticsSignalRun, control.published_run_id) if control and control.published_run_id else None
    active = await db.get(AnalyticsSignalRun, control.active_run_id) if control and control.active_run_id else None
    meta = dict(mode="SHADOW", automation_allowed=False,
                enabled=bool(settings.analytics_signals_enabled and control and control.enabled),
                configuration_enabled=settings.analytics_signals_enabled, durable_enabled=bool(control and control.enabled),
                active_run=json_value({"id": active.id, "status": active.status, "phase": active.phase,
                    "heartbeat_at": active.heartbeat_at, "error_count": active.error_count,
                    "error_code": active.error_code, "counters": active.counters,
                    "source_decision": active.metadata_json.get("source_decision")}) if active else None)
    meta["source_decision"] = (active.metadata_json.get("source_decision") if active else
                               run.metadata_json.get("source_decision") if run else None)
    scope = active.metadata_json.get("scope") if active else run.metadata_json.get("scope") if run else None
    meta["scope"] = {k: v for k, v in scope.items() if k != "client_keys"} if scope else None
    if not run:return dict(**meta, run=None, items=[], total=0, next_after=None, page_size=page_size)
    from app.services.analytics_signal_worker import last_closed_day
    public_metadata = {k: v for k, v in run.metadata_json.items() if k != "scope"}
    meta["run"] = json_value(dict(id=run.id, as_of=run.as_of, status=run.status,
        started_at=run.started_at, finished_at=run.finished_at,
        duration_seconds=(run.finished_at-run.started_at).total_seconds(),
        error_count=run.error_count, source_revision=run.source_revision,
        stale=run.as_of < last_closed_day() or run.source_revision != await source_revision(db),
        diagnostics=run.counters, **public_metadata))
    table = AnalyticsClientEvaluation
    conditions = [table.run_id == run.id, table.score >= min_score, table.confidence >= min_confidence]
    if search:conditions.append(table.display_name.ilike(f"%{search}%") | table.client_key.ilike(f"%{search}%"))
    if signal_type:conditions.append(table.signal_type == signal_type)
    if status:conditions.append(table.status == status)
    if eligible is not None:conditions.append(table.eligible == eligible)
    if reason:conditions.append(table.reason == reason)
    total = int(await db.scalar(select(func.count()).select_from(table).where(*conditions)) or 0)
    rank = case((table.status == "ACTIVE", 0), (table.status == "SUSPENDED", 1), else_=2)
    signature = hashlib.sha256(json.dumps([search,signal_type,status,eligible,reason,float(min_score),float(min_confidence)]).encode()).hexdigest()
    if after:
        try:
            cursor=json.loads(base64.urlsafe_b64decode(after.encode()).decode())
            if cursor[0]!=run.id or cursor[1]!=signature:
                raise HTTPException(409,"Срез или фильтры изменились: начните с первой страницы")
            _,_,r,score,key=cursor
            score=Decimal(score)
            if r not in (0,1,2) or not score.is_finite() or not isinstance(key,str):raise ValueError()
        except HTTPException:raise
        except (ValueError,TypeError,KeyError,IndexError,UnicodeError,InvalidOperation):
            raise HTTPException(422,"Некорректный cursor") from None
        conditions.append(or_(rank>r,and_(rank==r,table.score<score),and_(rank==r,table.score==score,table.client_key>key)))
    rows = (await db.scalars(select(table).where(*conditions).order_by(
        rank,table.score.desc(), table.client_key).limit(page_size+1))).all()
    more=len(rows)>page_size
    rows=rows[:page_size]
    last=rows[-1] if rows else None
    cursor = base64.urlsafe_b64encode(json.dumps([run.id,signature,
        0 if last.status=='ACTIVE' else 1 if last.status=='SUSPENDED' else 2,str(last.score),last.client_key]).encode()).decode() if more else None
    return dict(**meta, total=total, next_after=cursor, page_size=page_size, items=[json_value(dict(
        analytical_client_key=r.client_key, display_name=r.display_name, eligible=r.eligible, reason=r.reason,
        status=r.status, signal_type=r.signal_type, score=r.score, confidence=r.confidence, **r.data)) for r in rows])
