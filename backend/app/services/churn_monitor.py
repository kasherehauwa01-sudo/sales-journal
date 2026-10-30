"""Read model for the existing churn pipeline. No recalculation, release or writes."""
from datetime import datetime, timedelta, timezone
from math import isfinite
from zoneinfo import ZoneInfo
from sqlalchemy import select, func
from app.config import settings
from app.models import (AnalyticsSignalRun as Run, AnalyticsSignalState as State,
    AnalyticsAutomationPolicy as Policy, AnalyticsAutomationIdentity as Identity,
    AnalyticsAutomationRelease as Release, AnalyticsAutomationEvent as Event,
    AnalyticsAutomationDecision as Decision, SeasonalityState, ImportBatch)
from app.services.churn_pilot import ALGORITHM_POLICY, GENERATOR_VERSION
from app.services.churn_telemetry import load_telemetry
from app.services.churn_diagnostics import diagnostic


async def read_model(db):
    """Project only needed columns; never hydrate source facts or signed manifests."""
    policy = (await db.execute(select(Policy.enabled, Policy.approved, Policy.revision,
        Policy.settings_json['algorithm_policy'].as_string().label('algorithm_policy'),
        Policy.settings_json['min_confidence'].as_float().label('min_confidence'))
        .where(Policy.id == 1))).mappings().first()
    control = (await db.execute(select(State.published_run_id, State.active_run_id, State.enabled)
                               .where(State.id == 1))).mappings().first()
    run_columns = (Run.id, Run.status, Run.as_of, Run.started_at, Run.finished_at,
        Run.source_revision, Run.counters, Run.error_code,
        Run.metadata_json['generator_version'].as_string().label('generator_version'))
    runs = list((await db.execute(select(*run_columns).order_by(Run.started_at.desc(), Run.id.desc()).limit(20))).mappings().all())
    if control and control['published_run_id'] and not any(r['id'] == control['published_run_id'] for r in runs):
        published = (await db.execute(select(*run_columns).where(Run.id == control['published_run_id']))).mappings().first()
        if published:
            runs.append(published)
    releases = (await db.execute(select(Release.id, Release.source_run_id, Release.sequence, Release.created_at,
        Release.metadata_json['algorithm_policy'].as_string().label('algorithm_policy'),
        Release.metadata_json['run']['mode'].as_string().label('mode'),
        Release.metadata_json['policy_revision'].as_integer().label('policy_revision'),
        Release.metadata_json['run']['source_decision']['valid_until'].as_string().label('valid_until'))
        .order_by(Release.sequence.desc()).limit(30))).mappings().all()
    event_counts = dict((await db.execute(select(Event.release_id, func.count()).where(
        Event.release_id.in_([r['id'] for r in releases])).group_by(Event.release_id))).all()) if releases else {}
    identity_stage = Identity.evidence['stage'].as_string()
    identities = (await db.execute(select(Identity.status,
        identity_stage.label('stage'), func.count().label('count'))
        .group_by(Identity.status, identity_stage))).mappings().all()
    reviews = (await db.execute(select(Identity.client_id, Identity.manager_id, Identity.status, Identity.revision,
        Identity.evidence['stage'].as_string().label('stage')).where(Identity.status != 'VERIFIED')
        .order_by(Identity.client_id, Identity.subject_key).limit(50))).mappings().all()
    decisions = (await db.execute(select(Decision.id, Decision.source_run_id, Decision.created_at,
        Decision.data['status'].as_string().label('status'), Decision.data['reasons'].label('reasons'),
        Decision.data['ready'].as_integer().label('ready'), Decision.data['candidates'].as_integer().label('candidates'))
        .where(Decision.source_run_id != 'POLICY', Decision.source_run_id != 'IDENTITY')
        .order_by(Decision.created_at.desc()).limit(20))).mappings().all()
    identity_conflict = await db.scalar(select(Identity.client_id).where(Identity.status == 'VERIFIED')
        .group_by(Identity.client_id).having(func.count() > 1).limit(1))
    approved_at = await db.scalar(select(Decision.created_at).where(
        Decision.source_run_id == 'POLICY', Decision.data['action'].as_string() == 'approve',
        Decision.data['policy_revision'].as_integer() == (policy['revision'] if policy else -1))
        .order_by(Decision.created_at.desc()).limit(1))
    revision = await db.scalar(select(SeasonalityState.source_revision).where(SeasonalityState.id == 1))
    pending = await db.scalar(select(ImportBatch.id).where(ImportBatch.status.in_(['queued', 'processing'])).limit(1))
    return dict(policy=dict(policy) if policy else None, control=dict(control) if control else None,
                runs=[dict(r) for r in runs], releases=[dict(r) for r in releases],
                events=event_counts, identities=[dict(i) for i in identities],
                reviews=[dict(r) for r in reviews], decisions=[dict(d) for d in decisions],
                approved_at=approved_at, identity_conflict=identity_conflict is not None, source_revision=revision, import_pending=pending is not None)


