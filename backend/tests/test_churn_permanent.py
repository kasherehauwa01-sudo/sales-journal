"""Isolated SQLite/transport tests; no production DB, HTTP or real task creation."""
import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker

from app.models import Base, ChurnSchedule, ChurnJob, ChurnReceipt, AnalyticsAutomationEvent
from app.services import churn_schedule as s
from app.services.churn_receiver_evidence import attach
from app.services.analytics_automation import digest
from app.services.churn_pilot import ALGORITHM_POLICY
from app.routers.churn_scenario import router
from app.services.settings_auth import require_settings_admin
from app.database import get_db

NOW = datetime(2026, 10, 30, 7, tzinfo=timezone.utc)
CONFIG = dict(time='09:00', daily_limit=5, cooldown_days=30, algorithm_policy=ALGORITHM_POLICY,
              producer_policy_revision=4, receiver_policy_revision=2)


@asynccontextmanager
async def database():
    engine=create_async_engine('sqlite+aiosqlite:///:memory:')
    async with engine.begin() as c:
        await c.run_sync(Base.metadata.create_all)
    factory=async_sessionmaker(engine,expire_on_commit=False)
    try:
        yield factory
    finally:
        await engine.dispose()


async def approved(db):
    await s.change(db,'draft',CONFIG,revision=0,actor='test-operator')
    await s.change(db,'approve',{'confirm_business_approval':True},revision=1,actor='test-operator')
    await s.change(db,'enable',{},revision=2,actor='test-operator')


def test_no_initial_enablement_approval_and_daily_identity():
    async def run():
        async with database() as factory:
            async with factory.begin() as db:
                assert await s.enqueue(db,now=NOW) is None
                await s.change(db,'draft',CONFIG,revision=0,actor='op')
                assert not (await s.control(db)).enabled
                with pytest.raises(ValueError): await s.change(db,'enable',{},revision=1,actor='op')
                with pytest.raises(ValueError): await s.change(db,'approve',{'confirm_business_approval':1},revision=1,actor='op')
                await s.change(db,'approve',{'confirm_business_approval':True},revision=1,actor='op')
                assert await s.enqueue(db,now=NOW) is None
                await s.change(db,'enable',{},revision=2,actor='op')
                assert await s.enqueue(db,now=NOW-timedelta(hours=2)) is None
                a=await s.enqueue(db,now=NOW);b=await s.enqueue(db,now=NOW+timedelta(hours=1))
                assert a.id==b.id
                c=await s.enqueue(db,now=NOW+timedelta(days=1))
                assert c.id!=a.id
                assert (await s.overview(db,now=NOW))['next_run']=='2026-10-31T09:00:00+03:00'
    asyncio.run(run())


@pytest.mark.parametrize('key,value',[('daily_limit',0),('daily_limit',101),('daily_limit',True),('cooldown_days',0),('time','25:00'),('algorithm_policy','aviator-churn-pilot/1')])
def test_invalid_policy_is_not_saved(key,value):
    async def run():
        async with database() as factory:
            async with factory.begin() as db:
                with pytest.raises(ValueError):await s.change(db,'draft',dict(CONFIG,**{key:value}),revision=0,actor='op')
                assert not (await s.control(db)).approved
    asyncio.run(run())


def test_draft_revokes_approval_old_jobs_blocked_diagnostic_cannot_deliver():
    async def run():
        async with database() as factory:
            async with factory.begin() as db:
                await approved(db);job=await s.enqueue(db,now=NOW)
                await s.change(db,'draft',CONFIG,revision=2,actor='op')
                with pytest.raises(ValueError):s.guard_job(job,await s.control(db),NOW)
                diagnostic=await s.enqueue(db,now=NOW,diagnostic=True)
                assert await s.page(db,diagnostic,now=NOW)==[]
                with pytest.raises(ValueError):s.guard_job(diagnostic,await s.control(db),NOW+timedelta(days=1))
    asyncio.run(run())


