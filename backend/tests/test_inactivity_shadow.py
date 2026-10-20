"""Algorithm and PostgreSQL integration tests; never connect to app/production DB."""
import asyncio
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
import os
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy import event, func, insert, select, text, update
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.models import (Base, Sale, ImportBatch, SeasonalityState, AnalyticsSignalRun, AnalyticsSignalState,
    AnalyticsSignal, AnalyticsSignalChange, AnalyticsPurchaseDay, AnalyticsObservedPhone, AnalyticsClientEvaluation)
from app.database import get_db
from app.routers.analytics_signals import router
from app.services.settings_auth import require_settings_admin
from app.services.client_identity import normalize_client_name, analytical_name_sql
from app.services.client_features import interval_statistics, load_inactivity_features
from app.services.inactivity import evaluate
from app.services.analytics_signal_worker import run_batch, WORKER_LOCK, record_failure
from app.services.seasonality_worker import advisory, SeasonalityBusy

ANCHOR = date(2026, 8, 1)


def features(gaps=None, **overrides):
    start = ANCHOR-timedelta(days=100)
    days = [start]
    for gap in gaps or [5]*20:days.append(days[-1]+timedelta(days=gap))
    f = dict(interval_statistics(days), history_span_days=(days[-1]-days[0]).days,
        first_purchase_date=days[0], last_purchase_date=days[-1], client_key="alpha",
        display_name="Alpha", reference_revenue_180=Decimal("180000"), average_check=Decimal("1000"), known_phones=[])
    return f | overrides


@pytest.mark.parametrize("left,right,same", [
    ("  ООО  АЛЬФА\t", "ооо альфа", True), ("ООО\u00a0АЛЬФА", "ооо альфа", True),
    ("Альфа", "Альфа Плюс", False), ("ООО Альфа", "Альфа ООО", False),
    ("Ёлка", "Елка", False), ("", " \t", True)])
def test_versioned_normalization(left, right, same):
    assert (normalize_client_name(left, version=1) == normalize_client_name(right, version=1)) == same
    assert normalize_client_name("  A  B ")=="a  b"  # legacy path is unchanged


def test_distinct_days_mean_median_outlier_and_single_gap():
    days = [ANCHOR]
    for gap in [5,5,6,5,5,31,5]:days.append(days[-1]+timedelta(days=gap))
    stats = interval_statistics(days+[days[0]]*5)
    assert stats["distinct_purchase_days"]==8 and stats["gap_count"]==7
    assert stats["median_interval"]==5 and stats["mean_interval"]==62/7
    assert interval_statistics([ANCHOR])["median_interval"] is None
    assert interval_statistics([ANCHOR, ANCHOR+timedelta(days=5)])["q25"]==5


@pytest.mark.parametrize("overrides,reason", [
    ({"distinct_purchase_days":7},"INSUFFICIENT_HISTORY"),
    ({"gap_count":6},"INSUFFICIENT_HISTORY"), ({"history_span_days":55},"INSUFFICIENT_HISTORY"),
    ({"median_interval":0},"INSUFFICIENT_HISTORY"),
    ({"median_interval":10,"mad":8},"IRREGULAR_PURCHASE_PATTERN"),
    ({"median_interval":61,"mad":0},"INTERVAL_TOO_LONG"),
    ({"last_purchase_date":None},"NO_VALID_PURCHASE_DAYS")])
def test_eligibility(overrides,reason):
    result=evaluate(features(**overrides), ANCHOR+timedelta(days=8))
    assert not result["eligible"] and result["reason"]==reason and result["signal_type"] is None


def test_thresholds_unknown_coverage_score_explanation_and_no_phone():
    normal=evaluate(features(),ANCHOR+timedelta(days=7))
    overdue=evaluate(features(),ANCHOR+timedelta(days=8))
    churn=evaluate(features(),ANCHOR+timedelta(days=15))
    assert normal["signal_type"] is None
    assert overdue["signal_type"]=="PURCHASE_OVERDUE"
    assert churn["signal_type"]=="CHURN_RISK"
    assert overdue["metrics"]["overdue_threshold"]==8 and churn["metrics"]["churn_threshold"]==15
    assert churn["metrics"]["confidence_components"]["coverage"]==0
    assert churn["confidence"]<=.8 and churn["metrics"]["automation_allowed"] is False
    assert churn["score"]>overdue["score"]
    assert evaluate(features(), ANCHOR+timedelta(days=15), freshness="STALE")["reason"]=="SOURCE_UNRELIABLE"


