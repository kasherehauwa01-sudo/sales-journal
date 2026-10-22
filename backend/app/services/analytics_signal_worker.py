"""Bounded PK scan → SQL purchase days → features → persistent shadow episodes."""
from datetime import date, datetime, timedelta, timezone
from uuid import uuid4
from dataclasses import replace
from zoneinfo import ZoneInfo
from sqlalchemy import case, cast, Text, delete, func, literal, select, text
from sqlalchemy.dialects.postgresql import insert, ARRAY
from app.config import settings
from app.models import (AnalyticsSignalRun, AnalyticsSignalState, AnalyticsPurchaseDay,
    AnalyticsObservedPhone, AnalyticsSignal, ImportBatch, Sale, SeasonalityState)
from app.services.client_identity import analytical_name_sql, ANALYTICAL_NORMALIZATION_VERSION
from app.services.client_features import load_inactivity_features
from app.services.inactivity import GENERATOR_VERSION, PARAMETERS_VERSION, POLICY
from app.services.analytics_signal_store import reconcile_batch
from app.services.seasonality_worker import advisory, SOURCE_LOCK, verify
from app.services.product_analytics_runtime import ensure_headroom
from app.services.analytics_source import resolve_stored_sales_source, STORED_SALES_POLICY_VERSION, stored_sales_contract_valid
from app.services.analytics_scope import load_analytics_scope, SCOPE_POLICY
from app.services.clients_vr import ClientsVrError

WORKER_LOCK = 734190223


def last_closed_day():
    # sale_date is a calendar Date imported from documents, not a UTC timestamp.
    return datetime.now(ZoneInfo(settings.autoload_timezone)).date()-timedelta(days=1)


async def source_revision(db):
    return int(await db.scalar(select(SeasonalityState.source_revision).where(SeasonalityState.id == 1)) or 0)


async def state(db):
    value = await db.scalar(select(AnalyticsSignalState).where(AnalyticsSignalState.id == 1).with_for_update())
    if value is None:
        value = AnalyticsSignalState(id=1, enabled=False)
        db.add(value)
        await db.flush()
    return value


async def begin_run(db, control, decision, scope):
    now = datetime.now(timezone.utc)
    previous = await db.get(AnalyticsSignalRun, control.published_run_id) if control.published_run_id else None
    if decision.effective_as_of and previous and previous.as_of > decision.effective_as_of:
        decision = replace(decision, shadow_allowed=False,
            reason_codes=decision.reason_codes + ("EFFECTIVE_DATE_PRECEDES_PUBLISHED_RUN",))
    if previous and previous.metadata_json.get("scope", {}).get("policy") != scope.get("policy"):
        decision = replace(decision, shadow_allowed=False,
            reason_codes=decision.reason_codes + ("ANALYTIC_SCOPE_CHANGED_REQUIRES_ISOLATED_RUN",))
    evidence = decision.to_json()
    # Requested date/business context are part of reuse, not only the cutoff.
    candidate = previous
    if control.active_run_id:
        blocked = await db.get(AnalyticsSignalRun, control.active_run_id)
        if blocked and blocked.status == "BLOCKED":candidate = blocked
    if (candidate and candidate.status in {"SUCCEEDED", "BLOCKED"}
            and (candidate.metadata_json.get("source_decision") or {}).get("policy_version") == STORED_SALES_POLICY_VERSION
            and candidate.metadata_json.get("source_decision") == evidence
            and candidate.metadata_json.get("scope") == scope
            and candidate.metadata_json.get("generator_version") == GENERATOR_VERSION
            and candidate.metadata_json.get("parameters_version") == PARAMETERS_VERSION
            and candidate.metadata_json.get("normalization_version") == ANALYTICAL_NORMALIZATION_VERSION
            and candidate.metadata_json.get("parameters") == POLICY
            and stored_sales_contract_valid(candidate.metadata_json)):
        return candidate
    allowed = decision.shadow_allowed
    run = AnalyticsSignalRun(id=str(uuid4()), status="RUNNING" if allowed else "BLOCKED",
        phase="scan" if allowed else "blocked",
        # NOT NULL legacy column: blocked runs expose effective_as_of via evidence (possibly NULL).
        as_of=decision.effective_as_of or min(decision.requested_as_of, decision.last_closed_business_day),
        source_revision=decision.source_revision, upper_sale_id=int(await db.scalar(select(func.max(Sale.id))) or 0),
        sale_cursor=0, client_cursor="", started_at=now, heartbeat_at=now,
        finished_at=None if allowed else now,
        error_code=None if allowed else "SOURCE_DECISION_BLOCKED",
        counters={k: 0 for k in ("processed", "eligible", "not_eligible", "PURCHASE_OVERDUE", "CHURN_RISK",
            "ACTIVE", "SUSPENDED", "RESOLVED", "INSUFFICIENT_HISTORY", "IRREGULAR_PURCHASE_PATTERN",
            "INTERVAL_TOO_LONG", "NO_VALID_PURCHASE_DAYS", "SOURCE_UNRELIABLE", "missing_empty_name", "multiple_phones")}, error_count=0,
        metadata_json=dict(mode="SHADOW", automation_allowed=False, normalization_version=ANALYTICAL_NORMALIZATION_VERSION,
            generator_version=GENERATOR_VERSION, parameters_version=PARAMETERS_VERSION, parameters=dict(POLICY),
            coverage_status=decision.coverage_status, complete_through=None,
            as_of_reason=decision.reason_codes[0], source_decision=evidence, scope=scope,
            timezone=settings.autoload_timezone, freshness=dict(status=decision.business_freshness,
                reason=decision.reason_codes[0], coverage_status=decision.coverage_status)))
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
    scope = run.metadata_json["scope"]
    if scope["department"] is not None:
        base.append(Sale.department == scope["department"])
    if scope["client_keys"] is not None:
        base.append(key == func.any(cast(scope["client_keys"], ARRAY(Text))))
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
    existing = select(AnalyticsSignal.analytical_client_key).where(AnalyticsSignal.identity_kind == "normalized_name",
        AnalyticsSignal.normalization_version == ANALYTICAL_NORMALIZATION_VERSION)
    scope = run.metadata_json["scope"]
    if scope["client_keys"] is not None:
        existing = existing.where(AnalyticsSignal.analytical_client_key == func.any(cast(scope["client_keys"], ARRAY(Text))))
    q = select(AnalyticsPurchaseDay.client_key).where(AnalyticsPurchaseDay.run_id == run.id).union(existing).subquery()
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