def test_restart_partial_ack_and_replay_do_not_duplicate(monkeypatch):
    monkeypatch.setattr('app.services.analytics_release_guard.current_release',AsyncMock())
    async def run():
        async with database() as factory:
            async with factory.begin() as db:
                await approved(db);job=await s.enqueue(db,now=NOW)
                job.release_id='release';job.status='RELEASED';job.counters={'allowed':2}
                # SQLite disables FK checks here; ledger behavior doesn't forge authorization.
                db.add_all([AnalyticsAutomationEvent(id=i,release_id='release',payload={'id':i}) for i in (1,2)])
                jid=job.id
            async with factory.begin() as db:
                job=await db.get(ChurnJob,jid)
                assert len(await s.page(db,job,now=NOW))==2
                receipt={'event_id':1,'event_hash':digest({'id':1}),'decision':'TASK_CREATED','task_id':'task-1','reasons':[]}
                await s.acknowledge(db,job,[receipt],now=NOW)
                await s.acknowledge(db,job,[receipt],now=NOW)
                assert job.status=='DELIVERING'
            async with factory.begin() as db:
                job=await db.get(ChurnJob,jid)
                assert [e.id for e in await s.page(db,job,now=NOW)]==[2]
                await s.acknowledge(db,job,[{'event_id':2,'event_hash':digest({'id':2}),'decision':'BLOCKED','reasons':['IDENTITY_NOT_VERIFIED']}],now=NOW)
                assert job.status=='PARTIAL'
                assert job.counters['accepted']==1 and job.counters['tasks_created']==1 and job.counters['rejected']==1
                assert await db.scalar(select(func.count()).select_from(ChurnReceipt))==2
    asyncio.run(run())


@pytest.mark.parametrize('mutation',[{'event_hash':'fake'},{'decision':'READY'},{'task_id':None},{'reasons':['private customer text']},{'event_id':True}])
def test_unconfirmed_or_wrong_ack_rejected(mutation):
    async def run():
        async with database() as factory:
            async with factory.begin() as db:
                await approved(db);job=await s.enqueue(db,now=NOW);job.release_id='release'
                db.add(AnalyticsAutomationEvent(id=1,release_id='release',payload={'id':1}));await db.flush()
                r={'event_id':1,'event_hash':digest({'id':1}),'decision':'TASK_CREATED','task_id':'task','reasons':[]}
                with pytest.raises(ValueError):await s.acknowledge(db,job,[dict(r,**mutation)],now=NOW)
                assert await db.scalar(select(func.count()).select_from(ChurnReceipt))==0
    asyncio.run(run())


def evidence():
    event=dict(subject={'key':'client'},assignment={'manager_id':3,'binding_revision':1},last_purchase='2026-08-01',
        as_of='2026-10-29',run={'source_revision':7,'source_decision':{}},provenance={'shadow_run_id':'source'})
    source=dict(source_run_id='source',source_revision=7,as_of='2026-10-29',complete=True)
    review=dict(status='VERIFIED',client_id='client',manager_id=3,binding_revision=1,last_purchase='2026-08-01')
    bundle=dict(upstream_manifest={'payload_json':json.dumps(source)},binding_receipts={'client':{'payload_json':json.dumps(review)}})
    return event,bundle


def test_receiver_proofs_are_bound_and_preliminary_never_promoted():
    event,bundle=evidence();original=json.dumps(event)
    result=attach(event,bundle,expected_hash=digest(bundle),policy_revision=2,config=CONFIG)
    assert result['binding_receipt']==bundle['binding_receipts']['client']
    assert json.dumps(event)==original
    with pytest.raises(ValueError):attach(event,bundle,expected_hash='wrong',policy_revision=2,config=CONFIG)
    review=json.loads(bundle['binding_receipts']['client']['payload_json']);review['status']='PRELIMINARY_MATCH'
    bundle['binding_receipts']['client']['payload_json']=json.dumps(review)
    with pytest.raises(ValueError):attach(event,bundle,expected_hash=digest(bundle),policy_revision=2,config=CONFIG)


