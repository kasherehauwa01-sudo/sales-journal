"""Synthetic read-only scenario tests: no production DB, API or systemd access."""
import asyncio
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import importlib.util
import json
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import event, insert
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from app.config import settings
from app.database import get_db
from app.models import (Base, AnalyticsSignalRun as Run, AnalyticsSignalState as State,
    AnalyticsAutomationPolicy as Policy, AnalyticsAutomationIdentity as Identity,
    AnalyticsAutomationRelease as Release, AnalyticsAutomationEvent as Event,
    AnalyticsAutomationDecision as Decision, SeasonalityState)
from app.routers import churn_scenario
from app.services.churn_monitor import build_status
from app.services.churn_telemetry import Telemetry, load_telemetry, read_private_json, MAX_BYTES
from app.services.churn_pilot import ALGORITHM_POLICY, GENERATOR_VERSION
from app.services.settings_auth_crypto import create_session

NOW = datetime(2026, 10, 30, 7, tzinfo=timezone.utc)
CLOSED = NOW.date()-timedelta(days=1)
CONFIG = dict(automation_enabled=True, shadow_worker_enabled=False, calltrack_token_configured=True)


def model():
    return dict(policy=dict(enabled=True, approved=True, revision=4, algorithm_policy=ALGORITHM_POLICY, min_confidence=.4),
        control=dict(published_run_id='run-1', active_run_id=None, enabled=False),
        runs=[dict(id='run-1', status='SUCCEEDED', as_of=CLOSED, started_at=NOW-timedelta(hours=1), finished_at=NOW,
            source_revision=7, counters=dict(processed=10, CHURN_RISK=3), error_code=None, generator_version=GENERATOR_VERSION)],
        releases=[dict(id='release-1', source_run_id='run-1', sequence=3, created_at=NOW,
            algorithm_policy=ALGORITHM_POLICY, policy_revision=4, mode='AUTOMATION_APPROVED', valid_until=(NOW+timedelta(hours=1)).isoformat())],
        events={'release-1':3}, identities=[dict(status='VERIFIED', stage='VERIFIED', count=10)], reviews=[],
        decisions=[dict(id='decision-1', source_run_id='run-1', created_at=NOW, status='RELEASED', reasons=[], ready=3, candidates=3)],
        approved_at=NOW, source_revision=7, import_pending=False)


def telemetry(**updates):
    data=dict(schema_version=1, collected_at=NOW.isoformat(), integrity='match', codes=[], history=[],
        systemd=dict(timer_enabled=True, timer_active=True, service_state='inactive', service_result='success',
            last_attempt=NOW.isoformat(), last_success=NOW.isoformat(), next_run=(NOW+timedelta(days=1)).isoformat(), schedule='09:00 Europe/Moscow'),
        receiver=dict(status='available', policy_approved=True, churn_enabled=True, priority='NORMAL', due_days=3, min_confidence=.4, approved_generators=[GENERATOR_VERSION],
            accepted_signals=3, tasks_created=2, blocked_signals=0,
            lifetime=dict(limit=10,used=2,approved=True),
            daily=dict(limit=5,used=2,ledger_used=2,approved=True,business_date=NOW.date().isoformat(),policy_version='aviator-churn-pilot/1'),
            manager_budgets=[dict(manager_id=3,limit=5,used=2,approved=True),dict(manager_id=6,limit=5,used=0,approved=True)],
            receipts=[dict(release_id='release-1',accepted=3,blocked=0,tasks=2)]))
    data.update(updates)
    return Telemetry.model_validate(data)


def view(m=None, t=None, config=None, error=None):
    return build_status(m or model(), t if t is not None else telemetry(), error, now=NOW, config=config or CONFIG)


def test_healthy_state_requires_real_evidence_not_just_flags():
    result=view()
    assert result['status']=='ENABLED' and result['read_only']
    assert result['configuration']['mode']=='SHADOW'
    assert result['configuration']['shadow_worker_enabled'] is False  # Existing external timer is separate.
    assert result['statistics']['published_signals']==3 and result['statistics']['accepted_signals']==3
    assert result['statistics']['tasks_created']==2
    assert result['receiver']['lifetime']['remaining']==8
    assert result['diagnostics']==[]