def supersede_run(run, control, reason):
    run.status, run.error_code = "SUPERSEDED", reason
    run.finished_at = run.heartbeat_at = datetime.now(timezone.utc)
    control.active_run_id = None


def resume_blocker(run, current, scope):
    """Validate the frozen snapshot before either checkpoint phase can mutate it."""
    frozen = run.metadata_json.get("source_decision") or {}
    if frozen.get("policy_version") != STORED_SALES_POLICY_VERSION:
        return "SOURCE_POLICY_CHANGED"
    if (frozen.get("source_revision") != run.source_revision
            or run.source_revision != current.source_revision
            or frozen.get("evidence_fingerprint") != current.evidence_fingerprint):
        return "SOURCE_EVIDENCE_CHANGED"
    if not current.shadow_allowed:
        return "SOURCE_DECISION_BLOCKED"
    if frozen.get("timezone") != current.timezone:
        return "SOURCE_CONTEXT_CHANGED"
    # A formerly unclosed request may have a later permissible cutoff today.
    # Resume still scans/evaluates at the ORIGINAL run.as_of and keeps its frozen
    # evidence. Advancing the wall clock alone must not restart a valid run.
    if (frozen.get("requested_as_of") != current.requested_as_of.isoformat()
            or frozen.get("effective_as_of") != run.as_of.isoformat()
            or not run.as_of <= current.effective_as_of <= current.requested_as_of):
        return "SOURCE_CUTOFF_CHANGED"
    if run.metadata_json.get("scope") != scope:
        return "ANALYTIC_SCOPE_CHANGED"
    return None


async def stored_source_context(db, as_of):
    closed = last_closed_day()
    upper = int(await db.scalar(select(func.max(Sale.id))) or 0)
    revision = await source_revision(db)
    pending = await db.scalar(select(ImportBatch.id).where(
        ImportBatch.status.in_(["queued", "processing"])).limit(1))
    scope = dict(department="Авиаторов", buyer_types=["Оптовик", "Корпоративный"],
        client_keys=[], policy=SCOPE_POLICY, buyer_type_source="UNAVAILABLE")
    reason = None
    present = None
    # Pending imports also cover a crashed importer that released its advisory
    # lock after committing only some rows. Old failed imports do not prove
    # completeness, but do not prevent diagnostics over stored rows.
    if pending is None:
        try:
            scope = await load_analytics_scope()
            key = analytical_name_sql(Sale.client)
            conditions = [Sale.sale_date <= min(as_of, closed), Sale.client.is_not(None), key != ""]
            if scope["department"] is not None:
                conditions.append(Sale.department == scope["department"])
            if scope["client_keys"] is not None:
                conditions.append(key == func.any(cast(scope["client_keys"], ARRAY(Text))))
            present = await db.scalar(select(Sale.id).where(*conditions).limit(1))
            if scope["client_keys"] == []:
                reason = "BUYER_TYPE_SCOPE_EMPTY"
            elif present is None:
                reason = "NO_SCOPED_STORED_SALES"
        except ClientsVrError:
            reason = "BUYER_TYPE_SOURCE_UNAVAILABLE"
    decision = resolve_stored_sales_source(as_of, last_closed_business_day=closed,
        source_revision=revision, timezone=settings.autoload_timezone,
        has_sales=present is not None, upper_sale_id=upper, imports_pending=pending is not None)
    if reason:
        decision = replace(decision, shadow_allowed=False, effective_as_of=None,
            reason_codes=decision.reason_codes + (reason,))
    return decision, scope


