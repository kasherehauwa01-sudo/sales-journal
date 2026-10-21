"""Source-period extraction and safe-date integration; disposable PostgreSQL only."""
import asyncio
from dataclasses import replace, FrozenInstanceError
from datetime import date, datetime, timedelta, timezone
import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import select, update, func, text
from app.importer.source_period import extract_declared_period, METHOD, INVALID
from app.services.analytics_source import ImportEvidence, resolve_source
from app.services import analytics_signal_worker as worker
from app.models import ImportBatch, SeasonalityState, AnalyticsSignalRun, AnalyticsSignalState, AnalyticsSignal, AnalyticsClientEvaluation
from test_inactivity_shadow import database, seed, finish, ANCHOR, analytical_test_scope

D=date(2026,10,6);REQUEST=date(2026,10,19);NOW=datetime(2026,10,20,9,tzinfo=timezone.utc)
HEADER='<tr><td>Дата</td><td>№ Док.</td><td>Подразделение</td><td>Сумма</td></tr>'
def evidence(**kw):
    return replace(ImportEvidence(1,'sales.html','completed',imported_at=NOW,declared_start=date(2026,7,1),declared_end=D,
        observed_start=date(2026,7,1),observed_end=D,method=METHOD),**kw)
def decision(imports=None,requested=REQUEST,closed=REQUEST):
    return resolve_source(requested,tuple(imports if imports is not None else [evidence()]),last_closed_business_day=closed,source_revision=1,timezone='Europe/Moscow')

@pytest.mark.parametrize('text,start,end,method',[
 ('за период с 01.07.26 по 06.10.26',date(2026,7,1),D,METHOD),
 (' за  период\u00a0с  01.07.2026   по   06.10.2026 ',date(2026,7,1),D,METHOD),
 ('Отчёт без периода',None,None,None),
 ('за период с 31.02.26 по 06.10.26',None,None,INVALID),
 ('за период с 07.10.26 по 06.10.26',None,None,INVALID),
 ('за период с 01.07.26 по 06.10.',None,None,INVALID),
 ('за период с 01.07.26 по',None,None,INVALID)])
def test_html_period(tmp_path,text,start,end,method):
    p=tmp_path/'report.html';p.write_text(f'<table><tr><td>{text}</td></tr>{HEADER}</table>')
    r=extract_declared_period(p);assert (r.start,r.end,r.method)==(start,end,method)

def test_no_table_conflicting_periods_and_unverified_workbooks(tmp_path):
    p=tmp_path/'2026-10-20.html';p.write_text('<table><tr><td>за период с 01.07.26 по 06.10.26</td></tr>')
    assert extract_declared_period(p).method==INVALID
    p.write_text('<table><tr><td>за период с 01.07.26 по 06.10.26</td></tr><tr><td>за период с 01.07.26 по 07.10.26</td></tr>'+HEADER+'</table>')
    assert extract_declared_period(p).method==INVALID
    for name in ['fake.html','report.xlsx','report.xls']:
        p=tmp_path/name;p.write_bytes(b'PK\x03\x04dummy');assert extract_declared_period(p).end is None

def test_safe_date_old_import_and_immutable_evidence():
    a=decision();assert a.effective_as_of==a.accepted_data_through==D
    assert a.coverage_status=='DECLARED' and a.business_freshness=='STALE' and a.shadow_allowed and not a.automation_allowed
    assert a.reason_codes==('REQUESTED_DATE_AFTER_AVAILABLE_DATA',) and a.evidence[0].imported_at==NOW
    assert decision(requested=D).effective_as_of==D
    with pytest.raises(FrozenInstanceError):a.effective_as_of=REQUEST

def test_empty_last_days_and_current_day_clamp():
    imports=[evidence(declared_end=date(2026,10,20),observed_end=date(2026,10,18))]
    a=decision(imports,requested=date(2026,10,20));assert a.effective_as_of==REQUEST and a.coverage_status=='DECLARED'
    assert a.business_freshness=='CURRENT' and 'REQUESTED_DATE_NOT_CLOSED' in a.reason_codes
    assert decision(imports,requested=date(2026,10,20),closed=date(2026,10,20)).effective_as_of==date(2026,10,20)

