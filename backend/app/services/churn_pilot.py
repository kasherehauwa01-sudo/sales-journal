"""Versioned Aviatorov-only pilot; never grants automation permission."""
from app.services import inactivity

GENERATOR_VERSION = 'inactivity/aviator-churn-pilot/1'
PARAMETERS_VERSION = 2
POLICY = dict(policy='aviator-churn-pilot/1', churn_days=45, min_days=3,
              cycle_days=365, reference_days=180, phone_limit=10,
              eligibility='positive-distinct-store-days', cadence_limits=False,
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
    # Retain descriptive statistics/quality score without letting old cadence
    # eligibility or individual thresholds override the approved fixed rule.
    result = inactivity.evaluate(features, as_of, freshness=freshness, coverage=coverage)
    count = int(features.get('distinct_purchase_days') or 0)
    last = features.get('last_purchase_date')
    reason = ('NO_VALID_PURCHASE_DAYS' if not last or count == 0 else
              'INSUFFICIENT_HISTORY' if count < 3 else 'ELIGIBLE')
    if freshness != 'HEALTHY':
        reason = 'SOURCE_UNRELIABLE'
    eligible = reason == 'ELIGIBLE'
    active = eligible and (as_of-last).days >= 45
    result.update(eligible=eligible, reason=reason, signal_type='CHURN_RISK' if active else None)
    components=result['metrics']['score_components']
    result['score'] = round(100*(.5*components['severity']+.3*components['value']+.2*components['loss']),2) if active else 0
    result['metrics'].update(parameters=POLICY, churn_threshold=45, overdue_threshold=None,
                             automation_allowed=False)
    return result