@pytest.mark.parametrize('case,code',[
    ('algorithm','ALGORITHM_POLICY_NOT_APPROVED'),('policy','POLICY_NOT_APPROVED'),
    ('identity','IDENTITY_NOT_VERIFIED'),('daily','DAILY_BUDGET_EXHAUSTED'),
    ('lifetime','LIFETIME_BUDGET_EXHAUSTED'),('receiver','CALLTRACK_UNAVAILABLE'),
    ('auth','AUTHORIZATION_FAILED'),('service','SYSTEMD_FAILED'),
    ('publication','NO_CURRENT_PUBLICATION'),('unconfirmed','DELIVERY_UNCONFIRMED'),
    ('source','SOURCE_CHANGED_OR_PENDING'),('stale','SOURCE_STALE'),
    ('integrity','REVIEWED_CODE_CHANGED'),('worker','WORKER_FAILED'),
    ('signature','UPSTREAM_SIGNATURE_INVALID'),('ledger','BUDGET_UNCONFIRMED'),
    ('receiver-policy','RECEIVER_POLICY_UNCONFIRMED'),
])
def test_required_failure_cases(case,code):
    m,t=model(),telemetry()
    if case=='algorithm':m['policy']['algorithm_policy']='aviator-churn-pilot/1'
    if case=='policy':m['policy']['approved']=False
    if case=='identity':m['identities']=[dict(status='REVIEW',stage='PRELIMINARY_MATCH',count=39)]
    if case=='daily':t.receiver.daily.used=t.receiver.daily.ledger_used=5
    if case=='lifetime':t.receiver.lifetime.used=10
    if case=='receiver':t.receiver.status='unavailable'
    if case=='auth':t.receiver.status='unauthorized'
    if case=='service':t.systemd.service_state='failed'
    if case=='publication':m['releases']=[]
    if case=='unconfirmed':t.receiver.receipts=[]
    if case=='source':m['import_pending']=True
    if case=='stale':m['runs'][0]['as_of']-=timedelta(days=1)
    if case=='integrity':t.integrity='mismatch'
    if case=='worker':m['runs'][0]['status']='FAILED'
    if case=='signature':m['decisions'][0].update(status='BLOCKED',reasons=['UPSTREAM_SIGNATURE_INVALID'],ready=0)
    if case=='ledger':t.receiver.daily.ledger_used=1
    if case=='receiver-policy':t.receiver.approved_generators=['inactivity/aviator-churn-pilot/1']
    result=view(m,t)
    assert code in {d['code'] for d in result['diagnostics']}
    assert result['status'] in {'ERROR','NEEDS_SETUP'}
    assert result['read_only'] is True


def test_disabled_and_missing_runtime_do_not_invent_success_or_counts():
    result=build_status(model(),None,'RUNTIME_STATUS_MISSING',now=NOW,config=CONFIG)
    assert result['status']=='NEEDS_SETUP'
    assert result['statistics']['accepted_signals'] is None
    assert result['statistics']['tasks_created'] is None
    assert result['systemd'] is None and result['runtime_observed_at'] is None
    disabled=view(config={**CONFIG,'automation_enabled':False})
    assert disabled['status']=='DISABLED'


def test_repeated_reads_do_not_change_model_or_duplicate_history():
    m,t=model(),telemetry();before=deepcopy(m)
    a,b=view(m,t),view(m,t)
    assert a==b and m==before
    assert len(a['history'])==1


def test_historical_error_is_not_current_error_after_success():
    m=model();m['decisions'].append(dict(id='old',source_run_id='old-run',created_at=NOW-timedelta(days=1),status='BLOCKED',reasons=['UPSTREAM_SIGNATURE_INVALID'],ready=0,candidates=1))
    assert view(m)['status']=='ENABLED'


def test_unknown_codes_never_expose_raw_logs_or_secrets():
    t=telemetry(codes=['SECRET_CUSTOMER_TOKEN with personal data'])
    result=view(t=t)
    serialized=json.dumps(result)
    assert 'SECRET_CUSTOMER_TOKEN' not in serialized
    assert result['diagnostics'][0]['code']=='RUNTIME_ERROR'


def test_no_publication_has_unknown_not_fake_zero():
    m=model();m['releases']=[];m['decisions']=[];m['events']={};m['runs'][0]['counters']={}
    result=view(m)
    assert result['statistics']['published_signals'] is None
    assert result['statistics']['ready_signals'] is None
    assert result['statistics']['analyzed_clients'] is None