@pytest.mark.parametrize('change,coverage,reason',[
 ({'observed_end':date(2026,10,20)},'UNHEALTHY','OBSERVED_OUTSIDE_DECLARED_PERIOD'),
 ({'observed_start':date(2026,6,30)},'UNHEALTHY','OBSERVED_OUTSIDE_DECLARED_PERIOD'),
 ({'declared_start':None,'declared_end':None,'method':None},'UNKNOWN','DECLARED_PERIOD_MISSING'),
 ({'error_rows':1},'UNHEALTHY','IMPORT_PENDING_OR_FAILED_OR_ERRORS'),
 ({'status':'queued'},'UNHEALTHY','IMPORT_PENDING_OR_FAILED_OR_ERRORS'),
 ({'status':'processing'},'UNHEALTHY','IMPORT_PENDING_OR_FAILED_OR_ERRORS'),
 ({'status':'failed'},'UNHEALTHY','IMPORT_PENDING_OR_FAILED_OR_ERRORS'),
 ({'method':INVALID,'declared_start':None,'declared_end':None},'UNHEALTHY','INVALID_DECLARED_PERIOD')])
def test_unusable_evidence_does_not_fallback(change,coverage,reason):
    a=decision([evidence(**change)]);assert a.coverage_status==coverage and reason in a.reason_codes
    assert not a.shadow_allowed and a.effective_as_of is None and a.accepted_data_through is None and not a.automation_allowed

def test_multiple_sources_never_pick_max_and_unknown_blocks():
    newer=evidence(id=2,declared_end=REQUEST,observed_end=REQUEST)
    assert decision([evidence(),newer]).effective_as_of==D
    assert not decision([newer,evidence(method=None)]).shadow_allowed
    assert not decision([evidence(),replace(newer,declared_start=date(2026,10,7))]).shadow_allowed
    assert not decision([]).shadow_allowed
    assert not decision(requested=date(2026,6,30)).shadow_allowed

def test_real_upload_preserves_metadata_after_cleanup(tmp_path,monkeypatch):
    from app.services import imports
    from app.routers import imports as routes
    from app.database import get_db
    from app.services.settings_auth import require_settings_admin
    async def run():
        async with database() as (engine,factory):
            monkeypatch.setattr(imports,'SessionLocal',factory)
            async with factory() as db:
                await db.execute(text("SELECT setval(pg_get_serial_sequence('import_batches','id'),1,true)"))
                await db.commit()
            monkeypatch.setattr(routes,'process_import',imports._process_import_with_source)
            monkeypatch.setattr(routes,'import_temp_dir',lambda:tmp_path)
            app=FastAPI();app.include_router(routes.router,prefix='/api')
            async def session():
                async with factory() as db:yield db
            app.dependency_overrides[get_db]=session;app.dependency_overrides[require_settings_admin]=lambda:None
            body='<table><tr><td>за период с 01.07.26 по 06.10.26</td></tr>'+HEADER+'<tr><td>05.10.26</td><td>РН-1</td><td>Козловская</td><td>100</td></tr></table>'
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
                r=await client.post('/api/imports',files={'files':('sales.html',body.encode(),'text/html')});assert r.status_code==202,r.text
                data=(await client.get('/api/imports/'+str(r.json()[0]['id']))).json()
                assert data['status']=='completed' and data['error_rows']==0 and data['added_rows']==1
                assert data['declared_period_start']=='2026-07-01' and data['declared_period_end']=='2026-10-06'
                assert data['period_start']==data['period_end']=='2026-10-05' and data['source_period_method']==METHOD
            assert not list(tmp_path.iterdir())
    asyncio.run(run())