@asynccontextmanager
async def database():
    url=os.environ.get("ANALYTICS_SHADOW_TEST_DATABASE_URL")
    if not url:pytest.skip("ANALYTICS_SHADOW_TEST_DATABASE_URL required")
    parsed=make_url(url)
    assert parsed.host in {"127.0.0.1","localhost"} and parsed.database=="analytics_shadow_test"
    schema="shadow_"+uuid4().hex
    admin=create_async_engine(url)
    async with admin.begin() as c:await c.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine=create_async_engine(url, connect_args={"server_settings":{"search_path":schema}})
    factory=async_sessionmaker(engine,expire_on_commit=False)
    try:
        async with engine.begin() as c:await c.run_sync(Base.metadata.create_all)
        async with factory() as db:
            db.add_all([ImportBatch(id=1,filename="synthetic",file_size=0,status="completed",finished_at=datetime.now(timezone.utc)),
                        SeasonalityState(id=1),AnalyticsSignalState(id=1,enabled=True)])
            await db.commit()
        yield engine,factory
    finally:
        await engine.dispose()
        async with admin.begin() as c:await c.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        await admin.dispose()


async def seed(factory, clients=1, *, phones=False):
    rows=[]
    for i in range(clients):
        for day in range(21):
            rows.append(dict(id=len(rows)+1,sale_date=ANCHOR-timedelta(days=100-day*5),
                document_number=str(len(rows)),client=(f" Client {i} " if day%2 else f"CLIENT  {i}"),
                department="Shop",total_amount=Decimal("1000"),fingerprint=str(len(rows)),import_id=1,
                phone=f"+7900000{day:04}" if phones else None))
    async with factory() as db:
        await db.execute(insert(Sale),rows)
        await db.commit()


async def finish(factory,engine,as_of,**options):
    for _ in range(1000):
        r=await run_batch(factory,engine,as_of=as_of,configuration_enabled=True,**options)
        if r["status"]=="SUCCEEDED":return r
        assert r["status"]=="RUNNING",r
    raise AssertionError("Worker did not finish")


def test_postgres_lifecycle_resume_no_duplicates_multiple_phones_and_report():
    async def run():
        async with database() as (engine,factory):
            await seed(factory,phones=True)
            at=ANCHOR+timedelta(days=8)
            first=await run_batch(factory,engine,as_of=at,scan_size=8,configuration_enabled=True)
            assert first["sale_cursor"]==8
            # A new connection/factory resumes the committed checkpoint.
            await record_failure(factory,"InjectedTestFailure")
            result=await finish(async_sessionmaker(engine,expire_on_commit=False),engine,at,scan_size=8)
            async with factory() as db:
                s=(await db.scalars(select(AnalyticsSignal))).one()
                original=s.id
                assert s.signal_type=="PURCHASE_OVERDUE" and s.revision==1
                assert len(s.known_phones)==10 and s.metrics["distinct_purchase_days"]==21
                assert s.source_coverage_status=="UNKNOWN" and s.mode=="SHADOW"
                assert await db.scalar(select(func.count()).select_from(AnalyticsObservedPhone))==10
            await finish(factory,engine,at)
            async with factory() as db:
                assert await db.scalar(select(func.count()).select_from(AnalyticsSignalChange))==1
            await finish(factory,engine,ANCHOR+timedelta(days=15))
            async with factory() as db:
                s=await db.get(AnalyticsSignal,original)
                assert s.signal_type=="CHURN_RISK" and s.revision==2
                assert (await db.scalars(select(AnalyticsSignalChange.event_type).order_by(AnalyticsSignalChange.id))).all()==["CREATED","ESCALATED"]
                db.add(Sale(id=100,sale_date=ANCHOR+timedelta(days=16),document_number="return",client="client 0",
                    department="Shop",total_amount=1000,fingerprint="new",import_id=1))
                await db.execute(update(SeasonalityState).values(source_revision=SeasonalityState.source_revision+1))
                await db.commit()
            await finish(factory,engine,ANCHOR+timedelta(days=16))
            async with factory() as db:
                s=await db.get(AnalyticsSignal,original)
                assert s.status=="RESOLVED" and s.resolution_reason=="PURCHASE_OCCURRED" and s.resolved_at
            await finish(factory,engine,ANCHOR+timedelta(days=24))
            async with factory() as db:
                signals=(await db.scalars(select(AnalyticsSignal).order_by(AnalyticsSignal.episode_no))).all()
                assert len(signals)==2 and signals[1].id!=original and signals[1].episode_no==2
            app=FastAPI();app.include_router(router,prefix="/api")
            async def session():
                async with factory() as db:yield db
            app.dependency_overrides[get_db]=session
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url="http://test") as client:
                assert (await client.get('/api/reports/analytics-signals')).status_code in {401,503}
                app.dependency_overrides[require_settings_admin]=lambda:None
                response=await client.get('/api/reports/analytics-signals',params={'status':'ACTIVE','page_size':1})
                assert response.status_code==200,response.text
                body=response.json()
                assert body['total']==1 and len(body['items'])==1 and body['automation_allowed'] is False
                assert body['run']['coverage_status']=='UNKNOWN' and body['run']['error_count']==0
                assert (await client.get('/api/reports/analytics-signals',params={'min_score':101})).status_code==422
            print('LIFECYCLE',original,result)
    asyncio.run(run())


