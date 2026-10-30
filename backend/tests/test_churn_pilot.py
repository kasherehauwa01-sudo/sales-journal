from datetime import date, timedelta
import pytest
from app.services import inactivity, churn_pilot
from app.services.analytics_scope import SCOPE_POLICY


def cycle_features(*, days=50, median=90, count=3, gaps=2):
    return dict(distinct_purchase_days=count, last_purchase_date=date(2026, 10, 23)-timedelta(days=days),
                gap_count=gaps, history_span_days=180, median_interval=median,
                mean_interval=median, q75=median, mad=0, reference_revenue_180=18000)


@pytest.mark.parametrize('days,median,expected,threshold', [
    (50, 90, None, 91), (90, 90, None, 91), (91, 90, 'CHURN_RISK', 91),
    (103, 90, 'CHURN_RISK', 91), (44, 90, None, 91), (45, 90, None, 91),
    (46, 90, None, 91), (11, 10, 'CHURN_RISK', 11),
    (17, 17.5, None, 18), (18, 17.5, 'CHURN_RISK', 18),
    (42, 42, None, 43), (55, 42, 'CHURN_RISK', 43),
])
def test_individual_cycle_boundary(days, median, expected, threshold):
    r = churn_pilot.evaluate(cycle_features(days=days, median=median), date(2026, 10, 23))
    assert r['eligible'] and r['signal_type'] == expected
    assert r['metrics']['automation_allowed'] is False
    assert r['metrics']['churn_threshold'] == threshold
    assert r['metrics']['overdue_threshold'] is None
    assert r['metrics']['parameters']['churn_rule'] == 'days_without_purchase>median_interval'
    assert 'churn_days' not in r['metrics']['parameters']
    if expected is None:
        assert r['score'] == 0


@pytest.mark.parametrize('count,eligible', [(0, False), (1, False), (2, False), (3, True)])
def test_history(count, eligible):
    r = churn_pilot.evaluate(cycle_features(days=103, count=count), date(2026, 10, 23))
    assert r['eligible'] == eligible
    assert r['metrics']['coverage'] == 'UNKNOWN'
    if not eligible:
        assert r['signal_type'] is None and r['score'] == 0


@pytest.mark.parametrize('median', [None, 0, -1, float('nan'), float('inf'), -float('inf')])
def test_missing_or_invalid_cycle_cannot_fall_back_to_fixed_days(median):
    r = churn_pilot.evaluate(cycle_features(days=103, median=median), date(2026, 10, 23))
    assert not r['eligible'] and r['reason'] == 'INSUFFICIENT_HISTORY'
    assert r['signal_type'] is None and r['metrics']['churn_threshold'] is None


@pytest.mark.parametrize('change', [dict(gap_count=0), dict(gap_count=1), dict(last_purchase_date=None)])
def test_missing_cycle_evidence_cannot_emit(change):
    r = churn_pilot.evaluate(cycle_features(days=103) | change, date(2026, 10, 23))
    assert not r['eligible'] and r['signal_type'] is None


def test_median_not_mean_defines_typical_cycle():
    f = cycle_features(days=50, median=90) | dict(mean_interval=20)
    assert churn_pilot.evaluate(f, date(2026, 10, 23))['signal_type'] is None
    f = cycle_features(days=103, median=90) | dict(mean_interval=120)
    assert churn_pilot.evaluate(f, date(2026, 10, 23))['signal_type'] == 'CHURN_RISK'


def test_confidence_and_commercial_components_are_unchanged():
    f = cycle_features(days=103)
    old = inactivity.evaluate(f, date(2026, 10, 23))
    new = churn_pilot.evaluate(f, date(2026, 10, 23))
    assert new['confidence'] == old['confidence']
    assert new['metrics']['score_components'] == old['metrics']['score_components']
    assert new['metrics']['deviation_ratio'] == old['metrics']['deviation_ratio']
    assert not old['eligible'] and new['eligible']  # No legacy 60-day maximum.
    assert new['score'] > 0


def test_source_health_still_blocks_individual_cycle():
    r = churn_pilot.evaluate(cycle_features(days=103), date(2026, 10, 23), freshness='STALE')
    assert not r['eligible'] and r['reason'] == 'SOURCE_UNRELIABLE'
    assert r['signal_type'] is None


