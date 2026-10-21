"""Pure, explainable SHADOW evaluation. No tasks, network or side effects."""
from datetime import date, timedelta
from decimal import Decimal
from math import ceil, log1p

GENERATOR_VERSION = "inactivity/1"
PARAMETERS_VERSION = 1
METRICS_SCHEMA_VERSION = 1
POLICY = dict(min_days=8, min_gaps=7, min_span=56, max_dispersion=0.75,
              max_interval=60, cycle_days=365, reference_days=180,
              value_ceiling=300000, loss_ceiling=30000, phone_limit=10,
              confidence_version="quality/1-unknown-coverage-zero", score_version="commercial/1")


def json_value(value):
    if isinstance(value, (date,)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, float):
        return round(value, 8)
    if isinstance(value, dict):
        return {key: json_value(v) for key, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_value(v) for v in value]
    return value


def evaluate(features, as_of, *, freshness="HEALTHY", coverage="UNKNOWN"):
    if coverage not in {"UNKNOWN", "CONFIRMED"}:
        raise ValueError("Unsupported coverage policy")
    f = dict(features)
    last = f.get("last_purchase_date")
    days = (as_of-last).days if last else None
    mid = float(f.get("median_interval") or 0)
    mad = float(f.get("mad") or 0)
    dispersion = mad/max(mid, 1)
    count, gaps, span = (int(f.get(k) or 0) for k in ("distinct_purchase_days", "gap_count", "history_span_days"))
    reason = "ELIGIBLE"
    if not last or count == 0:
        reason = "NO_VALID_PURCHASE_DAYS"
    elif count < POLICY["min_days"] or gaps < POLICY["min_gaps"] or span < POLICY["min_span"] or mid <= 0:
        reason = "INSUFFICIENT_HISTORY"
    elif dispersion > POLICY["max_dispersion"]:
        reason = "IRREGULAR_PURCHASE_PATTERN"
    elif mid > POLICY["max_interval"]:
        reason = "INTERVAL_TOO_LONG"
    if freshness != "HEALTHY":
        reason = "SOURCE_UNRELIABLE"
    eligible = reason == "ELIGIBLE"
    overdue = max(ceil(1.5*mid), ceil(float(f.get("q75") or 0))+2, ceil(mid)+2) if mid else None
    churn = max(ceil(3*mid), ceil(float(f.get("q75") or 0))+7, 14) if mid else None
    ratio = days/mid if days is not None and mid else None
    kind = ("CHURN_RISK" if days >= churn else "PURCHASE_OVERDUE" if days >= overdue else None) if eligible else None
    h, r, a, c = min(gaps/20, 1), max(0, 1-dispersion), min(span/180, 1), (1 if coverage == "CONFIRMED" else 0)
    confidence = round(0.35*h+0.35*r+0.20*c+0.10*a, 4)
    raw_reference = float(f.get("reference_revenue_180") or 0)
    # Net stored receipts may be negative; retain them and expose score clipping.
    daily = max(raw_reference, 0)/180
    reference = daily*28
    loss = daily*max((days or 0)-mid, 0)
    severity = min(max(((ratio or 0)-1)/2, 0), 1)
    value = min(max(log1p(reference)/log1p(POLICY["value_ceiling"]), 0), 1)
    loss_component = min(max(loss/POLICY["loss_ceiling"], 0), 1)
    score = round(100*(.5*severity+.3*value+.2*loss_component), 2) if kind else 0
    f.update(days_without_purchase=days, regularity_dispersion=dispersion,
             overdue_threshold=overdue, churn_threshold=churn, deviation_ratio=ratio,
             reference_revenue=reference, estimated_loss=loss,
             confidence_components=dict(history=h, regularity=r, coverage=c, history_span=a),
             score_components=dict(severity=severity, value=value, loss=loss_component),
             confidence_policy=POLICY["confidence_version"], score_policy=POLICY["score_version"],
             parameters=POLICY, automation_allowed=False, coverage=coverage, freshness=freshness,
             reference_revenue_provenance="SUM stored sales.total_amount, last 180 closed days; not confirmed complete",
             source_period_start=as_of-timedelta(days=364), source_period_end=as_of)
    # UNKNOWN is allowed in shadow; confidence is never an eligibility bypass.
    return dict(eligible=eligible, reason=reason, signal_type=kind, score=score,
                confidence=confidence, metrics=json_value(f))