def test_resume_new_revision_in_evaluate_preserves_frozen_date(monkeypatch):
    monkeypatch.setattr(worker,'last_closed_day',lambda:REQUEST)
    async def run():
        async with database() as (engine,factory):
            await seed(factory,clients=3)
            async with factory() as db:
                await db.execute(update(ImportBatch).values(declared_period_end=ANCHOR+timedelta(days=8)));await db.commit()
            requested=ANCHOR+timedelta(days=15)
            while True:
                first=await worker.run_batch(factory,engine,as_of=requested,scan_size=500,client_size=1,configuration_enabled=True)
                if first['phase']=='evaluate':break
            async with factory() as db:
                old=await db.get(AnalyticsSignalRun,first['run_id']);frozen=old.metadata_json['source_decision']
                assert old.as_of==requested
                await db.execute(update(ImportBatch).values(declared_period_end=requested))
                await db.execute(update(SeasonalityState).values(source_revision=1));await db.commit()
            assert (await worker.run_batch(factory,engine,as_of=requested,configuration_enabled=True))['status']=='SUPERSEDED'
            async with factory() as db:
                old=await db.get(AnalyticsSignalRun,first['run_id']);assert old.metadata_json['source_decision']==frozen
                assert old.as_of==requested and (await db.get(AnalyticsSignalState,1)).published_run_id is None
            fresh=await finish(factory,engine,requested)
            assert fresh['run_id']!=first['run_id'] and fresh['source_decision']['effective_as_of']==requested.isoformat()
            assert fresh['source_decision']['source_revision']==1
    asyncio.run(run())

def test_backwards_safe_date_blocks_without_rewriting_lifecycle(monkeypatch):
    monkeypatch.setattr(worker,'last_closed_day',lambda:REQUEST)
    async def run():
        async with database() as (engine,factory):
            await seed(factory);first=await finish(factory,engine,ANCHOR+timedelta(days=15))
            async with factory() as db:
                old=await db.scalar(select(AnalyticsSignal));sid,rev=old.id,old.revision
                await db.execute(update(ImportBatch).values(declared_period_end=ANCHOR+timedelta(days=8)))
                await db.execute(update(SeasonalityState).values(source_revision=1));await db.commit()
            blocked=await worker.run_batch(factory,engine,as_of=ANCHOR+timedelta(days=8),configuration_enabled=True)
            assert blocked['status']=='BLOCKED' and 'EFFECTIVE_DATE_PRECEDES_PUBLISHED_RUN' in blocked['source_decision']['reason_codes']
            async with factory() as db:
                assert (await db.get(AnalyticsSignalState,1)).published_run_id==first['run_id']
                assert (await db.get(AnalyticsSignal,sid)).revision==rev
                assert await db.scalar(select(func.count()).select_from(AnalyticsClientEvaluation).where(AnalyticsClientEvaluation.run_id==blocked['run_id']))==0
    asyncio.run(run())

def test_aviatorov_wholesale_corporate_filter_is_applied_before_features(monkeypatch):
    from app.services import analytics_scope
    from app.models import Sale
    async def buyer_types():return {'client 0':'Оптовик','client 1':'Корпоративный','client 2':'Розница'}
    monkeypatch.setattr(analytics_scope,'get_client_buyer_types',buyer_types)
    monkeypatch.setattr(worker,'load_analytics_scope',analytics_scope.load_analytics_scope)
    async def run():
        async with database() as (engine,factory):
            await seed(factory,clients=3)
            async with factory() as db:
                await db.execute(update(Sale).values(department='Авиаторов'))
                db.add(Sale(id=500,sale_date=ANCHOR+timedelta(days=7),document_number='wrong-store',client='client 0',
                    department='Козловская',total_amount=1000,fingerprint='wrong-store',import_id=1))
                await db.commit()
            result=await finish(factory,engine,ANCHOR+timedelta(days=8))
            async with factory() as db:
                rows=(await db.scalars(select(AnalyticsClientEvaluation).where(AnalyticsClientEvaluation.run_id==result['run_id']))).all()
                assert {r.client_key for r in rows}=={'client 0','client 1'}
                for row in rows:
                    assert row.data['features']['last_purchase_date']==ANCHOR.isoformat()
                    assert row.data['features']['days_without_purchase']==8
                    assert row.data['features']['distinct_purchase_days']==21
                run=await db.get(AnalyticsSignalRun,result['run_id'])
                assert run.metadata_json['scope']['department']=='Авиаторов'
                assert run.metadata_json['scope']['buyer_types']==['Оптовик','Корпоративный']
    asyncio.run(run())