def test_admin_control_csrf_and_no_http_approval(monkeypatch):
    async def run():
        async with database() as factory:
            app=FastAPI();app.include_router(router)
            async def dbs():
                async with factory() as db:yield db
            app.dependency_overrides[get_db]=dbs
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
                assert (await client.post('/settings/churn-scenario/automation',json={})).status_code in {401,503}
                app.dependency_overrides[require_settings_admin]=lambda:None
                body={'action':'draft','revision':0,'settings':CONFIG}
                assert (await client.post('/settings/churn-scenario/automation',json=body)).status_code==403
                assert (await client.post('/settings/churn-scenario/automation',json=body,headers={'X-Churn-Control':'1','Origin':'https://evil.test'})).status_code==403
                r=await client.post('/settings/churn-scenario/automation',json=body,headers={'X-Churn-Control':'1'})
                assert r.status_code==200 and not r.json()['approved']
                r=await client.post('/settings/churn-scenario/automation',json={'action':'approve','revision':1},headers={'X-Churn-Control':'1'})
                assert r.status_code==422
                r=await client.post('/settings/churn-scenario/automation',json={'action':'diagnostic','revision':1},headers={'X-Churn-Control':'1'})
                assert r.status_code==200
    asyncio.run(run())


def host_module():
    import sys
    scripts=str(Path(__file__).parents[1]/'scripts')
    sys.path.insert(0,scripts)
    spec=importlib.util.spec_from_file_location('permanent_host',Path(scripts)/'run_churn_automation.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


def test_host_delivery_replays_identical_page_after_lost_ack():
    host=host_module();calls=[]
    page={'job_id':'job','release_id':'release','events':[{'event_id':1,'event_hash':'hash','event':{}}]}
    def runner(argv,**kw):
        calls.append((argv,kw))
        action=argv[argv.index('app.churn_automation')+1] if 'app.churn_automation' in argv else argv[-1]
        if action=='page':return json.dumps(page)
        if action in {'preflight','deliver'}:return json.dumps({'job_id':'job','release_id':'release','receipts':[{'decision':'TASK_EXISTS'}]})
        if action=='ack':return json.dumps({'status':'SUCCEEDED'})
        raise AssertionError(argv)
    job={'id':'job','status':'RELEASED'}
    assert host.process_job(job,{'calltrack_root':'/test'},{},runner=runner,deadline=host.time.monotonic()+10,verify=lambda c:None)['status']=='SUCCEEDED'
    assert calls[1][1]['input_text']==calls[2][1]['input_text']
    assert calls[1][0][-1]=='preflight' and calls[2][0][-1]=='deliver'


def test_old_pilot_active_blocks_before_docker_or_php():
    host=host_module();calls=[]
    def runner(argv,**kw):calls.append(argv);return 'active\n'
    with pytest.raises(ValueError):host.run({}, {},runner=runner,verify=lambda c:None)
    assert len(calls)==1 and calls[0][0]=='/usr/bin/systemctl'


def test_host_failure_records_without_exposing_private_content():
    host=host_module();calls=[]
    def runner(argv,**kw):
        calls.append(argv)
        if 'page' in argv:raise host.ObservationError('RUNTIME_ERROR')
    with pytest.raises(host.ObservationError):host.process_job({'id':'job','status':'RELEASED'},
        {'calltrack_root':'/test'}, {},runner=runner,deadline=host.time.monotonic()+10,verify=lambda c:None)
    assert len(calls)==1


def test_units_are_opt_in_bounded_and_do_not_start_web_background_worker():
    root=Path(__file__).parents[2]
    service=(root/'deploy/churn-permanent/systemd/sales-churn-permanent.service').read_text()
    timer=(root/'deploy/churn-permanent/systemd/sales-churn-permanent.timer').read_text()
    assert 'TimeoutStartSec=240' in service and 'KillMode=control-group' in service
    assert 'ExecStopPost=-' in service and 'Europe/Moscow' in timer
    assert 'Persistent=false' in timer
    main=(root/'backend/app/main.py').read_text()
    assert 'churn_automation.execute' not in main and 'run_churn_automation' not in main


def test_resume_old_frozen_day_then_calculate_current_day(monkeypatch):
    from app import churn_automation as cli
    from app.models import AnalyticsSignalRun
    monkeypatch.setattr(cli,'now',lambda:NOW)
    calls=[]
    async def batch(*args,**kwargs):
        calls.append(kwargs)
        return {'status':'SUCCEEDED','run_id':'old' if len(calls)==1 else 'current'}
    monkeypatch.setattr(cli,'run_batch',batch)
    async def run():
        async with database() as factory:
            async with factory.begin() as db:
                await approved(db);job=await s.enqueue(db,now=NOW);jid=job.id
                for rid,ago in [('old',2),('current',1)]:
                    db.add(AnalyticsSignalRun(id=rid,status='SUCCEEDED',phase='done',as_of=NOW.date()-timedelta(days=ago),
                        source_revision=1,upper_sale_id=0,started_at=NOW,heartbeat_at=NOW,metadata_json={},counters={'CHURN_RISK':3}))
            async with factory() as db:engine=db.bind
            await cli.calculate(factory,engine,jid,deadline=cli.time.monotonic()+5)
            async with factory() as db:
                job=await db.get(ChurnJob,jid)
                assert job.run_id=='current' and job.status=='AWAITING_EVIDENCE'
            assert len(calls)==2 and all(c['as_of'] is None for c in calls)
    asyncio.run(run())


@pytest.mark.parametrize('decision',['TASK_EXISTS','REVIEW'])
def test_lost_initial_receipt_counts_real_task_on_replay(decision):
    async def run():
        async with database() as factory:
            async with factory.begin() as db:
                await approved(db);job=await s.enqueue(db,now=NOW);job.release_id='release';job.counters={'allowed':1}
                db.add(AnalyticsAutomationEvent(id=1,release_id='release',payload={'id':1}));await db.flush()
                await s.acknowledge(db,job,[dict(event_id=1,event_hash=digest({'id':1}),decision=decision,
                    task_id='task',created_for_release=True)],now=NOW)
                assert job.status==('SUCCEEDED' if decision=='TASK_EXISTS' else 'PARTIAL')
                assert job.counters['tasks_created']==1
    asyncio.run(run())


def test_additive_migration_roundtrip_on_sqlite():
    from alembic.migration import MigrationContext
    from alembic.operations import Operations
    from sqlalchemy import text
    path=Path(__file__).parents[1]/'alembic/versions/20261030_25_churn_schedule.py'
    spec=importlib.util.spec_from_file_location('permanent_migration',path)
    migration=importlib.util.module_from_spec(spec);spec.loader.exec_module(migration)
    async def run():
        engine=create_async_engine('sqlite+aiosqlite:///:memory:')
        async with engine.begin() as c:
            # Minimal pre-existing source tables, never use a real DB URL.
            await c.execute(text('CREATE TABLE analytics_automation_releases(id VARCHAR(36) PRIMARY KEY)'))
            await c.execute(text("INSERT INTO analytics_automation_releases VALUES('preserved')"))
            def upgrade(conn):
                with Operations.context(MigrationContext.configure(conn)):migration.upgrade()
            await c.run_sync(upgrade)
            assert await c.scalar(text('SELECT COUNT(*) FROM churn_schedule'))==0
            def downgrade(conn):
                with Operations.context(MigrationContext.configure(conn)):migration.downgrade()
            await c.run_sync(downgrade)
            assert await c.scalar(text('SELECT id FROM analytics_automation_releases'))=='preserved'
        await engine.dispose()
    asyncio.run(run())