def test_postgres_normalization_features_and_rfm_mean_compatibility():
    async def run():
        async with database() as (engine,factory):
            await seed(factory)
            async with factory() as db:
                for name in [' ООО\u00a0  АЛЬФА\t','Alpha  Plus','ЁЛКА','Straße']:
                    actual=await db.scalar(select(analytical_name_sql(text(':name'))),{'name':name})
                    assert actual==normalize_client_name(name,version=1)
                # Extra check on the same day and a zero check on a later day.
                await db.execute(insert(Sale),[
                    dict(id=50,sale_date=ANCHOR,document_number='50',client='client 0',department='Shop',total_amount=500,fingerprint='50',import_id=1),
                    dict(id=51,sale_date=ANCHOR+timedelta(days=1),document_number='51',client='client 0',department='Shop',total_amount=0,fingerprint='51',import_id=1)])
                await db.commit()
            result=await finish(factory,engine,ANCHOR+timedelta(days=8))
            async with factory() as db:
                f=(await load_inactivity_features(db,result['run_id'],['client 0'],ANCHOR+timedelta(days=8)))[0]
                assert f['distinct_purchase_days']==21 and f['gap_count']==20 and f['median_interval']==5
                assert f['mean_interval']==5 and f['last_purchase_date']==ANCHOR
                assert f['revenue']==21500 and f['frequency']==23
                from app.services.rfm_data import rfm_rows
                legacy=await rfm_rows(db,ANCHOR-timedelta(days=100),ANCHOR+timedelta(days=8),None,include_products=False)
                assert sum(r['monetary'] for r in legacy)==21500
                # Old RFM keys are intentionally NOT collapsed by this feature change.
                assert any(r['client_key']=='client  0' for r in legacy)
    asyncio.run(run())


def test_postgres_disabled_superseded_and_leadership():
    async def run():
        async with database() as (engine,factory):
            await seed(factory)
            assert (await run_batch(factory,engine,configuration_enabled=False))['status']=='DISABLED'
            at=ANCHOR+timedelta(days=8)
            await run_batch(factory,engine,as_of=at,scan_size=5,configuration_enabled=True)
            async with factory() as db:
                await db.execute(update(SeasonalityState).values(source_revision=1));await db.commit()
            result=await run_batch(factory,engine,as_of=at,configuration_enabled=True)
            assert result['status']=='SUPERSEDED'
            async with factory() as db:assert await db.scalar(select(func.count()).select_from(AnalyticsSignal))==0
            async with advisory(engine,key=WORKER_LOCK):
                with pytest.raises(SeasonalityBusy):
                    async with advisory(engine,key=WORKER_LOCK):pass
    asyncio.run(run())


def test_postgres_feature_query_count_is_constant_for_client_batch():
    async def run():
        async with database() as (engine,factory):
            await seed(factory,clients=40)
            result=await finish(factory,engine,ANCHOR+timedelta(days=8))
            statements=[]
            def trace(conn,cursor,statement,parameters,context,executemany):statements.append(statement)
            event.listen(engine.sync_engine,'before_cursor_execute',trace)
            async with factory() as db:
                assert len(await load_inactivity_features(db,result['run_id'],[f'client {i}' for i in range(40)],ANCHOR+timedelta(days=8)))==40
            event.remove(engine.sync_engine,'before_cursor_execute',trace)
            assert len(statements)==1
    asyncio.run(run())


