"""Bounded PK scan → SQL purchase days → features → persistent shadow episodes."""
from datetime import date, datetime, timedelta, timezone
from uuid import uuid4
from zoneinfo import ZoneInfo
from sqlalchemy import case, delete, func, literal, select, text
from sqlalchemy.dialects.postgresql import insert
from app.config import settings
from app.models import (AnalyticsSignalRun, AnalyticsSignalState, AnalyticsPurchaseDay,
    AnalyticsObservedPhone, AnalyticsSignal, ImportBatch, Sale, SeasonalityState)
from app.services.client_identity import analytical_name_sql, ANALYTICAL_NORMALIZATION_VERSION
from app.services.client_features import load_inactivity_features
from app.services.inactivity import GENERATOR_VERSION, PARAMETERS_VERSION, POLICY
from app.services.analytics_signal_store import reconcile_batch
from app.services.seasonality_worker import advisory, SOURCE_LOCK, verify
from app.services.product_analytics_runtime import ensure_headroom

WORKER_LOCK = 734190223


def last_closed_day():
    # sale_date is a calendar Date imported from documents, not a UTC timestamp.
    return datetime.now(ZoneInfo(settings.autoload_timezone)).date()-timedelta(days=1)


async def source_revision(db):
    return int(await db.scalar(select(SeasonalityState.source_revision).where(SeasonalityState.id == 1)) or 0)


async def freshness(db, now):
    pending = await db.scalar(select(ImportBatch.id).where(ImportBatch.status.in_(["queued", "processing"])).limit(1))
    latest = (await db.execute(select(ImportBatch.id, ImportBatch.status, ImportBatch.error_rows, ImportBatch.finished_at)
                              .order_by(ImportBatch.id.desc()).limit(1))).first()
    if pending or (latest and (latest.status != "completed" or latest.error_rows)):
        return dict(status="UNHEALTHY", reason="PENDING_OR_FAILED_IMPORT", coverage_status="UNKNOWN")
    finished = latest.finished_at if latest else None
    if finished and finished.tzinfo is None:finished = finished.replace(tzinfo=timezone.utc)
    age = (now-finished).total_seconds() if finished else None
    status = "HEALTHY" if age is not None and 0 <= age <= settings.analytics_signals_freshness_hours*3600 else "STALE"
    return dict(status=status, reason="RECENT_COMPLETED_IMPORT" if status == "HEALTHY" else "NO_RECENT_COMPLETED_IMPORT",
                last_import_id=latest.id if latest else None, age_seconds=age, coverage_status="UNKNOWN")


async def state(db):
    value = await db.scalar(select(AnalyticsSignalState).where(AnalyticsSignalState.id == 1).with_for_update())
    if value is None:
        value = AnalyticsSignalState(id=1, enabled=False)
        db.add(value)
        await db.flush()
    return value


async def begin_run(db, control, as_of):
    now = datetime.now(timezone.utc)
    revision = await source_revision(db)
    previous = await db.get(AnalyticsSignalRun, control.published_run_id) if control.published_run_id else None
    if previous and previous.as_of > as_of:raise ValueError("as_of precedes last successful run")
    if previous and previous.as_of == as_of and previous.source_revision == revision and previous.metadata_json["generator_version"] == GENERATOR_VERSION:
        # Freshness changes require reevaluation even without sales changes.
        if previous.metadata_json["freshness"]["status"] == (await freshness(db, now))["status"]:
            return previous
    run = AnalyticsSignalRun(id=str(uuid4()), status="RUNNING", phase="scan", as_of=as_of,
        source_revision=revision, upper_sale_id=int(await db.scalar(select(func.max(Sale.id))) or 0),
        sale_cursor=0, client_cursor="", started_at=now, heartbeat_at=now,
        counters={k: 0 for k in ("processed", "eligible", "not_eligible", "PURCHASE_OVERDUE", "CHURN_RISK",
            "ACTIVE", "SUSPENDED", "RESOLVED", "INSUFFICIENT_HISTORY", "IRREGULAR_PURCHASE_PATTERN",
            "INTERVAL_TOO_LONG", "NO_VALID_PURCHASE_DAYS", "SOURCE_UNRELIABLE", "missing_empty_name", "multiple_phones")}, error_count=0,
        metadata_json=dict(mode="SHADOW", automation_allowed=False, normalization_version=ANALYTICAL_NORMALIZATION_VERSION,
            generator_version=GENERATOR_VERSION, parameters_version=PARAMETERS_VERSION, parameters=POLICY,
            coverage_status="UNKNOWN", complete_through=None, as_of_reason="EXPLICIT_CLOSED_DAY" if as_of != last_closed_day() else "LAST_CLOSED_BUSINESS_DAY",
            timezone=settings.autoload_timezone, freshness=await freshness(db, now)))
    db.add(run)
    await db.flush()
    control.active_run_id = run.id
    return run


