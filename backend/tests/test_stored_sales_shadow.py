"""Autonomous tests: no engines, network, schemas or PostgreSQL connections.

Async DB boundaries are mocks; SQL/locking behaviour is separately covered by
explicit opt-in PostgreSQL tests, never inferred from these mock assertions.
"""
import asyncio
from contextlib import asynccontextmanager
from copy import deepcopy
from dataclasses import replace
from datetime import date, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from app.models import AnalyticsSignalRun, AnalyticsSignalState, AnalyticsSignalChange
from app.services import analytics_signal_worker as worker
from app.services import analytics_source as source
from app.services import analytics_signal_store as store
from app.services.analytics_scope import load_analytics_scope
from app.services.clients_vr import ClientsVrError
from app.services.analytics_delivery import publish_delivery
from test_inactivity_shadow import features

DAY = date(2026, 10, 19)
SCOPE = dict(department='Авиаторов', buyer_types=['Оптовик', 'Корпоративный'],
    client_keys=['alpha'], policy='aviatorov-wholesale-corporate/1', buyer_type_source='ClientsVR')


@pytest.fixture(autouse=True)
def no_connections(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError('Autonomous test attempted a DB connection')
    monkeypatch.setattr(AsyncEngine, 'connect', forbidden)
    monkeypatch.setattr('httpx.AsyncClient.send', forbidden)
    monkeypatch.setattr('socket.create_connection', forbidden)
    monkeypatch.setattr('socket.socket.connect', forbidden)


def decision(**overrides):
    args = dict(last_closed_business_day=DAY, source_revision=7, timezone='Europe/Moscow',
                has_sales=True, upper_sale_id=100)
    args.update(overrides)
    return source.resolve_stored_sales_source(DAY, **args)


def active(**overrides):
    d = decision()
    args = dict(id='active', status='RUNNING', phase='scan', as_of=DAY, source_revision=7,
        upper_sale_id=100, sale_cursor=5, client_cursor='', error_count=0, counters={},
        metadata_json=dict(source_decision=d.to_json(), scope=deepcopy(SCOPE),
            mode="SHADOW", automation_allowed=False, coverage_status="UNKNOWN",
            freshness=dict(status="UNKNOWN"), complete_through=None, parameters=dict(worker.POLICY),
            generator_version=worker.GENERATOR_VERSION, parameters_version=worker.PARAMETERS_VERSION,
            normalization_version=worker.ANALYTICAL_NORMALIZATION_VERSION))
    args.update(overrides)
    return AnalyticsSignalRun(**args)


def test_stored_sales_shadow_policy():
    d = decision()
    assert d.policy_version == 'stored-sales-shadow/1'
    assert d.shadow_allowed and not d.automation_allowed
    assert d.coverage_status == d.business_freshness == 'UNKNOWN'
    assert d.coverage_method == 'STORED_SALES'
    assert d.accepted_data_through is d.declared_data_through is d.observed_data_through is None
    assert d.evidence == () and d.effective_as_of == DAY


def test_stored_sales_shadow_accepts_closed_date_without_completeness_claim():
    assert decision().shadow_allowed
    future = source.resolve_stored_sales_source(DAY + timedelta(days=10),
        last_closed_business_day=DAY, source_revision=7, timezone='Europe/Moscow',
        has_sales=True, upper_sale_id=100)
    assert future.effective_as_of == DAY
    assert 'REQUESTED_DATE_NOT_CLOSED' in future.reason_codes
    assert future.coverage_status == future.business_freshness == 'UNKNOWN'
    historical = source.resolve_stored_sales_source(DAY - timedelta(days=10),
        last_closed_business_day=DAY, source_revision=7, timezone='Europe/Moscow',
        has_sales=True, upper_sale_id=100)
    assert historical.effective_as_of == DAY - timedelta(days=10)


@pytest.mark.parametrize('change', [dict(has_sales=False), dict(upper_sale_id=0), dict(imports_pending=True)])
def test_missing_or_in_progress_source_is_blocked(change):
    d = decision(**change)
    assert not d.shadow_allowed and d.effective_as_of is None and not d.automation_allowed


def test_fingerprint_tracks_revision_and_watermark_but_not_clock():
    d = decision()
    assert decision(source_revision=8).evidence_fingerprint != d.evidence_fingerprint
    assert decision(upper_sale_id=101).evidence_fingerprint != d.evidence_fingerprint
    assert decision(last_closed_business_day=DAY+timedelta(days=1)).evidence_fingerprint == d.evidence_fingerprint
    assert worker.resume_blocker(active(), decision(last_closed_business_day=DAY+timedelta(days=1)), SCOPE) is None


@pytest.mark.parametrize('change,scope,reason', [
    (dict(source_revision=8), SCOPE, 'SOURCE_EVIDENCE_CHANGED'),
    (dict(upper_sale_id=101), SCOPE, 'SOURCE_EVIDENCE_CHANGED'),
    (dict(imports_pending=True), SCOPE, 'SOURCE_DECISION_BLOCKED'),
    ({}, SCOPE | dict(client_keys=['beta']), 'ANALYTIC_SCOPE_CHANGED'),
])
def test_resume_guards(change, scope, reason):
    assert worker.resume_blocker(active(), decision(**change), scope) == reason


@pytest.mark.parametrize('pending,available,present,keys,reason', [
    (None, True, 1, ['alpha'], None),
    (2, True, 1, ['alpha'], 'IMPORT_PENDING'),
    (None, False, 1, ['alpha'], 'BUYER_TYPE_SOURCE_UNAVAILABLE'),
    (None, True, None, ['alpha'], 'NO_SCOPED_STORED_SALES'),
    (None, True, None, [], 'BUYER_TYPE_SCOPE_EMPTY'),
])
def test_actual_source_context_filters_date_and_business_scope(monkeypatch, pending, available, present, keys, reason):
    monkeypatch.setattr(worker, 'last_closed_day', lambda: DAY)
    scope = SCOPE | dict(client_keys=keys)
    loader = AsyncMock(return_value=scope) if available else AsyncMock(side_effect=ClientsVrError('offline'))
    monkeypatch.setattr(worker, 'load_analytics_scope', loader)
    values = [100, 7, pending]
    if pending is None and available:
        values.append(present)
    db = SimpleNamespace(scalar=AsyncMock(side_effect=values))
    d, actual = asyncio.run(worker.stored_source_context(db, DAY+timedelta(days=10)))
    assert d.automation_allowed is False
    if reason:
        assert not d.shadow_allowed and reason in d.reason_codes
    else:
        assert d.shadow_allowed and actual == scope
        query = db.scalar.call_args_list[-1].args[0]
        params = query.compile().params
        assert DAY in params.values() and 'Авиаторов' in params.values() and ['alpha'] in params.values()
        assert 'sale_date <=' in str(query) and 'price_type' not in str(query)
    if pending is not None:
        loader.assert_not_awaited()


def test_actual_scope_never_substitutes_price_types(monkeypatch):
    loader = AsyncMock(return_value={'Оптовик':[' ALPHA\u00a0 X '], 'Корпоративный':['Beta']})
    monkeypatch.setattr('app.services.analytics_scope.get_analytics_buyer_type_names', loader)
    scope = asyncio.run(load_analytics_scope())
    loader.assert_awaited_once_with(('Оптовик', 'Корпоративный'))
    assert scope['client_keys'] == ['alpha x', 'beta'] and scope['department'] == 'Авиаторов'


def harness(monkeypatch, run, current=None, scope=None):
    control = AnalyticsSignalState(id=1, enabled=True, active_run_id=run.id, published_run_id='published')
    db = SimpleNamespace(execute=AsyncMock(), get=AsyncMock(return_value=run), commit=AsyncMock())
    @asynccontextmanager
    async def factory():
        yield db
    @asynccontextmanager
    async def lock(*args, **kwargs):
        yield None
    monkeypatch.setattr(worker, 'advisory', lock)
    monkeypatch.setattr(worker, 'state', AsyncMock(return_value=control))
    monkeypatch.setattr(worker, 'ensure_headroom', Mock())
    monkeypatch.setattr(worker, 'verify', AsyncMock())
    context = AsyncMock(return_value=(current or decision(), scope or deepcopy(SCOPE)))
    monkeypatch.setattr(worker, 'stored_source_context', context)
    scan, evaluate, publish = AsyncMock(), AsyncMock(), AsyncMock()
    monkeypatch.setattr(worker, 'scan_batch', scan)
    monkeypatch.setattr(worker, 'evaluate_batch', evaluate)
    monkeypatch.setattr('app.services.analytics_delivery.publish_delivery', publish)
    engine = SimpleNamespace(dialect=SimpleNamespace(name='postgresql'))
    def batch(as_of=DAY):
        return asyncio.run(worker.run_batch(factory, engine, as_of=as_of, configuration_enabled=True))
    return control, db, context, scan, evaluate, publish, batch


@pytest.mark.parametrize('status', ['RUNNING', 'PAUSED', 'FAILED'])
def test_old_policy_checkpoint_is_retired_before_any_processing(monkeypatch, status):
    run = active(status=status)
    run.metadata_json['source_decision']['policy_version'] = source.POLICY_VERSION
    frozen = deepcopy(run.metadata_json)
    control, db, context, scan, evaluate, publish, batch = harness(monkeypatch, run)
    result = batch()
    assert result['status'] == 'SUPERSEDED' and run.error_code == 'SOURCE_POLICY_CHANGED'
    assert control.active_run_id is None and control.published_run_id == 'published'
    assert run.sale_cursor == 5 and run.metadata_json == frozen
    context.assert_not_awaited(); scan.assert_not_awaited(); evaluate.assert_not_awaited(); publish.assert_not_awaited()
    db.commit.assert_awaited_once()


@pytest.mark.parametrize('phase', ['scan', 'evaluate'])
def test_revision_change_prevents_both_phases_and_publication(monkeypatch, phase):
    run = active(phase=phase)
    control, db, context, scan, evaluate, publish, batch = harness(monkeypatch, run, decision(source_revision=8))
    assert batch()['status'] == 'SUPERSEDED'
    scan.assert_not_awaited(); evaluate.assert_not_awaited(); publish.assert_not_awaited()
    assert control.published_run_id == 'published'


@pytest.mark.parametrize('status,phase', [('PAUSED', 'scan'), ('FAILED', 'evaluate')])
def test_compatible_checkpoint_resumes_only_its_phase(monkeypatch, status, phase):
    run = active(status=status, phase=phase)
    control, db, context, scan, evaluate, publish, batch = harness(monkeypatch, run)
    assert batch()['status'] == 'RUNNING'
    (scan if phase == 'scan' else evaluate).assert_awaited_once()
    (evaluate if phase == 'scan' else scan).assert_not_awaited()
    publish.assert_not_awaited()
    assert control.published_run_id == 'published'


def test_frozen_cutoff_cannot_move_when_clock_advances():
    run = active()
    changed = replace(decision(), effective_as_of=DAY+timedelta(days=1))
    assert worker.resume_blocker(run, changed, SCOPE) == 'SOURCE_CUTOFF_CHANGED'


def test_published_old_policy_not_reused_and_backwards_date_blocked():
    async def scenario():
        previous = active(id='published', status='SUCCEEDED')
        previous.metadata_json['source_decision']['policy_version'] = source.POLICY_VERSION
        control = AnalyticsSignalState(id=1, enabled=True, published_run_id=previous.id)
        db = SimpleNamespace(get=AsyncMock(return_value=previous), scalar=AsyncMock(return_value=100),
                             add=Mock(), flush=AsyncMock())
        new = await worker.begin_run(db, control, decision(), deepcopy(SCOPE))
        assert new.id != previous.id and new.status == 'RUNNING'
        assert previous.status == 'SUCCEEDED' and control.published_run_id == previous.id
        earlier = replace(decision(), effective_as_of=DAY-timedelta(days=1))
        blocked = await worker.begin_run(db, control, earlier, deepcopy(SCOPE))
        assert blocked.status == 'BLOCKED'
        assert 'EFFECTIVE_DATE_PRECEDES_PUBLISHED_RUN' in blocked.metadata_json['source_decision']['reason_codes']
    asyncio.run(scenario())


def test_reconciliation_keeps_unknown_evidence_and_repeat_has_no_change_event():
    async def scenario():
        run = active()
        run.metadata_json.update(coverage_status='UNKNOWN', freshness=dict(status='UNKNOWN'))
        objects = []
        class Session:
            async def scalars(self, query):
                # Real reconciliation executes its SELECT; emulate its result only.
                signals = [o for o in objects if o.__class__.__name__ == 'AnalyticsSignal']
                return SimpleNamespace(all=lambda: signals)
            def add(self, obj): objects.append(obj)
            async def flush(self): pass
        db = Session()
        f = features(last_purchase_date=DAY-timedelta(days=20))
        await store.reconcile_batch(db, run, [f])
        signal = next(o for o in objects if o.__class__.__name__ == 'AnalyticsSignal')
        assert signal.metrics['freshness'] == signal.metrics['coverage'] == 'UNKNOWN'
        assert signal.source_freshness_status == signal.source_coverage_status == 'UNKNOWN'
        assert signal.metrics['confidence_components']['coverage'] == 0
        assert signal.metrics['automation_allowed'] is False and signal.mode == 'SHADOW'
        assert len([o for o in objects if isinstance(o, AnalyticsSignalChange)]) == 1
        # A committed evaluation checkpoint is not evaluated again by the worker;
        # even reconciling the same evidence emits no extra signal change.
        await store.reconcile_batch(db, run, [f])
        assert len([o for o in objects if isinstance(o, AnalyticsSignalChange)]) == 1
    asyncio.run(scenario())


def test_publish_delivery_is_local_and_idempotent():
    async def scenario():
        head = SimpleNamespace(sequence=4)
        db = SimpleNamespace(scalar=AsyncMock(side_effect=[head, None]), add=Mock(), flush=AsyncMock())
        assert await publish_delivery(db, active(status='SUCCEEDED')) == 5
        publication = db.add.call_args.args[0]
        assert publication.sequence == 5 and publication.run_id == 'active'
        db.scalar = AsyncMock(side_effect=[head, publication])
        assert await publish_delivery(db, active(status='SUCCEEDED')) == 5
        assert db.add.call_count == 1
    asyncio.run(scenario())


def test_real_reconciliation_suspend_resolve_and_new_episode():
    async def scenario():
        from app.models import AnalyticsSignal
        run = active()
        run.metadata_json.update(coverage_status='UNKNOWN', freshness=dict(status='UNKNOWN'))
        objects = []
        class Session:
            async def scalars(self, query):
                signals = sorted([o for o in objects if isinstance(o, AnalyticsSignal)],
                                 key=lambda s: -s.episode_no)
                return SimpleNamespace(all=lambda: signals)
            def add(self, obj): objects.append(obj)
            async def flush(self): pass
        db = Session()
        original = features(last_purchase_date=DAY-timedelta(days=20))
        await store.reconcile_batch(db, run, [original])
        signal = next(o for o in objects if isinstance(o, AnalyticsSignal))
        await store.reconcile_batch(db, run, [original | dict(distinct_purchase_days=2)])
        assert signal.status == 'SUSPENDED'
        await store.reconcile_batch(db, run, [original])
        assert signal.status == 'ACTIVE'
        returned = original | dict(last_purchase_date=DAY)
        await store.reconcile_batch(db, run, [returned])
        assert signal.status == 'RESOLVED' and signal.resolution_reason == 'PURCHASE_OCCURRED'
        run.as_of += timedelta(days=20)
        run.metadata_json['source_decision']['effective_as_of'] = run.as_of.isoformat()
        await store.reconcile_batch(db, run, [returned])
        signals = [o for o in objects if isinstance(o, AnalyticsSignal)]
        assert len(signals) == 2 and signals[-1].episode_no == 2
        events = [o.event_type for o in objects if isinstance(o, AnalyticsSignalChange)]
        assert events == ['CREATED', 'SUSPENDED', 'RESUMED', 'RESOLVED', 'CREATED']
    asyncio.run(scenario())


@pytest.mark.parametrize('implicit', [False, True])
@pytest.mark.parametrize('status', ['RUNNING', 'PAUSED', 'FAILED'])
def test_next_day_resume_keeps_original_request_and_snapshot(monkeypatch, implicit, status):
    run = active(status=status)
    frozen = deepcopy(run.metadata_json)
    tomorrow = decision(last_closed_business_day=DAY+timedelta(days=1))
    control, db, context, scan, evaluate, publish, batch = harness(monkeypatch, run, tomorrow)
    monkeypatch.setattr(worker, 'last_closed_day', lambda: DAY+timedelta(days=1))
    result = batch(as_of=None if implicit else DAY)
    assert result['status'] == 'RUNNING'
    assert context.call_args.args[1] == DAY
    assert run.as_of == DAY and run.sale_cursor == 5 and run.metadata_json == frozen
    scan.assert_awaited_once(); evaluate.assert_not_awaited(); publish.assert_not_awaited()
    assert control.published_run_id == 'published'


def test_different_explicit_request_is_rejected_without_writes(monkeypatch):
    run = active()
    control, db, context, scan, evaluate, publish, batch = harness(monkeypatch, run)
    with pytest.raises(ValueError, match='original requested_as_of'):
        batch(as_of=DAY+timedelta(days=1))
    context.assert_not_awaited(); scan.assert_not_awaited(); evaluate.assert_not_awaited()
    publish.assert_not_awaited(); db.commit.assert_not_awaited()
    assert control.active_run_id == run.id and control.published_run_id == 'published'


@pytest.mark.parametrize('phase', ['scan', 'evaluate'])
@pytest.mark.parametrize('keys', [['alpha', 'beta'], ['beta'], []])
def test_changed_directory_during_resume_supersedes_without_publishing(monkeypatch, phase, keys):
    run = active(phase=phase)
    frozen = deepcopy(run.metadata_json)
    current_scope = SCOPE | dict(client_keys=keys)
    control, db, context, scan, evaluate, publish, batch = harness(monkeypatch, run, scope=current_scope)
    # Use the actual source resolver/context as well as the real run_batch.
    monkeypatch.setattr(worker, 'stored_source_context', stored_context_function)
    monkeypatch.setattr(worker, 'last_closed_day', lambda: DAY)
    monkeypatch.setattr(worker, 'load_analytics_scope', AsyncMock(return_value=current_scope))
    db.scalar = AsyncMock(side_effect=[100, 7, None, 1 if keys else None])
    result = batch()
    assert result['status'] == 'SUPERSEDED'
    assert run.error_code == ('ANALYTIC_SCOPE_CHANGED' if keys else 'SOURCE_DECISION_BLOCKED')
    assert run.metadata_json == frozen and run.sale_cursor == 5 and run.client_cursor == ''
    assert control.active_run_id is None and control.published_run_id == 'published'
    scan.assert_not_awaited(); evaluate.assert_not_awaited(); publish.assert_not_awaited()
    db.commit.assert_awaited_once()


# Retain the real callable before harness monkeypatches the module attribute.
stored_context_function = worker.stored_source_context


@pytest.mark.parametrize('phase', ['scan', 'evaluate'])
def test_clientsvr_unavailable_during_resume_preserves_published_run(monkeypatch, phase):
    run = active(phase=phase)
    frozen = deepcopy(run.metadata_json)
    control, db, context, scan, evaluate, publish, batch = harness(monkeypatch, run)
    monkeypatch.setattr(worker, 'stored_source_context', stored_context_function)
    monkeypatch.setattr(worker, 'last_closed_day', lambda: DAY)
    monkeypatch.setattr(worker, 'load_analytics_scope', AsyncMock(side_effect=ClientsVrError('offline')))
    db.scalar = AsyncMock(side_effect=[100, 7, None])
    assert batch()['status'] == 'SUPERSEDED'
    assert run.error_code == 'SOURCE_DECISION_BLOCKED' and run.metadata_json == frozen
    assert control.active_run_id is None and control.published_run_id == 'published'
    scan.assert_not_awaited(); evaluate.assert_not_awaited(); publish.assert_not_awaited()
    # Only timeout statements and snapshot metadata queries; no sales DML.
    assert all(str(call.args[0]).startswith('SET LOCAL') for call in db.execute.call_args_list)


def test_completed_compatible_run_is_reused_but_old_policy_is_not():
    async def scenario():
        current = decision()
        previous = active(id='published', status='SUCCEEDED')
        control = AnalyticsSignalState(id=1, enabled=True, published_run_id=previous.id)
        db = SimpleNamespace(get=AsyncMock(return_value=previous), scalar=AsyncMock(return_value=100),
                             add=Mock(), flush=AsyncMock())
        reused = await worker.begin_run(db, control, current, deepcopy(SCOPE))
        assert reused is previous
        db.add.assert_not_called(); db.flush.assert_not_awaited()
        previous.metadata_json['source_decision']['policy_version'] = source.POLICY_VERSION
        frozen = deepcopy(previous.metadata_json)
        fresh = await worker.begin_run(db, control, current, deepcopy(SCOPE))
        assert fresh.id != previous.id and fresh.status == 'RUNNING'
        assert previous.metadata_json == frozen and control.published_run_id == previous.id
        assert control.active_run_id == fresh.id
    asyncio.run(scenario())


def test_shadow_success_only_publishes_local_metadata(monkeypatch):
    run = active(phase='evaluate')
    control, db, context, scan, evaluate, publish, batch = harness(monkeypatch, run)
    async def complete(db, run, size):
        run.phase, run.status = 'complete', 'SUCCEEDED'
    evaluate.side_effect = complete
    assert batch()['status'] == 'SUCCEEDED'
    publish.assert_awaited_once_with(db, run)
    assert control.active_run_id is None and control.published_run_id == run.id
    # The publication helper itself is tested above under a network denial guard.
    assert all(str(call.args[0]).startswith('SET LOCAL') for call in db.execute.call_args_list)


@pytest.mark.parametrize('implicit', [False, True])
def test_next_day_resume_of_originally_clamped_request_keeps_effective_date(monkeypatch, implicit):
    requested = DAY+timedelta(days=2)
    def at(closed):
        return source.resolve_stored_sales_source(requested,
            last_closed_business_day=closed, source_revision=7, timezone='Europe/Moscow',
            has_sales=True, upper_sale_id=100)
    run = active()
    run.metadata_json['source_decision'] = at(DAY).to_json()
    frozen = deepcopy(run.metadata_json)
    control, db, context, scan, evaluate, publish, batch = harness(monkeypatch, run, at(DAY+timedelta(days=1)))
    monkeypatch.setattr(worker, 'last_closed_day', lambda: DAY+timedelta(days=1))
    assert batch(as_of=None if implicit else requested)['status'] == 'RUNNING'
    assert context.call_args.args[1] == requested
    assert run.as_of == DAY and run.metadata_json == frozen
    scan.assert_awaited_once(); publish.assert_not_awaited()


def test_resume_cannot_use_future_corrupted_frozen_cutoff():
    run = active(as_of=DAY+timedelta(days=1))
    assert worker.resume_blocker(run, decision(), SCOPE) == 'SOURCE_CUTOFF_CHANGED'


@pytest.mark.parametrize('version', [None, 2])
def test_published_normalization_mismatch_is_not_reused(version):
    async def scenario():
        previous = active(id='published', status='SUCCEEDED')
        previous.metadata_json['normalization_version'] = version
        control = AnalyticsSignalState(id=1, enabled=True, published_run_id=previous.id)
        db = SimpleNamespace(get=AsyncMock(return_value=previous), scalar=AsyncMock(return_value=100),
                             add=Mock(), flush=AsyncMock())
        fresh = await worker.begin_run(db, control, decision(), deepcopy(SCOPE))
        assert fresh.id != previous.id and control.published_run_id == previous.id
    asyncio.run(scenario())


@pytest.mark.parametrize('kind', ['parameters', 'phase', 'mode', 'automation', 'coverage', 'freshness', 'version'])
def test_incompatible_checkpoint_is_superseded_before_processing(monkeypatch, kind):
    run = active()
    if kind == 'parameters':
        run.metadata_json['parameters']['min_days'] += 1
        expected = 'ALGORITHM_POLICY_CHANGED'
    elif kind == 'version':
        run.metadata_json['parameters_version'] += 1
        expected = 'ALGORITHM_POLICY_CHANGED'
    elif kind == 'phase':
        run.phase = 'unexpected'
        expected = 'INVALID_CHECKPOINT_PHASE'
    else:
        expected = 'SHADOW_CONTRACT_CHANGED'
        if kind == 'mode': run.metadata_json['mode'] = 'ENABLED'
        if kind == 'automation': run.metadata_json['automation_allowed'] = True
        if kind == 'coverage': run.metadata_json['coverage_status'] = 'CONFIRMED'
        if kind == 'freshness': run.metadata_json['freshness']['status'] = 'CURRENT'
    control, db, context, scan, evaluate, publish, batch = harness(monkeypatch, run)
    assert batch()['status'] == 'SUPERSEDED'
    assert run.error_code == expected and control.published_run_id == 'published'
    context.assert_not_awaited(); scan.assert_not_awaited(); evaluate.assert_not_awaited()
    publish.assert_not_awaited()


def test_changed_parameters_without_version_bump_cannot_reuse_published_run():
    async def scenario():
        run = active(status='SUCCEEDED')
        run.metadata_json['parameters']['min_days'] += 1
        control = AnalyticsSignalState(id=1, enabled=True, published_run_id=run.id)
        db = SimpleNamespace(get=AsyncMock(return_value=run), scalar=AsyncMock(return_value=100),
                             add=Mock(), flush=AsyncMock())
        result = await worker.begin_run(db, control, decision(), deepcopy(SCOPE))
        assert result is not run and result.metadata_json['parameters'] == worker.POLICY
        assert control.published_run_id == run.id
    asyncio.run(scenario())


@pytest.mark.parametrize('kind', ['RUNNING', 'FAILED', 'SUPERSEDED', 'parameters', 'coverage', 'automation'])
def test_publication_rejects_invalid_snapshot_before_any_database_write(kind):
    async def scenario():
        run = active(status='SUCCEEDED')
        if kind in {'RUNNING', 'FAILED', 'SUPERSEDED'}: run.status = kind
        if kind == 'parameters': run.metadata_json['parameters']['min_days'] += 1
        if kind == 'coverage': run.metadata_json['source_decision']['coverage_status'] = 'CONFIRMED'
        if kind == 'automation': run.metadata_json['source_decision']['automation_allowed'] = True
        db = SimpleNamespace(scalar=AsyncMock(), add=Mock(), flush=AsyncMock())
        with pytest.raises(ValueError, match='Only completed compatible'):
            await publish_delivery(db, run)
        db.scalar.assert_not_awaited(); db.add.assert_not_called(); db.flush.assert_not_awaited()
    asyncio.run(scenario())


@pytest.mark.parametrize('case', ['replaced', 'advanced', 'PAUSED', 'SUPERSEDED', 'SUCCEEDED', 'disabled', 'valid'])
def test_failure_recording_cannot_modify_replacement_or_advanced_checkpoint(case):
    from datetime import datetime, timezone
    now = datetime(2026, 10, 21, tzinfo=timezone.utc)
    run = active(heartbeat_at=now)
    control = AnalyticsSignalState(id=1, enabled=True, active_run_id=run.id, published_run_id='published')
    if case == 'replaced': control.active_run_id = 'new-run'
    if case == 'advanced': run.heartbeat_at += timedelta(seconds=1)
    if case in {'PAUSED', 'SUPERSEDED', 'SUCCEEDED'}: run.status = case
    if case == 'disabled': control.enabled = False
    original_status, original_error = run.status, run.error_code
    db = SimpleNamespace(get=AsyncMock(side_effect=[control, run]), commit=AsyncMock())
    @asynccontextmanager
    async def factory(): yield db
    result = asyncio.run(worker.record_failure(factory, 'Injected', run_id='active', heartbeat_at=now))
    if case == 'valid':
        assert result is True and run.status == 'FAILED' and run.error_count == 1
        db.commit.assert_awaited_once()
    else:
        assert result is False and run.status == original_status and run.error_code == original_error
        db.commit.assert_not_awaited()
    assert control.published_run_id == 'published'


def test_failure_without_checkpoint_never_opens_a_session():
    factory = Mock(side_effect=AssertionError('No checkpoint must not open DB'))
    assert asyncio.run(worker.record_failure(factory, 'Unknown')) is False
    factory.assert_not_called()


def test_timezone_change_requires_new_run():
    assert worker.resume_blocker(active(), replace(decision(), timezone='UTC'), SCOPE) == 'SOURCE_CONTEXT_CHANGED'


def test_successful_resume_clears_previous_failure_code(monkeypatch):
    run = active(status='FAILED', error_code='OldFailure')
    *_, batch = harness(monkeypatch, run)
    assert batch()['status'] == 'RUNNING' and run.error_code is None


def test_reconciliation_rejects_false_completeness_before_any_query():
    run = active()
    run.metadata_json['source_decision']['coverage_status'] = 'CONFIRMED'
    db = SimpleNamespace(scalars=AsyncMock(), add=Mock())
    with pytest.raises(ValueError, match='compatible immutable SHADOW'):
        asyncio.run(store.reconcile_batch(db, run, [features()]))
    db.scalars.assert_not_awaited(); db.add.assert_not_called()


def test_source_lock_failure_prevents_state_or_database_access(monkeypatch):
    from app.services.seasonality_worker import SeasonalityBusy
    @asynccontextmanager
    async def busy(*args, **kwargs):
        raise SeasonalityBusy('resource_busy')
        yield
    monkeypatch.setattr(worker, 'advisory', busy)
    monkeypatch.setattr(worker, 'ensure_headroom', Mock())
    factory = Mock(side_effect=AssertionError('Lock not acquired'))
    engine = SimpleNamespace(dialect=SimpleNamespace(name='postgresql'))
    with pytest.raises(SeasonalityBusy):
        asyncio.run(worker.run_batch(factory, engine, configuration_enabled=True))
    factory.assert_not_called()


def test_lost_source_lock_prevents_batch_commit(monkeypatch):
    run = active()
    control, db, context, scan, evaluate, publish, batch = harness(monkeypatch, run)
    monkeypatch.setattr(worker, 'verify', AsyncMock(side_effect=RuntimeError('Lost lock')))
    with pytest.raises(RuntimeError, match='Lost lock'): batch()
    db.commit.assert_not_awaited(); publish.assert_not_awaited()
    # Mock proves commit is gated; actual PostgreSQL rollback remains untested.


def test_publication_failure_cannot_commit_completed_run(monkeypatch):
    run = active(phase='evaluate')
    control, db, context, scan, evaluate, publish, batch = harness(monkeypatch, run)
    async def complete(db, run, size): run.status = 'SUCCEEDED'
    evaluate.side_effect = complete
    publish.side_effect = RuntimeError('Injected publication error')
    with pytest.raises(RuntimeError, match='publication error'): batch()
    db.commit.assert_not_awaited()
    # ORM changes in memory are not evidence of a PostgreSQL commit/rollback.


def test_cli_failure_passes_exact_checkpoint_token(monkeypatch):
    from datetime import datetime, timezone
    from app import analytics_worker as cli
    from app import database as database_module
    async def scenario():
        loop = asyncio.get_running_loop()
        monkeypatch.setattr(loop, 'add_signal_handler', Mock())
        run = active(heartbeat_at=datetime(2026, 10, 21, tzinfo=timezone.utc))
        control = AnalyticsSignalState(id=1, enabled=True, active_run_id=run.id)
        db = SimpleNamespace(get=AsyncMock(return_value=run), commit=AsyncMock())
        @asynccontextmanager
        async def factory(): yield db
        @asynccontextmanager
        async def lock(*args, **kwargs): yield None
        monkeypatch.setattr(database_module, 'SessionLocal', factory)
        engine = SimpleNamespace(dispose=AsyncMock())
        monkeypatch.setattr(database_module, 'engine', engine)
        monkeypatch.setattr(cli.settings, 'analytics_signals_enabled', True)
        monkeypatch.setattr(cli, 'state', AsyncMock(return_value=control))
        monkeypatch.setattr(cli, 'advisory', lock)
        monkeypatch.setattr(cli, 'run_batch', AsyncMock(side_effect=RuntimeError('Injected')))
        failure = AsyncMock()
        monkeypatch.setattr(cli, 'record_failure', failure)
        options = SimpleNamespace(command='run', as_of=None, scan_size=10, client_size=10)
        assert await cli.execute(options) == 1
        failure.assert_awaited_once_with(factory, 'RuntimeError', run_id=run.id, heartbeat_at=run.heartbeat_at)
        engine.dispose.assert_awaited_once()
    asyncio.run(scenario())


@pytest.mark.parametrize('case', ['replacement', 'advanced', 'unchanged'])
def test_cli_shutdown_does_not_pause_a_different_checkpoint(monkeypatch, case):
    import signal
    from datetime import datetime, timezone
    from app import analytics_worker as cli
    from app import database as database_module
    async def scenario():
        handlers = {}
        loop = asyncio.get_running_loop()
        monkeypatch.setattr(loop, 'add_signal_handler', lambda signum, callback: handlers.update({signum: callback}))
        now = datetime(2026, 10, 21, tzinfo=timezone.utc)
        run = active(heartbeat_at=now)
        other = active(id='other', heartbeat_at=now)
        control = AnalyticsSignalState(id=1, enabled=True, active_run_id=run.id)
        db = SimpleNamespace(get=AsyncMock(), commit=AsyncMock())
        async def get(model, key, **kwargs):
            if model is AnalyticsSignalState: return control
            return other if key == 'other' else run
        db.get.side_effect = get
        @asynccontextmanager
        async def factory(): yield db
        @asynccontextmanager
        async def lock(*args, **kwargs): yield None
        async def interrupted(*args, **kwargs):
            handlers[signal.SIGTERM]()
            if case == 'replacement': control.active_run_id = 'other'
            if case == 'advanced': run.heartbeat_at += timedelta(seconds=1)
            return dict(status='SUCCEEDED', run_id=run.id, heartbeat_at=now.isoformat())
        monkeypatch.setattr(database_module, 'SessionLocal', factory)
        monkeypatch.setattr(database_module, 'engine', SimpleNamespace(dispose=AsyncMock()))
        monkeypatch.setattr(cli.settings, 'analytics_signals_enabled', True)
        monkeypatch.setattr(cli, 'state', AsyncMock(return_value=control))
        monkeypatch.setattr(cli, 'advisory', lock)
        monkeypatch.setattr(cli, 'run_batch', interrupted)
        options = SimpleNamespace(command='run', as_of=DAY, scan_size=10, client_size=10)
        assert await cli.execute(options) == 0
        assert other.status == 'RUNNING'
        assert run.status == ('PAUSED' if case == 'unchanged' else 'RUNNING')
    asyncio.run(scenario())


def test_normalized_target_type_collision_is_ambiguous(monkeypatch):
    monkeypatch.setattr('app.services.analytics_scope.get_analytics_buyer_type_names', AsyncMock(return_value={
        'Оптовик':['A  B', 'E  F', 'Valid'], 'Корпоративный':['A B', 'E F']}))
    scope = asyncio.run(load_analytics_scope())
    assert scope['client_keys'] == ['valid']
    assert scope['department'] == 'Авиаторов'


@pytest.mark.parametrize('catalogue', [None, [], {'Оптовик': []}, {'Оптовик': [None], 'Корпоративный': []}])
def test_malformed_buyer_catalogue_fails_closed(monkeypatch, catalogue):
    monkeypatch.setattr('app.services.analytics_scope.get_analytics_buyer_type_names', AsyncMock(return_value=catalogue))
    with pytest.raises(ClientsVrError): asyncio.run(load_analytics_scope())