async def run_batch(factory, engine, *, as_of=None, scan_size=2000, client_size=250, configuration_enabled=None, leadership=None):
    if engine.dialect.name != "postgresql":raise ValueError("Shadow feature worker requires PostgreSQL")
    if not 1 <= scan_size <= 5000 or not 1 <= client_size <= 1000:raise ValueError("Invalid batch limits")
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
            # Policy changes terminate the old checkpoint before any source read,
            # evaluation or publication. Its frozen metadata and results are kept.
            if run and run.status in {"RUNNING", "PAUSED", "FAILED"}:
                frozen = run.metadata_json.get("source_decision") or {}
                incompatible = None
                if frozen.get("policy_version") != STORED_SALES_POLICY_VERSION:
                    incompatible = "SOURCE_POLICY_CHANGED"
                elif (run.metadata_json.get("generator_version") != GENERATOR_VERSION
                        or run.metadata_json.get("parameters_version") != PARAMETERS_VERSION
                        or run.metadata_json.get("normalization_version") != ANALYTICAL_NORMALIZATION_VERSION
                        or run.metadata_json.get("parameters") != POLICY):
                    incompatible = "ALGORITHM_POLICY_CHANGED"
                elif not stored_sales_contract_valid(run.metadata_json):
                    incompatible = "SHADOW_CONTRACT_CHANGED"
                elif run.phase not in {"scan", "evaluate"}:
                    incompatible = "INVALID_CHECKPOINT_PHASE"
                if incompatible:
                    supersede_run(run, control, incompatible)
                    await verify(source_lock, SOURCE_LOCK)
                    if leadership is not None:await verify(leadership, WORKER_LOCK)
                    await db.commit()
                    return dict(status=run.status, run_id=run.id, phase=run.phase,
                                error_code=run.error_code, automation_allowed=False)
                # Resolve the implicit date only after reading the locked active
                # checkpoint. Midnight must not change its original request.
                if as_of is None:
                    as_of = date.fromisoformat(frozen["requested_as_of"])
                if frozen.get("requested_as_of") != as_of.isoformat():
                    raise ValueError("Resume the active run with its original requested_as_of")
            as_of = as_of or last_closed_day()
            current, scope = await stored_source_context(db, as_of)
            if run and run.status in {"RUNNING", "PAUSED", "FAILED"}:
                reason = resume_blocker(run, current, scope)
                if reason:
                    supersede_run(run, control, reason)
                    await verify(source_lock, SOURCE_LOCK)
                    if leadership is not None:await verify(leadership, WORKER_LOCK)
                    await db.commit()
                    return dict(status=run.status, run_id=run.id, phase=run.phase,
                                error_code=run.error_code, automation_allowed=False)
                run.status = "RUNNING"
                run.error_code = None
            else:
                run = await begin_run(db, control, current, scope)
            if run.status in {"SUCCEEDED", "BLOCKED"}:
                await db.commit()
                return {"status": run.status, "run_id": run.id, "phase": run.phase,
                        "source_decision": run.metadata_json.get("source_decision"), "automation_allowed": False}
            if run.phase == "scan":await scan_batch(db, run, scan_size)
            else:await evaluate_batch(db, run, client_size)
            run.heartbeat_at = datetime.now(timezone.utc)
            if run.status == "SUCCEEDED":
                control.published_run_id, control.active_run_id = run.id, None
                from app.services.analytics_delivery import publish_delivery
                await publish_delivery(db, run)
            await verify(source_lock, SOURCE_LOCK)
            if leadership is not None:await verify(leadership, WORKER_LOCK)
            await db.commit()
            return {"status": run.status, "run_id": run.id, "phase": run.phase,
                    "sale_cursor": run.sale_cursor, "client_cursor": run.client_cursor, "counters": run.counters,
                    "heartbeat_at": run.heartbeat_at.isoformat(),
                    "source_decision": run.metadata_json.get("source_decision"), "automation_allowed": False}


async def record_failure(factory, code, *, run_id=None, heartbeat_at=None):
    # An error from a previous transaction must never mark a replacement run.
    # No ID means no committed checkpoint was identified; do not create state.
    if run_id is None or heartbeat_at is None:
        return False
    async with factory() as db:
        control = await db.get(AnalyticsSignalState, 1, with_for_update=True)
        if not control or control.active_run_id != run_id:
            return False
        run = await db.get(AnalyticsSignalRun, run_id)
        if (not run or run.status not in {"RUNNING", "FAILED"} or not control.enabled
                or run.heartbeat_at != heartbeat_at):
            return False
        run.status, run.error_code = "FAILED", code
        run.error_count += 1
        run.heartbeat_at = datetime.now(timezone.utc)
        await db.commit()
        return True