def test_postgres_same_phone_different_clients_no_change_events_and_unhealthy_source():
    async def run():
        async with database() as (engine,factory):
            await seed(factory,clients=2)
            async with factory() as db:
                await db.execute(update(Sale).values(phone='+79991234567'))
                await db.commit()
            at=ANCHOR+timedelta(days=8)
            await finish(factory,engine,at)
            async with factory() as db:
                signals=(await db.scalars(select(AnalyticsSignal))).all()
                assert len(signals)==2 and {s.analytical_client_key for s in signals}=={'client 0','client 1'}
                assert all(s.known_phones==['+79991234567'] for s in signals)
                assert await db.scalar(select(func.count()).select_from(AnalyticsSignalChange))==2
                # Force a new run with identical features: inspection must not emit UPDATED.
                await db.execute(update(SeasonalityState).values(source_revision=1));await db.commit()
            await finish(factory,engine,at)
            async with factory() as db:
                assert await db.scalar(select(func.count()).select_from(AnalyticsSignalChange))==2
                db.add(ImportBatch(id=2,filename='failed',file_size=0,status='failed',error_rows=1))
                await db.commit()
            result=await finish(factory,engine,at)
            async with factory() as db:
                signals=(await db.scalars(select(AnalyticsSignal))).all()
                assert all(s.status=='SUSPENDED' and s.resolution_reason=='SOURCE_UNRELIABLE' for s in signals)
                row=(await db.scalars(select(AnalyticsClientEvaluation).where(AnalyticsClientEvaluation.run_id==result['run_id']))).first()
                assert row.reason=='SOURCE_UNRELIABLE' and not row.eligible
                # Restore delivery: resume same episode, not a new one.
                await db.execute(update(ImportBatch).where(ImportBatch.id==2).values(status='completed',error_rows=0,finished_at=datetime.now(timezone.utc)))
                await db.commit()
            await finish(factory,engine,at)
            async with factory() as db:
                assert await db.scalar(select(func.count()).select_from(AnalyticsSignal))==2
                assert (await db.scalars(select(AnalyticsSignal.status))).all()==['ACTIVE','ACTIVE']
                assert 'RESUMED' in (await db.scalars(select(AnalyticsSignalChange.event_type))).all()
    asyncio.run(run())


def test_postgres_report_keyset_diagnostics_missing_names_and_no_eager_calculation():
    async def run():
        async with database() as (engine,factory):
            await seed(factory,clients=4)
            async with factory() as db:
                await db.execute(insert(Sale),[
                    dict(id=500,sale_date=ANCHOR,document_number='empty',client=' \t ',department='S',total_amount=1,fingerprint='empty',import_id=1),
                    dict(id=501,sale_date=ANCHOR,document_number='new',client='One-time',department='S',total_amount=1,fingerprint='once',import_id=1),
                    dict(id=502,sale_date=ANCHOR,document_number='zero',client='Zero',department='S',total_amount=0,fingerprint='zero',import_id=1)])
                await db.commit()
            result=await finish(factory,engine,ANCHOR+timedelta(days=8))
            app=FastAPI();app.include_router(router,prefix='/api')
            async def session():
                async with factory() as db:yield db
            app.dependency_overrides[get_db]=session
            app.dependency_overrides[require_settings_admin]=lambda:None
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
                first=(await client.get('/api/reports/analytics-signals',params={'page_size':2})).json()
                assert first['total']==6 and first['run']['diagnostics']['missing_empty_name']==1
                assert first['run']['diagnostics']['eligible']==4
                assert first['run']['diagnostics']['INSUFFICIENT_HISTORY']==1
                assert first['run']['diagnostics']['NO_VALID_PURCHASE_DAYS']==1
                second=(await client.get('/api/reports/analytics-signals',params={'page_size':2,'after':first['next_after']})).json()
                assert not set(x['analytical_client_key'] for x in first['items'])&set(x['analytical_client_key'] for x in second['items'])
                assert all(r['features']['known_phones']==[] for r in first['items'])
                assert all(r['signal_type']=='PURCHASE_OVERDUE' for r in first['items'])
                assert (await client.get('/api/reports/analytics-signals',params={'after':first['next_after'],'status':'ACTIVE'})).status_code==409
                assert (await client.get('/api/reports/analytics-signals',params={'after':'garbage'})).status_code==422
                before=first['run']['id']
                assert (await client.post('/api/reports/analytics-signals')).status_code==405
                assert (await client.get('/api/reports/analytics-signals')).json()['run']['id']==before
    asyncio.run(run())