def test_scope_and_legacy():
    scope=dict(policy=SCOPE_POLICY,department='Авиаторов',buyer_types=['Оптовик','Корпоративный'])
    assert churn_pilot.algorithm(scope)[0]==churn_pilot.GENERATOR_VERSION
    assert churn_pilot.algorithm({**scope,'department':'Другой'})[0]==inactivity.GENERATOR_VERSION
    assert churn_pilot.algorithm({**scope,'buyer_types':['Розница']})[0]==inactivity.GENERATOR_VERSION
    assert churn_pilot.algorithm(scope)[2]!=inactivity.POLICY


def test_scoped_positive_days_native_postgresql(monkeypatch):
    import asyncio
    from unittest.mock import AsyncMock
    from sqlalchemy import insert, select
    from app.models import Sale, AnalyticsClientEvaluation, AnalyticsSignalRun, AnalyticsPurchaseDay
    from app.services import analytics_scope
    from test_inactivity_shadow import database, finish
    names=['two','three','same-day','other-store','returns','zero','negative','retail','ambiguous']
    monkeypatch.setattr('app.services.analytics_signal_worker.load_analytics_scope',analytics_scope.load_analytics_scope)
    monkeypatch.setattr(analytics_scope,'get_analytics_buyer_type_names',AsyncMock(return_value={
        'Оптовик':[n for n in names if n!='retail'],'Корпоративный':['ambiguous']}))
    async def scenario():
        async with database() as (engine,factory):
            day=date(2026,10,6);rows=[]
            for name in names:
                for n in range(3):
                    offset=45+(0 if name=='same-day' else n)
                    department='Другой' if name=='other-store' and n==2 else 'Авиаторов'
                    amount=0 if name=='zero' and n==2 else -10 if name=='negative' and n==2 else 100
                    doc='ВЗВ-return' if name=='returns' and n==2 else str(len(rows))
                    if name=='two' and n==2:continue
                    rows.append(dict(id=len(rows)+1,sale_date=day-timedelta(days=offset),client=name,department=department,total_amount=amount,document_number=doc,fingerprint=str(len(rows)),import_id=1))
            rows.append(dict(id=len(rows)+1,sale_date=day,client='three',department='Другой',total_amount=100,document_number='recent-other',fingerprint='recent',import_id=1))
            async with factory() as db:await db.execute(insert(Sale),rows);await db.commit()
            r=await finish(factory,engine,day)
            async with factory() as db:
                evaluations={e.client_key:e for e in (await db.scalars(select(AnalyticsClientEvaluation).where(AnalyticsClientEvaluation.run_id==r['run_id']))).all()}
                assert 'retail' not in evaluations and 'ambiguous' not in evaluations
                assert evaluations['three'].eligible and evaluations['three'].signal_type=='CHURN_RISK'
                for name in ['two','same-day','other-store','returns','zero','negative']:
                    assert not evaluations[name].eligible,name
                run=await db.get(AnalyticsSignalRun,r['run_id'])
                assert run.metadata_json['generator_version']==churn_pilot.GENERATOR_VERSION
                assert run.metadata_json['coverage_status']=='UNKNOWN' and run.metadata_json['automation_allowed'] is False
            again=await finish(factory,engine,day)
            assert again['run_id']==r['run_id']
            async with factory() as db:
                old=await db.get(AnalyticsSignalRun,r['run_id'])
                old.metadata_json={**old.metadata_json,'generator_version':inactivity.GENERATOR_VERSION,
                                   'parameters':inactivity.POLICY,'parameters_version':1}
                await db.commit()
            fresh=await finish(factory,engine,day)
            assert fresh['run_id']!=r['run_id']
    asyncio.run(scenario())


def test_old_checkpoint_is_superseded_not_reused(monkeypatch):
    import asyncio
    from unittest.mock import AsyncMock
    from sqlalchemy import insert
    from app.models import Sale, AnalyticsSignalRun
    from app.services import analytics_scope
    from app.services.analytics_signal_worker import run_batch
    from test_inactivity_shadow import database
    monkeypatch.setattr('app.services.analytics_signal_worker.load_analytics_scope',analytics_scope.load_analytics_scope)
    monkeypatch.setattr(analytics_scope,'get_analytics_buyer_type_names',AsyncMock(return_value={'Оптовик':['fixture'],'Корпоративный':[]}))
    async def scenario():
        async with database() as (engine,factory):
            day=date(2026,10,6)
            async with factory() as db:
                await db.execute(insert(Sale),[dict(id=1,sale_date=day-timedelta(days=45),client='fixture',department='Авиаторов',total_amount=100,document_number='test',fingerprint='test',import_id=1)]);await db.commit()
            r=await run_batch(factory,engine,as_of=day,configuration_enabled=True)
            async with factory() as db:
                run=await db.get(AnalyticsSignalRun,r['run_id']);run.metadata_json={**run.metadata_json,'generator_version':inactivity.GENERATOR_VERSION,'parameters':inactivity.POLICY,'parameters_version':1};await db.commit()
            r=await run_batch(factory,engine,as_of=day,configuration_enabled=True)
            assert r['status']=='SUPERSEDED' and r['error_code']=='ALGORITHM_POLICY_CHANGED'
    asyncio.run(scenario())