async def scan_batch(db, run, size):
    ids = list((await db.scalars(select(Sale.id).where(Sale.id > run.sale_cursor, Sale.id <= run.upper_sale_id).order_by(Sale.id).limit(size))).all())
    if not ids:
        run.phase = "evaluate"
        return
    key = analytical_name_sql(Sale.client)
    base = [Sale.id > run.sale_cursor, Sale.id <= ids[-1], Sale.sale_date <= run.as_of]
    missing = int(await db.scalar(select(func.count()).select_from(Sale).where(*base, (Sale.client.is_(None)) | (key == ""))) or 0)
    counters = dict(run.counters)
    counters["missing_empty_name"] = counters.get("missing_empty_name", 0)+missing
    counters["scanned_sales"] = counters.get("scanned_sales", 0)+len(ids)
    run.counters = counters
    query = select(literal(run.id), key, Sale.sale_date, func.min(Sale.client), func.count(),
                   func.sum(case((Sale.total_amount > 0, 1), else_=0)), func.sum(Sale.total_amount)).where(
        *base, Sale.client.is_not(None), key != "").group_by(key, Sale.sale_date)
    statement = insert(AnalyticsPurchaseDay).from_select(
        ["run_id", "client_key", "sale_date", "display_name", "checks", "positive_checks", "revenue"], query)
    await db.execute(statement.on_conflict_do_update(index_elements=["run_id", "client_key", "sale_date"], set_={
        "display_name": func.least(AnalyticsPurchaseDay.display_name, statement.excluded.display_name),
        "checks": AnalyticsPurchaseDay.checks+statement.excluded.checks,
        "positive_checks": AnalyticsPurchaseDay.positive_checks+statement.excluded.positive_checks,
        "revenue": AnalyticsPurchaseDay.revenue+statement.excluded.revenue}))
    # Imported phones already follow normalize_phone; reproduce its legacy '.' split.
    digits = func.regexp_replace(func.split_part(func.coalesce(Sale.phone, ""), ".", 1), "[^0-9]", "", "g")
    phone = literal("+7")+func.right(digits, 10)
    phones_query = select(literal(run.id), key, phone, func.max(Sale.sale_date)).where(
        *base, Sale.client.is_not(None), key != "", func.length(digits) >= 10).group_by(key, phone)
    p = insert(AnalyticsObservedPhone).from_select(["run_id", "client_key", "phone", "last_seen"], phones_query)
    await db.execute(p.on_conflict_do_update(index_elements=["run_id", "client_key", "phone"],
        set_={"last_seen": func.greatest(AnalyticsObservedPhone.last_seen, p.excluded.last_seen)}))
    # Keep at most ten most recently observed distinct phones per client, on disk too.
    affected = list((await db.scalars(select(key).where(*base, Sale.client.is_not(None), key != "").distinct())).all())
    await db.execute(text("""DELETE FROM analytics_observed_phones p USING (
      SELECT run_id,client_key,phone,row_number() OVER(PARTITION BY client_key ORDER BY last_seen DESC,phone) AS rn
      FROM analytics_observed_phones WHERE run_id=:run AND client_key=ANY(CAST(:keys AS text[]))
    ) ranked WHERE p.run_id=ranked.run_id AND p.client_key=ranked.client_key AND p.phone=ranked.phone AND ranked.rn>10"""), {"run": run.id, "keys": affected})
    run.sale_cursor = ids[-1]