def test_missing_buyer_type_source_blocks_instead_of_using_all_clients(monkeypatch):
    from app.services.clients_vr import ClientsVrError
    async def unavailable():raise ClientsVrError('unavailable')
    monkeypatch.setattr(worker,'load_analytics_scope',unavailable)
    async def run():
        async with database() as (engine,factory):
            await seed(factory)
            result=await worker.run_batch(factory,engine,as_of=ANCHOR+timedelta(days=8),configuration_enabled=True)
            assert result['status']=='BLOCKED'
            assert 'BUYER_TYPE_SOURCE_UNAVAILABLE' in result['source_decision']['reason_codes']
            async with factory() as db:
                assert await db.scalar(select(func.count()).select_from(AnalyticsSignal))==0
    asyncio.run(run())

def test_other_department_file_is_not_evidence_for_aviatorov(monkeypatch):
    async def scope():return dict(department='Авиаторов',buyer_types=['Оптовик','Корпоративный'],
                                 client_keys=['client 0'],policy='aviatorov-wholesale-corporate/1')
    monkeypatch.setattr(worker,'load_analytics_scope',scope)
    async def run():
        async with database() as (engine,factory):
            await seed(factory)  # only Shop, no Авиаторов
            result=await worker.run_batch(factory,engine,as_of=ANCHOR+timedelta(days=8),configuration_enabled=True)
            assert result['status']=='BLOCKED'
            assert 'NO_SCOPED_STORED_SALES' in result['source_decision']['reason_codes']
            assert result['source_decision']['coverage_status']=='UNKNOWN'
            assert result['source_decision']['effective_as_of'] is None
    asyncio.run(run())


def test_postgres_old_policy_checkpoint_replaced_without_import_dates(monkeypatch):
    """Prepared only: opt-in disposable DB/schema required; never production."""
    from app.models import Sale, AnalyticsDeliveryPublication
    monkeypatch.setattr(worker, 'last_closed_day', lambda: REQUEST)
    async def scenario():
        async with database() as (engine, factory):
            await seed(factory)
            old_decision = decision(requested=ANCHOR+timedelta(days=8))
            async with factory() as db:
                scope = await worker.load_analytics_scope()
                control = await worker.state(db)
                old = await worker.begin_run(db, control, old_decision, scope)
                old_id, frozen = old.id, old.metadata_json.copy()
                await db.execute(update(ImportBatch).values(declared_period_start=None,
                    declared_period_end=None, source_period_method=None))
                db.add(Sale(id=500, sale_date=REQUEST+timedelta(days=1), document_number='future',
                    client='client 0', department='Shop', total_amount=1000, fingerprint='future', import_id=1))
                await db.commit()
            retired = await worker.run_batch(factory, engine, as_of=REQUEST, configuration_enabled=True)
            assert retired['status'] == 'SUPERSEDED'
            async with factory() as db:
                old = await db.get(AnalyticsSignalRun, old_id)
                assert old.metadata_json == frozen and old.error_code == 'SOURCE_POLICY_CHANGED'
                assert await db.scalar(select(func.count()).select_from(AnalyticsDeliveryPublication)) == 0
            result = await finish(factory, engine, REQUEST+timedelta(days=1))
            assert result['source_decision']['effective_as_of'] == REQUEST.isoformat()
            assert result['source_decision']['coverage_status'] == 'UNKNOWN'
            assert result['source_decision']['accepted_data_through'] is None
            async with factory() as db:
                signal = await db.scalar(select(AnalyticsSignal))
                assert signal.metrics['last_purchase_date'] == ANCHOR.isoformat()
                assert signal.source_coverage_status == signal.source_freshness_status == 'UNKNOWN'
                assert signal.metrics['automation_allowed'] is False
                assert await db.scalar(select(func.count()).select_from(AnalyticsDeliveryPublication)) == 1
    asyncio.run(scenario())