def pilot_run(**overrides):
    from test_stored_sales_shadow import active
    run = active(**overrides)
    run.metadata_json.update(
        scope=dict(policy=SCOPE_POLICY, department='Авиаторов', buyer_types=['Оптовик', 'Корпоративный'],
                   client_keys=['alpha'], buyer_type_source='ClientsVR'),
        generator_version=churn_pilot.GENERATOR_VERSION,
        parameters_version=churn_pilot.PARAMETERS_VERSION, parameters=dict(churn_pilot.POLICY))
    return run


@pytest.mark.parametrize('phase', ['scan', 'evaluate'])
def test_fixed_rule_checkpoint_is_superseded_before_processing(monkeypatch, phase):
    from test_stored_sales_shadow import harness
    run = pilot_run(phase=phase)
    run.metadata_json.update(generator_version='inactivity/aviator-churn-pilot/1', parameters_version=2)
    control, db, context, scan, evaluate, publish, batch = harness(monkeypatch, run)
    assert batch()['status'] == 'SUPERSEDED'
    assert run.error_code == 'ALGORITHM_POLICY_CHANGED'
    context.assert_not_awaited()
    scan.assert_not_awaited()
    evaluate.assert_not_awaited()
    publish.assert_not_awaited()
    assert control.published_run_id == 'published'


def test_fixed_rule_completed_run_is_not_reused_or_republished():
    import asyncio
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, Mock
    from app.models import AnalyticsSignalState
    from app.services import analytics_signal_worker as worker
    from app.services.analytics_delivery import publish_delivery
    from test_stored_sales_shadow import decision

    async def scenario():
        old = pilot_run(id='old-fixed-rule', status='SUCCEEDED')
        old.metadata_json.update(generator_version='inactivity/aviator-churn-pilot/1', parameters_version=2)
        control = AnalyticsSignalState(id=1, enabled=True, published_run_id=old.id)
        db = SimpleNamespace(get=AsyncMock(return_value=old), scalar=AsyncMock(return_value=100),
                             add=Mock(), flush=AsyncMock())
        new = await worker.begin_run(db, control, decision(), old.metadata_json['scope'])
        assert new.id != old.id and new.metadata_json['generator_version'] == churn_pilot.GENERATOR_VERSION
        assert control.published_run_id == old.id
        db.scalar.reset_mock()
        with pytest.raises(ValueError, match='compatible'):
            await publish_delivery(db, old)
        db.scalar.assert_not_awaited()
    asyncio.run(scenario())


