"""Transactional episode reconciliation. One query per batch, not per client."""
from datetime import datetime, timezone, timedelta, date
from decimal import Decimal
from uuid import uuid4
from sqlalchemy import func, select
from app.models import AnalyticsSignal, AnalyticsSignalChange, AnalyticsClientEvaluation
from app.services.client_identity import ANALYTICAL_NORMALIZATION_VERSION
from app.services.inactivity import GENERATOR_VERSION, PARAMETERS_VERSION, METRICS_SCHEMA_VERSION, evaluate, json_value


def snapshot(signal):
    return json_value({c.name: getattr(signal, c.name) for c in AnalyticsSignal.__table__.columns})


def record(db, signal, event, run, now):
    signal.revision += 1
    signal.updated_at = now
    signal.run_id = run.id
    db.add(AnalyticsSignalChange(signal_id=signal.id, signal_revision=signal.revision,
        event_type=event, changed_at=now, run_id=run.id, payload=snapshot(signal)))


async def reconcile_batch(db, run, features):
    keys = [f["client_key"] for f in features]
    conditions = (AnalyticsSignal.identity_kind == "normalized_name",
                  AnalyticsSignal.normalization_version == ANALYTICAL_NORMALIZATION_VERSION,
                  AnalyticsSignal.family == "INACTIVITY", AnalyticsSignal.analytical_client_key.in_(keys))
    existing = list((await db.scalars(select(AnalyticsSignal).where(*conditions).distinct(AnalyticsSignal.analytical_client_key).order_by(
        AnalyticsSignal.analytical_client_key, AnalyticsSignal.episode_no.desc()))).all())
    latest = {}
    for s in existing:
        latest.setdefault(s.analytical_client_key, s)
    now = datetime.now(timezone.utc)
    counts = dict(run.counters)
    source = run.metadata_json.get("source_decision")
    if not source or not source["shadow_allowed"] or source["effective_as_of"] != run.as_of.isoformat():
        raise ValueError("Evaluation requires accepted immutable source evidence")
    # The pure evaluator's HEALTHY parameter means calculation is permitted.
    # Historical business STALE is independent; retain it truthfully in evidence.
    # UNKNOWN coverage weight (zero) remains unchanged for DECLARED data.
    decisions = {f['client_key']: evaluate(f, run.as_of, freshness="HEALTHY") for f in features}
    for decision in decisions.values():
        decision["metrics"].update(coverage=source["coverage_status"],
            freshness=source["business_freshness"],
            source_decision={k: v for k, v in source.items() if k != "evidence"}, automation_allowed=False)
    closed = False
    for f in features:
        s = latest.get(f['client_key'])
        last = f.get('last_purchase_date')
        if s and s.as_of > run.as_of:raise ValueError('Cannot evaluate an episode backwards in time')
        if (s and s.status != 'RESOLVED' and decisions[f['client_key']]['reason'] != 'SOURCE_UNRELIABLE'
                and last and last > s.anchor_purchase_date):
            s.status, s.resolved_at, s.resolution_reason = 'RESOLVED', now, 'PURCHASE_OCCURRED'
            s.last_evaluated_at, s.as_of = now, run.as_of
            apply_evidence(s, f, decisions[f['client_key']], run)
            record(db, s, 'RESOLVED', run, now)
            counts['resolved'] = counts.get('resolved', 0)+1
            closed = True
    # Release all old open slots together, before new episodes are inserted.
    if closed:await db.flush()
    for f in features:
        key = f["client_key"]
        decision = decisions[key]
        s = latest.get(key)
        if s and s.as_of > run.as_of:
            raise ValueError("Cannot evaluate an episode backwards in time")
        if s and s.status != "RESOLVED":
            before = snapshot(s)
            old_status, old_type = s.status, s.signal_type
            if not decision["eligible"]:
                s.status = "SUSPENDED"
                s.resolution_reason = decision["reason"]
            elif f["last_purchase_date"] < s.anchor_purchase_date:
                s.status, s.resolved_at, s.resolution_reason = "RESOLVED", now, "DATA_CORRECTION"
            elif not decision["signal_type"]:
                s.status, s.resolved_at, s.resolution_reason = "RESOLVED", now, "MODEL_REEVALUATION"
            else:
                s.status, s.signal_type, s.resolution_reason = "ACTIVE", decision["signal_type"], None
            apply_evidence(s, f, decision, run)
            s.last_evaluated_at = now
            # Exclude inspection/bookkeeping timestamps from meaningful-change identity.
            ignored = {"last_evaluated_at", "run_id", "updated_at", "revision"}
            changed = any(before[k] != snapshot(s)[k] for k in before if k not in ignored)
            if changed:
                if s.status == "RESOLVED": event = "RESOLVED"
                elif old_status != "SUSPENDED" and s.status == "SUSPENDED": event = "SUSPENDED"
                elif old_status == "SUSPENDED" and s.status == "ACTIVE": event = "RESUMED"
                elif old_type != s.signal_type: event = "ESCALATED" if s.signal_type == "CHURN_RISK" else "DEESCALATED"
                else: event = "UPDATED"
                record(db, s, event, run, now)
                counts[event.lower()] = counts.get(event.lower(), 0)+1
        if (not s or s.status == "RESOLVED") and decision["signal_type"]:
            number = s.episode_no+1 if s else 1
            s = AnalyticsSignal(id=str(uuid4()), identity_kind="normalized_name",
                analytical_client_key=key, normalization_version=ANALYTICAL_NORMALIZATION_VERSION,
                family="INACTIVITY", mode="SHADOW", episode_no=number, status="ACTIVE",
                signal_type=decision["signal_type"], anchor_purchase_date=f["last_purchase_date"],
                detected_at=now, updated_at=now, last_evaluated_at=now, revision=0,
                resolved_at=None, resolution_reason=None)
            apply_evidence(s, f, decision, run)
            db.add(s)
            record(db, s, "CREATED", run, now)
            counts["created"] = counts.get("created", 0)+1
        metrics = decision["metrics"]
        db.add(AnalyticsClientEvaluation(run_id=run.id, client_key=key, display_name=f["display_name"],
            eligible=decision["eligible"], reason=decision["reason"],
            signal_type=s.signal_type if s else None, status=s.status if s else "NORMAL",
            score=s.score if s else 0, confidence=decision["confidence"],
            data=dict(features=metrics, signal=snapshot(s) if s else None)))
        counts["processed"] = counts.get("processed", 0)+1
        counts["eligible" if decision["eligible"] else "not_eligible"] = counts.get("eligible" if decision["eligible"] else "not_eligible", 0)+1
        counts[decision["reason"]] = counts.get(decision["reason"], 0)+1
        if s:
            counts[s.status] = counts.get(s.status, 0)+1
            if s.status == "ACTIVE":counts[s.signal_type] = counts.get(s.signal_type, 0)+1
        if len(f.get("known_phones", [])) > 1:counts["multiple_phones"] = counts.get("multiple_phones", 0)+1
    run.counters = counts


def apply_evidence(s, f, decision, run):
    s.display_name = f["display_name"]
    s.known_phones = f.get("known_phones", [])
    s.severity = 3 if s.signal_type == "CHURN_RISK" else 2
    s.score = Decimal(str(decision["score"])).quantize(Decimal('.01'))
    s.confidence = Decimal(str(decision["confidence"])).quantize(Decimal('.0001'))
    if s.status == "RESOLVED":s.score, s.severity = Decimal(0), 0
    s.metrics = decision["metrics"]
    s.metrics_schema_version, s.generator_version, s.parameters_version = METRICS_SCHEMA_VERSION, GENERATOR_VERSION, PARAMETERS_VERSION
    s.source_period_start, s.source_period_end = run.as_of-timedelta(days=364), run.as_of
    s.as_of = run.as_of
    s.source_coverage_status = run.metadata_json["coverage_status"]
    s.source_freshness_status = run.metadata_json["freshness"]["status"]
    s.run_id = run.id