def test_runtime_file_security_and_freshness(tmp_path,monkeypatch):
    path=tmp_path/'status.json'
    assert load_telemetry(path,NOW)[1]=='RUNTIME_STATUS_MISSING'
    path.write_text(telemetry().model_dump_json());path.chmod(0o600)
    assert read_private_json(path,owners={os.getuid()})['schema_version']==1
    link=tmp_path/'link';link.symlink_to(path)
    with pytest.raises(OSError):read_private_json(link,owners={os.getuid()})
    path.chmod(0o666)
    with pytest.raises(ValueError):read_private_json(path,owners={os.getuid()})
    path.chmod(0o600);path.write_bytes(b'x'*(MAX_BYTES+1))
    with pytest.raises(ValueError):read_private_json(path,owners={os.getuid()})
    monkeypatch.setattr('app.services.churn_telemetry.read_private_json',lambda p:telemetry(collected_at=NOW-timedelta(hours=37)).model_dump())
    assert load_telemetry(path,NOW)[1]=='RUNTIME_STATUS_STALE'
    monkeypatch.setattr('app.services.churn_telemetry.read_private_json',lambda p:{'schema_version':1,'collected_at':NOW.isoformat(),'token':'SECRET'})
    assert load_telemetry(path,NOW)[1]=='RUNTIME_STATUS_INVALID'


def test_admin_api_projects_database_fields_and_never_writes(monkeypatch):
    from app.services import churn_monitor
    async def scenario():
        engine=create_async_engine('sqlite+aiosqlite:///:memory:')
        factory=async_sessionmaker(engine,expire_on_commit=False)
        async with engine.begin() as connection:await connection.run_sync(Base.metadata.create_all)
        async with factory() as db:
            db.add_all([
                Policy(id=1,enabled=True,approved=True,revision=4,sequence=3,settings_json={'algorithm_policy':ALGORITHM_POLICY,'min_confidence':.4,'private_key':'SECRET'}),
                Run(id='run-1',status='SUCCEEDED',phase='complete',as_of=CLOSED,source_revision=7,upper_sale_id=0,sale_cursor=0,client_cursor='',started_at=NOW,heartbeat_at=NOW,finished_at=NOW,counters={'processed':10,'CHURN_RISK':3},error_count=0,metadata_json={'generator_version':GENERATOR_VERSION,'scope':{'client_keys':['PERSONAL NAME']},'signature':'SECRET'}),
                State(id=1,enabled=False,published_run_id='run-1'),SeasonalityState(id=1,source_revision=7),
                Identity(subject_key='private-hash',client_id='synthetic-id',manager_id=3,revision=1,status='VERIFIED',evidence={}),
                Release(id='release-1',sequence=3,source_run_id='run-1',created_at=NOW,proof_hash='SECRET',metadata_json={'algorithm_policy':ALGORITHM_POLICY,'policy_revision':4,'run':{'mode':'AUTOMATION_APPROVED','source_decision':{'valid_until':(NOW+timedelta(hours=1)).isoformat()}},'manifest':{'signature':'SECRET'}}),
                Event(id=1,release_id='release-1',payload={'private_key':'SECRET'}),
                Decision(id='decision-1',source_run_id='run-1',created_at=NOW,data={'status':'RELEASED','reasons':[],'ready':1,'candidates':1,'private_key':'SECRET'}),
            ])
            # Published run remains authoritative even after more than 20 failed attempts.
            for index in range(21):
                db.add(Run(id=f'failed-{index}', status='FAILED', phase='evaluate', as_of=CLOSED, source_revision=7, upper_sale_id=0, sale_cursor=0, client_cursor='', started_at=NOW+timedelta(minutes=index+1), heartbeat_at=NOW, finished_at=NOW, counters={}, error_count=1, error_code='WORKER_FAILED', metadata_json={'generator_version':GENERATOR_VERSION}))
            await db.commit()
        app=FastAPI();app.include_router(churn_scenario.router,prefix='/api')
        async def sessions():
            async with factory() as db:yield db
        app.dependency_overrides[get_db]=sessions
        monkeypatch.setattr(settings,'settings_admin_password_hash','configured')
        monkeypatch.setattr(settings,'settings_admin_session_secret','x'*40)
        monkeypatch.setattr(churn_monitor,'load_telemetry',lambda *a:(telemetry(),None))
        captured=[]
        @event.listens_for(engine.sync_engine,'before_cursor_execute')
        def statements(conn,cursor,statement,parameters,context,executemany):
            captured.append(statement)
            if 'analytics_automation_identities' in statement and 'GROUP BY' in statement and 'stage' in statement:
                from sqlalchemy.dialects import postgresql
                compiled=context.compiled.statement.compile(dialect=postgresql.asyncpg.dialect())
                assert list(compiled.params.values()).count('stage')==1  # Reuse the JSON-path bind in SELECT/GROUP BY.
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
            path='/api/settings/churn-scenario/status'
            assert (await client.get(path)).status_code==401
            assert captured==[]
            client.cookies.set('sales_settings_admin',create_session('x'*40))
            result=await client.get(path)
            assert result.status_code==200,result.text
            assert result.headers['cache-control']=='no-store'
            body=result.json()
            assert body['api_version']==1 and body['read_only']
            assert body['statistics']['published_signals']==1
            assert body['statistics']['analyzed_clients']==10
            assert 'SECRET' not in result.text and 'PERSONAL NAME' not in result.text and 'private-hash' not in result.text
            assert (await client.post(path)).status_code==405
            assert all(sql.lstrip().upper().startswith('SELECT') for sql in captured)
        await engine.dispose()
    asyncio.run(scenario())