def test_old_signal_resolves_and_new_cycle_episode_remains_idempotent():
    import asyncio
    from types import SimpleNamespace
    from app.models import AnalyticsSignal, AnalyticsSignalChange
    from app.services import analytics_signal_store as store

    async def scenario():
        run = pilot_run(as_of=date(2026, 10, 23))
        run.metadata_json['source_decision']['effective_as_of'] = run.as_of.isoformat()
        objects = []
        class Session:
            async def scalars(self, query):
                signals = sorted((o for o in objects if isinstance(o, AnalyticsSignal)),
                                 key=lambda s: s.episode_no, reverse=True)
                return SimpleNamespace(all=lambda: signals)
            def add(self, obj):
                objects.append(obj)
            async def flush(self):
                pass
        db = Session()
        f = cycle_features(days=50) | dict(client_key='alpha', display_name='Synthetic client')
        # Seed an active episode, then mark it as historical fixed-rule evidence.
        await store.reconcile_batch(db, run, [f | dict(median_interval=30)])
        signal = next(o for o in objects if isinstance(o, AnalyticsSignal))
        signal.generator_version = 'inactivity/aviator-churn-pilot/1'
        signal.parameters_version = 2
        signal.metrics.update(churn_threshold=45, median_interval=90)
        old_id = signal.id
        await store.reconcile_batch(db, run, [f])
        assert signal.status == 'RESOLVED' and signal.resolution_reason == 'MODEL_REEVALUATION'
        assert signal.id == old_id and signal.score == 0
        assert signal.metrics['churn_threshold'] == 91
        assert len([o for o in objects if isinstance(o, AnalyticsSignal)]) == 1
        # Same last purchase, now 103 days ago: one new active episode, no duplicate.
        run.as_of += timedelta(days=53)
        run.metadata_json['source_decision']['effective_as_of'] = run.as_of.isoformat()
        await store.reconcile_batch(db, run, [f])
        latest = max((o for o in objects if isinstance(o, AnalyticsSignal)), key=lambda s: s.episode_no)
        assert latest.status == 'ACTIVE' and latest.episode_no == 2 and latest.id != old_id
        assert latest.generator_version == churn_pilot.GENERATOR_VERSION
        changes = len([o for o in objects if isinstance(o, AnalyticsSignalChange)])
        await store.reconcile_batch(db, run, [f])
        assert len([o for o in objects if isinstance(o, AnalyticsSignal)]) == 2
        assert len([o for o in objects if isinstance(o, AnalyticsSignalChange)]) == changes
        await store.reconcile_batch(db, run, [f | dict(last_purchase_date=run.as_of-timedelta(days=1))])
        assert latest.status == 'RESOLVED' and latest.resolution_reason == 'PURCHASE_OCCURRED'
    asyncio.run(scenario())


def test_retired_policy_cannot_be_approved_or_exported(monkeypatch):
    import asyncio
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, Mock
    from fastapi import HTTPException
    from app.models import AnalyticsAutomationPolicy, AnalyticsAutomationRelease
    from app.services.analytics_automation import manage_policy
    from app.routers.analytics_delivery import automation_available, automation_changes
    from app.config import settings

    monkeypatch.setattr(settings, 'analytics_automation_enabled', True)
    async def scenario():
        old = AnalyticsAutomationPolicy(id=1, enabled=True, approved=True, revision=1,
                                       settings_json=dict(algorithm_policy='aviator-churn-pilot/1'))
        db = SimpleNamespace(execute=AsyncMock(), get=AsyncMock(return_value=old), add=Mock(), flush=AsyncMock())
        with pytest.raises(ValueError, match='algorithm approval'):
            await manage_policy(db, 'approve', dict(confirm_business_approval=True), actor='test', expected_revision=1)
        with pytest.raises(HTTPException) as error:
            await automation_available(db)
        assert error.value.status_code == 503
        current = AnalyticsAutomationPolicy(id=1, enabled=True, approved=True, revision=2,
                                           settings_json=dict(algorithm_policy=churn_pilot.ALGORITHM_POLICY))
        db.get = AsyncMock(return_value=current)
        assert await automation_available(db) is current
        release = AnalyticsAutomationRelease(id='old-release', metadata_json=dict(algorithm_policy='aviator-churn-pilot/1'))
        db.get = AsyncMock(side_effect=[current, release])
        with pytest.raises(HTTPException) as error:
            await automation_changes('old-release', after=0, page_size=50, db=db)
        assert error.value.status_code == 410
        db.flush.assert_not_awaited()
    asyncio.run(scenario())


def test_new_generator_retains_return_document_exclusions_in_scan():
    import asyncio
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from sqlalchemy.dialects import postgresql
    from app.services.analytics_signal_worker import scan_batch

    async def scenario():
        run = pilot_run(upper_sale_id=3, sale_cursor=0)
        db = SimpleNamespace(
            scalars=AsyncMock(side_effect=[SimpleNamespace(all=lambda: [1, 2, 3]),
                                          SimpleNamespace(all=lambda: ['alpha'])]),
            scalar=AsyncMock(return_value=0), execute=AsyncMock())
        await scan_batch(db, run, 3)
        purchase_day_insert = db.execute.await_args_list[0].args[0]
        compiled = purchase_day_insert.compile(dialect=postgresql.dialect())
        assert all(prefix in compiled.params.values() for prefix in ('ВЗВ-', 'РНВ-', 'ВРМ-'))
        assert 'positive_checks' in str(compiled) and 'CASE WHEN' in str(compiled)
        assert run.sale_cursor == 3
    asyncio.run(scenario())