async def evaluate_batch(db, run, size):
    # Open/resolved clients remain in evaluation even after all source rows are deleted.
    from app.models import AnalyticsClientEvaluation
    q = select(AnalyticsPurchaseDay.client_key).where(AnalyticsPurchaseDay.run_id == run.id).union(
        select(AnalyticsSignal.analytical_client_key).where(AnalyticsSignal.identity_kind == "normalized_name",
            AnalyticsSignal.normalization_version == ANALYTICAL_NORMALIZATION_VERSION)).subquery()
    keys = list((await db.scalars(select(q.c.client_key).where(q.c.client_key > run.client_cursor).order_by(q.c.client_key).limit(size))).all())
    if not keys:
        run.phase, run.status, run.finished_at = "complete", "SUCCEEDED", datetime.now(timezone.utc)
        return
    features = {f["client_key"]: f for f in await load_inactivity_features(db, run.id, keys, run.as_of)}
    phones = {}
    for k, number in (await db.execute(select(AnalyticsObservedPhone.client_key, AnalyticsObservedPhone.phone).where(
        AnalyticsObservedPhone.run_id == run.id, AnalyticsObservedPhone.client_key.in_(keys)).order_by(AnalyticsObservedPhone.phone))):
        phones.setdefault(k, []).append(number)
    for k in keys:
        features.setdefault(k, dict(client_key=k, display_name=k))
        features[k]["known_phones"] = phones.get(k, [])
        features[k]["phones_limit"] = POLICY["phone_limit"]
        features[k]["phones_may_be_truncated"] = len(phones.get(k, [])) == POLICY["phone_limit"]
    await reconcile_batch(db, run, list(features.values()))
    run.client_cursor = keys[-1]


async def run_batch(factory, engine, *, as_of=None, scan_size=2000, client_size=250, configuration_enabled=None, leadership=None):
    if engine.dialect.name != "postgresql":raise ValueError("Shadow feature worker requires PostgreSQL")
    if not 1 <= scan_size <= 5000 or not 1 <= client_size <= 1000:raise ValueError("Invalid batch limits")
    as_of = as_of or last_closed_day()
    if as_of > last_closed_day():raise ValueError("as_of must be a closed business day")
    enabled = settings.analytics_signals_enabled if configuration_enabled is None else configuration_enabled
    if not enabled:return {"status": "DISABLED", "mode": "SHADOW", "automation_allowed": False}
    ensure_headroom()
    async with advisory(engine, key=SOURCE_LOCK) as source_lock:
        async with factory() as db:
            await db.execute(text("SET LOCAL statement_timeout = '30s'"))
            await db.execute(text("SET LOCAL lock_timeout = '2s'"))
            control = await state(db)
            if not control.enabled:
                await db.commit()
                return {"status": "DISABLED", "mode": "SHADOW", "automation_allowed": False}
            run = await db.get(AnalyticsSignalRun, control.active_run_id) if control.active_run_id else None
            if run and run.status in {"RUNNING", "PAUSED", "FAILED"}:
                if run.as_of != as_of:raise ValueError("Resume the active run with its original as_of")
                if (run.metadata_json["generator_version"] != GENERATOR_VERSION
                    or run.metadata_json["parameters_version"] != PARAMETERS_VERSION
                    or run.metadata_json["normalization_version"] != ANALYTICAL_NORMALIZATION_VERSION):
                    raise ValueError("Active run uses a different algorithm/identity policy")
                run.status = "RUNNING"
            else:
                run = await begin_run(db, control, as_of)
            if run.status == "SUCCEEDED":
                await db.commit()
                return {"status": run.status, "run_id": run.id, "phase": run.phase}
            if run.phase == "scan" and run.source_revision != await source_revision(db):
                run.status, run.error_code = "SUPERSEDED", "SOURCE_CHANGED_DURING_SCAN"
                run.finished_at = datetime.now(timezone.utc)
                control.active_run_id = None
            elif run.phase == "scan":await scan_batch(db, run, scan_size)
            else:await evaluate_batch(db, run, client_size)
            run.heartbeat_at = datetime.now(timezone.utc)
            if run.status == "SUCCEEDED":
                control.published_run_id, control.active_run_id = run.id, None
            await verify(source_lock, SOURCE_LOCK)
            if leadership is not None:await verify(leadership, WORKER_LOCK)
            await db.commit()
            return {"status": run.status, "run_id": run.id, "phase": run.phase,
                    "sale_cursor": run.sale_cursor, "client_cursor": run.client_cursor, "counters": run.counters}


async def record_failure(factory, code):
    async with factory() as db:
        control = await state(db)
        if control.active_run_id:
            run = await db.get(AnalyticsSignalRun, control.active_run_id)
            run.status, run.error_code = "FAILED", code
            run.error_count += 1
            run.heartbeat_at = datetime.now(timezone.utc)
        await db.commit()