def iso(value):
    if value is None:
        return None
    if isinstance(value, datetime) and value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.isoformat()


def budget_view(budget):
    if budget is None:
        return None
    result = budget.model_dump()
    result['remaining'] = max(budget.limit - budget.used, 0) if budget.limit is not None and budget.used is not None else None
    return result


def build_status(model, telemetry, telemetry_error, *, now, config):
    issues = []
    def add(code, at=None):
        item = diagnostic(code, iso(at or now))
        if item['code'] not in {d['code'] for d in issues}:
            issues.append(item)
    policy, control = model['policy'], model['control']
    runs, releases = model['runs'], model['releases']
    latest = runs[0] if runs else None
    current = next((r for r in runs if control and r['id'] == control['published_run_id']), None)
    minimum = policy.get('min_confidence') if policy else None
    valid_minimum = type(minimum) in (int, float) and isfinite(minimum) and 0 <= minimum <= 1
    if not policy or not policy['enabled'] or not policy['approved'] or not valid_minimum:
        add('POLICY_NOT_APPROVED')
    if not policy or policy['algorithm_policy'] != ALGORITHM_POLICY:
        add('ALGORITHM_POLICY_NOT_APPROVED')
    if not config['calltrack_token_configured']:
        add('AUTHORIZATION_FAILED')
    if latest and latest['status'] == 'FAILED':
        add('WORKER_FAILED', latest['finished_at'] or latest['started_at'])
    if not current or current['status'] != 'SUCCEEDED':
        add('RUN_NOT_SUCCEEDED')
    if latest and control and latest['id'] != control['published_run_id']:
        add('RUN_NOT_CURRENT_PUBLICATION')
    if model['import_pending'] or (current and current['source_revision'] != model['source_revision']):
        add('SOURCE_CHANGED_OR_PENDING')
    closed = now.astimezone(ZoneInfo('Europe/Moscow')).date() - timedelta(days=1)
    if current and (current['as_of'] != closed or current['generator_version'] != GENERATOR_VERSION):
        add('SOURCE_STALE')
    release = next((r for r in releases if control and r['source_run_id'] == control['published_run_id']
                    and r['algorithm_policy'] == ALGORITHM_POLICY and r.get('mode') == 'AUTOMATION_APPROVED' and policy
                    and r['policy_revision'] == policy['revision']), None)
    if release is None:
        add('NO_CURRENT_PUBLICATION')
    else:
        try:
            expiry = datetime.fromisoformat(release['valid_until'])
            if expiry.utcoffset() is None or expiry <= now:
                add('SOURCE_STALE', release['created_at'])
        except (TypeError, ValueError):
            add('SOURCE_STALE', release['created_at'])
    verified = sum(i['count'] for i in model['identities'] if i['status'] == 'VERIFIED')
    preliminary = sum(i['count'] for i in model['identities'] if i['status'] == 'PRELIMINARY_MATCH'
                      or (i['status'] == 'REVIEW' and i['stage'] == 'PRELIMINARY_MATCH'))
    unverified = sum(i['count'] for i in model['identities'] if i['status'] != 'VERIFIED')
    if unverified or not verified:
        add('IDENTITY_NOT_VERIFIED')
    if model.get('identity_conflict'):
        add('IDENTITY_CONFLICT')
    # Decisions remain historical evidence; a later success must not be masked by an old failure.
    recent_decision = next((d for d in model['decisions'] if current and d['source_run_id'] == current['id']), None)
    if recent_decision:
        for code in (recent_decision['reasons'] or [])[:20]:
            add(code, recent_decision['created_at'])
    receiver, systemd = None, None
    receipts = {}
    if telemetry_error:
        add(telemetry_error)
    if telemetry:
        systemd = telemetry.systemd.model_dump(mode='json')
        receiver = telemetry.receiver.model_dump(mode='json')
        receiver['lifetime'] = budget_view(telemetry.receiver.lifetime)
        receiver['daily'] = budget_view(telemetry.receiver.daily)
        receiver['manager_budgets'] = [budget_view(b) for b in telemetry.receiver.manager_budgets]
        for code in telemetry.codes:
            add(code, telemetry.collected_at)
        if telemetry.systemd.service_state == 'failed' or telemetry.systemd.service_result == 'failed':
            add('SYSTEMD_FAILED', telemetry.systemd.last_attempt)
        if not (telemetry.systemd.timer_enabled is True and telemetry.systemd.timer_active is True
                and telemetry.systemd.schedule == '09:00 Europe/Moscow' and telemetry.systemd.next_run
                and telemetry.systemd.next_run > now):
            add('SCHEDULE_UNCONFIRMED')
            systemd['next_run'] = None
        if telemetry.integrity == 'mismatch':
            add('REVIEWED_CODE_CHANGED', telemetry.collected_at)
        elif telemetry.integrity != 'match':
            add('INTEGRITY_UNCONFIRMED')
        if telemetry.receiver.status != 'available':
            add('RECEIVER_STATUS_MISSING' if telemetry.receiver.status == 'unknown' else 'AUTHORIZATION_FAILED' if telemetry.receiver.status == 'unauthorized' else 'CALLTRACK_UNAVAILABLE')
        else:
            receipts = {r.release_id: r for r in telemetry.receiver.receipts}
            if not telemetry.receiver.policy_approved or telemetry.receiver.churn_enabled is not True or telemetry.receiver.priority is None or telemetry.receiver.due_days is None or telemetry.receiver.min_confidence is None or GENERATOR_VERSION not in (telemetry.receiver.approved_generators or []):
                add('RECEIVER_POLICY_UNCONFIRMED')
            managers = telemetry.receiver.manager_budgets
            if {b.manager_id for b in managers} != {3, 6}:
                add('BUDGET_UNCONFIRMED')
            for budget in managers:
                if budget.limit is None or budget.used is None or budget.approved is not True:
                    add('BUDGET_UNCONFIRMED')
                elif budget.used >= budget.limit:
                    add('MANAGER_BUDGET_EXHAUSTED')
            for kind in ('lifetime', 'daily'):
                budget = getattr(telemetry.receiver, kind)
                if budget is None or budget.limit is None or budget.used is None or budget.approved is not True:
                    add('BUDGET_UNCONFIRMED')
                elif kind == 'daily' and (budget.business_date != now.astimezone(ZoneInfo('Europe/Moscow')).date().isoformat()
                                         or budget.ledger_used != budget.used):
                    add('BUDGET_UNCONFIRMED')
                    receiver['daily']['used'] = receiver['daily']['remaining'] = None
                elif budget.used >= budget.limit:
                    add('LIFETIME_BUDGET_EXHAUSTED' if kind == 'lifetime' else 'DAILY_BUDGET_EXHAUSTED')
    receipt = receipts.get(release['id']) if release else None
    if receipt and receipt.blocked:
        add('RECEIVER_SIGNALS_BLOCKED', telemetry.collected_at)
    if release and (receipt is None or receipt.accepted == 0):
        add('DELIVERY_UNCONFIRMED', release['created_at'])
    decisions_by_run = {}
    for d in model['decisions']:
        decisions_by_run.setdefault(d['source_run_id'], d)
    releases_by_run = {}
    for r in releases:
        if r['algorithm_policy'] == ALGORITHM_POLICY:
            releases_by_run.setdefault(r['source_run_id'], r)
    history = []
    for run in runs:
        r = releases_by_run.get(run['id'])
        ack = receipts.get(r['id']) if r else None
        counters = run['counters'] or {}
        decision = decisions_by_run.get(run['id'])
        codes = ([run['error_code']] if run['error_code'] else []) + ((decision['reasons'] or []) if decision else [])
        history.append(dict(id=run['id'], component='Sales Journal', date=iso(run['started_at']),
            result=run['status'], clients=counters.get('processed'), signals=counters.get('CHURN_RISK'),
            delivered=ack.accepted if ack else None, diagnostics=[diagnostic(c, iso(run['started_at'])) for c in codes[:20]],
            details=dict(as_of=iso(run['as_of']), generator=run['generator_version'], release_id=r['id'] if r else None,
                         published=model['events'].get(r['id'], 0) if r else None)))
    if telemetry:
        for item in telemetry.history:
            history.append(dict(id=item.id, component='Внешний пилот', date=iso(item.observed_at), result=item.result,
                clients=None, signals=None, delivered=None,
                diagnostics=[diagnostic(c, iso(item.observed_at)) for c in item.codes], details={}))
    history.sort(key=lambda h: h['date'] or '', reverse=True)
    disabled = not config['automation_enabled'] or bool(policy and not policy['enabled'])
    state = 'DISABLED' if disabled else 'ERROR' if any(d['severity'] == 'error' for d in issues) else 'NEEDS_SETUP' if issues else 'ENABLED'
    successful = next((r for r in runs if r['status'] == 'SUCCEEDED'), None)
    counters = (current or latest or {}).get('counters') or {}
    return dict(api_version=1, read_only=True, status=state, observed_at=iso(now),
        configuration={**config, 'mode': config.get('mode', 'SHADOW'), 'delivery_mode': release.get('mode') if release else None, 'active_algorithm': ALGORITHM_POLICY, 'generator_version': GENERATOR_VERSION},
        policy={**(policy or {}), 'approved_at': iso(model['approved_at'])},
        calculations=dict(last_attempt=iso(latest['started_at']) if latest else None,
                          last_success=iso(successful['finished_at']) if successful else None,
                          active_run_id=control['active_run_id'] if control else None),
        statistics=dict(scope='Последний опубликованный расчёт / последняя сохранённая проверка допуска / связанная публикация',
            decision_at=iso(recent_decision['created_at']) if recent_decision else None,
            analyzed_clients=counters.get('processed'), risk_clients=counters.get('CHURN_RISK'),
            ready_signals=recent_decision['ready'] if recent_decision else None,
            published_signals=model['events'].get(release['id'], 0) if release else None,
            accepted_signals=receipt.accepted if receipt else None,
            blocked_signals=(max(recent_decision['candidates'] - recent_decision['ready'], 0)
                if recent_decision and recent_decision['candidates'] is not None and recent_decision['ready'] is not None else None),
            receiver_blocked_signals=receipt.blocked if receipt else None, tasks_created=receipt.tasks if receipt else None,
            verified_bindings=verified, preliminary_bindings=preliminary, pending_bindings=unverified),
        publication=dict(id=release['id'], created_at=iso(release['created_at'])) if release else None,
        receiver=receiver, systemd=systemd, integrity=telemetry.integrity if telemetry else None,
        runtime_observed_at=iso(telemetry.collected_at) if telemetry else None,
        diagnostics=issues, history=history[:30],
        identity_reviews=[dict(client_id=r['client_id'], manager_id=r['manager_id'], status=r['status'],
            revision=r['revision'], reason=('Неоднозначное сопоставление: требуется проверка ID и полной истории.' if r['status']=='AMBIGUOUS' else 'Предварительная привязка не является VERIFIED в producer main; требуется существующая процедура проверки.' if r.get('stage')=='PRELIMINARY_MATCH' or r['status']=='PRELIMINARY_MATCH' else 'Ожидает проверки ID, полной истории, уникальности и менеджера.')) for r in model['reviews']])


async def status(db, now=None):
    now = now or datetime.now(timezone.utc)
    model = await read_model(db)
    telemetry, error = load_telemetry(settings.churn_monitor_status_file, now)
    return build_status(model, telemetry, error, now=now, config=dict(
        automation_enabled=settings.analytics_automation_enabled,
        shadow_worker_enabled=settings.analytics_signals_enabled, mode=settings.analytics_signals_mode,
        calltrack_token_configured=bool(settings.calltrack_integration_token)))
