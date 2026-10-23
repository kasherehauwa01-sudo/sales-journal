from datetime import date, timedelta
import pytest
from app.services import inactivity, churn_pilot
from app.services.analytics_scope import SCOPE_POLICY

@pytest.mark.parametrize('days,expected',[(44,None),(45,'CHURN_RISK'),(46,'CHURN_RISK')])
def test_fixed_boundary(days,expected):
    day=date(2026,10,23)
    f=dict(distinct_purchase_days=3,last_purchase_date=day-timedelta(days=days),gap_count=2,
           history_span_days=2,median_interval=1,q75=1,mad=0)
    r=churn_pilot.evaluate(f,day)
    assert r['eligible'] and r['signal_type']==expected
    assert r['metrics']['automation_allowed'] is False
    assert r['metrics']['churn_threshold']==45

@pytest.mark.parametrize('count,eligible',[(0,False),(1,False),(2,False),(3,True)])
def test_history(count,eligible):
    r=churn_pilot.evaluate(dict(distinct_purchase_days=count,last_purchase_date=date(2026,8,1)),date(2026,10,23))
    assert r['eligible']==eligible
    assert r['metrics']['coverage']=='UNKNOWN'

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