def test_database_error_is_safe_and_rollback_is_attempted(monkeypatch):
    monkeypatch.setattr(churn_scenario,'status',AsyncMock(side_effect=SQLAlchemyError('SECRET database connection')))
    db=SimpleNamespace(get_bind=lambda:SimpleNamespace(dialect=SimpleNamespace(name='sqlite')),rollback=AsyncMock())
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as error:asyncio.run(churn_scenario.scenario_status(db))
    assert error.value.status_code==503 and 'SECRET' not in json.dumps(error.value.detail)
    db.rollback.assert_awaited_once()


def test_shadow_or_old_release_never_counts_as_approved_publication():
    for field, value in [('mode','SHADOW'),('algorithm_policy','aviator-churn-pilot/1'),('policy_revision',3)]:
        m=model();m['releases'][0][field]=value
        result=view(m)
        assert result['publication'] is None
        assert result['statistics']['accepted_signals'] is None
        assert 'NO_CURRENT_PUBLICATION' in {d['code'] for d in result['diagnostics']}


def test_cancellation_rolls_back_without_running_other_work(monkeypatch):
    monkeypatch.setattr(churn_scenario,'status',AsyncMock(side_effect=asyncio.CancelledError()))
    db=SimpleNamespace(get_bind=lambda:SimpleNamespace(dialect=SimpleNamespace(name='sqlite')),rollback=AsyncMock())
    with pytest.raises(asyncio.CancelledError):asyncio.run(churn_scenario.scenario_status(db))
    db.rollback.assert_awaited_once()


def test_timeout_is_controlled_and_does_not_expose_exception(monkeypatch):
    monkeypatch.setattr(churn_scenario,'status',AsyncMock(side_effect=TimeoutError('SECRET')))
    db=SimpleNamespace(get_bind=lambda:SimpleNamespace(dialect=SimpleNamespace(name='sqlite')),rollback=AsyncMock())
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as error:asyncio.run(churn_scenario.scenario_status(db))
    assert error.value.status_code==503 and 'SECRET' not in json.dumps(error.value.detail)
    db.rollback.assert_awaited_once()


def test_identity_conflict_and_missing_receiver_observation_are_explicit():
    m=model();m['identity_conflict']=True
    t=telemetry();t.receiver.status='unknown'
    result=view(m,t)
    codes={d['code'] for d in result['diagnostics']}
    assert 'IDENTITY_CONFLICT' in codes and 'RECEIVER_STATUS_MISSING' in codes
    assert 'CALLTRACK_UNAVAILABLE' not in codes


def test_naive_telemetry_dates_are_rejected():
    from pydantic import ValidationError
    with pytest.raises(ValidationError):telemetry(collected_at='2026-10-30T07:00:00')


def test_manager_cap_is_independent_of_daily_and_total_remaining():
    t=telemetry();t.receiver.manager_budgets[0].used=5
    result=view(t=t)
    assert result['receiver']['lifetime']['remaining']==8
    assert result['receiver']['daily']['remaining']==3
    assert 'MANAGER_BUDGET_EXHAUSTED' in {d['code'] for d in result['diagnostics']}


def test_past_next_run_and_previous_business_day_are_not_presented_as_current():
    t=telemetry();t.systemd.next_run=NOW-timedelta(minutes=1)
    t.receiver.daily.business_date=(NOW.date()-timedelta(days=1)).isoformat()
    result=view(t=t)
    assert result['systemd']['next_run'] is None
    assert result['receiver']['daily']['used'] is None and result['receiver']['daily']['remaining'] is None
    assert {'SCHEDULE_UNCONFIRMED','BUDGET_UNCONFIRMED'} <= {d['code'] for d in result['diagnostics']}


def test_receiver_type_policy_is_independent_of_producer_enabled_flag():
    t=telemetry();t.receiver.churn_enabled=False
    assert 'RECEIVER_POLICY_UNCONFIRMED' in {d['code'] for d in view(t=t)['diagnostics']}
    t=telemetry();t.receiver.receipts[0].blocked=1
    assert 'RECEIVER_SIGNALS_BLOCKED' in {d['code'] for d in view(t=t)['diagnostics']}
