"""Individual purchase-cycle pilot; never grants automation permission."""
from math import floor, isfinite

from app.services import inactivity

ALGORITHM_POLICY = 'aviator-churn-pilot/2'
GENERATOR_VERSION = 'inactivity/aviator-churn-pilot/2'
PARAMETERS_VERSION = 3
POLICY = dict(policy=ALGORITHM_POLICY, min_days=3, min_gaps=2,
              cycle_days=365, reference_days=180, phone_limit=10,
              eligibility='positive-distinct-store-days-with-valid-cycle',
              churn_rule='days_without_purchase>median_interval',
              cadence_limits=False,
              overdue_enabled=False, confidence_version=inactivity.POLICY['confidence_version'],
              score_version=inactivity.POLICY['score_version'],
              value_ceiling=inactivity.POLICY['value_ceiling'], loss_ceiling=inactivity.POLICY['loss_ceiling'])


def algorithm(scope):
    from app.services.analytics_scope import SCOPE_POLICY
    if (scope.get('policy') == SCOPE_POLICY and scope.get('department') == 'Авиаторов'
            and scope.get('buyer_types') == ['Оптовик', 'Корпоративный']):
        return GENERATOR_VERSION, PARAMETERS_VERSION, POLICY
    return inactivity.GENERATOR_VERSION, inactivity.PARAMETERS_VERSION, inactivity.POLICY


def evaluate(features, as_of, *, freshness='HEALTHY', coverage='UNKNOWN'):
    # Keep existing score/quality definitions, but neither the legacy 3x cadence
    # threshold nor the retired 45-day rule determines this pilot's signal.
    mid = float(features.get('median_interval') or 0)
    valid_cycle = isfinite(mid) and mid > 0
    # SQL features are finite day intervals. Fail closed for invalid inputs too,
    # rather than letting the legacy evaluator ceil(NaN/Infinity).
    scoring_features = features if valid_cycle else {**features, 'median_interval': None}
    result = inactivity.evaluate(scoring_features, as_of, freshness=freshness, coverage=coverage)
    count = int(features.get('distinct_purchase_days') or 0)
    gaps = int(features.get('gap_count') or 0)
    last = features.get('last_purchase_date')
    reason = ('NO_VALID_PURCHASE_DAYS' if not last or count == 0 else
              'INSUFFICIENT_HISTORY' if count < POLICY['min_days'] or gaps < POLICY['min_gaps'] or not valid_cycle
              else 'ELIGIBLE')
    if freshness != 'HEALTHY':
        reason = 'SOURCE_UNRELIABLE'
    eligible = reason == 'ELIGIBLE'
    # Dates have whole-day precision: a 90-day cycle is overdue on day 91;
    # a 17.5-day cycle is overdue on day 18. Equality is not an overdue cycle.
    threshold = floor(mid) + 1 if valid_cycle else None
    active = eligible and (as_of-last).days >= threshold
    result.update(eligible=eligible, reason=reason, signal_type='CHURN_RISK' if active else None)
    components=result['metrics']['score_components']
    result['score'] = round(100*(.5*components['severity']+.3*components['value']+.2*components['loss']),2) if active else 0
    result['metrics'].update(parameters=POLICY, churn_threshold=threshold, overdue_threshold=None,
                             automation_allowed=False)
    return result
