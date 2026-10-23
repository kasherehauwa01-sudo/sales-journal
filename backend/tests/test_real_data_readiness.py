"""Synthetic PostgreSQL checks for real-data audit hypotheses; no production I/O."""
import asyncio
import time
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

from sqlalchemy import func, insert, select
from app.models import Sale, ImportBatch, ImportError, AnalyticsSignal, AnalyticsSignalRun
from app.services import analytics_scope, imports
from app.services.rfm_data import rfm_rows
from test_inactivity_shadow import database, finish


def test_store_inactivity_does_not_use_global_rfm_or_other_buyer_groups(monkeypatch):
    monkeypatch.setattr('app.services.analytics_signal_worker.load_analytics_scope', analytics_scope.load_analytics_scope)
    monkeypatch.setattr(analytics_scope, 'get_analytics_buyer_type_names', AsyncMock(return_value={
        'Оптовик':['Fixture Alpha','Ambiguous'], 'Корпоративный':['Ambiguous']}))
    async def scenario():
        async with database() as (engine, factory):
            day=date(2026,10,6)
            rows=[]
            for name in ['Fixture Alpha','Retail','Ambiguous']:
                for n in range(21):
                    rows.append(dict(id=len(rows)+1,sale_date=day-timedelta(days=45+n*5),
                        document_number=str(len(rows)),client=name,department='Авиаторов',
                        total_amount=1000,fingerprint='fixture-'+str(len(rows)),import_id=1))
            rows.append(dict(id=len(rows)+1,sale_date=day,document_number='other-store',client='Fixture Alpha',
                        department='Другой',total_amount=1000,fingerprint='other-store',import_id=1))
            async with factory() as db:
                await db.execute(insert(Sale),rows);await db.commit()
            result=await finish(factory,engine,day)
            async with factory() as db:
                global_rows=await rfm_rows(db,day-timedelta(days=364),day,None,include_products=False)
                assert next(r for r in global_rows if r['client_key']=='fixture alpha')['last_purchase']==day
                signal=(await db.scalars(select(AnalyticsSignal))).one()
                assert signal.analytical_client_key=='fixture alpha' and signal.signal_type=='CHURN_RISK'
                assert signal.anchor_purchase_date==day-timedelta(days=45)
                run=await db.get(AnalyticsSignalRun,result['run_id'])
                assert run.metadata_json['scope']['department']=='Авиаторов'
                assert run.metadata_json['source_decision']['coverage_status']=='UNKNOWN'
                assert run.metadata_json['source_decision']['business_freshness']=='UNKNOWN'
                assert run.metadata_json['automation_allowed'] is False
    asyncio.run(scenario())


def test_repeated_import_errors_do_not_count_unique_lost_sales_and_header_failure_is_zero_rows(monkeypatch):
    monkeypatch.setattr(imports, 'extract_declared_period', lambda path: SimpleNamespace(start=None,end=None,method=None))
    valid=dict(sale_date='01.10.2026',document_number='Fixture-1',department='Авиаторов',total_amount='100')
    invalid={**valid,'document_number':''}
    skipped={**valid,'document_number':'ВЗВ-fixture'}
    other={**valid,'department':'Другой'}
    monkeypatch.setattr(imports,'read_sales',lambda path:('synthetic',iter(enumerate([valid,invalid,skipped,other],2))))
    async def scenario():
        async with database() as (engine,factory):
            monkeypatch.setattr(imports,'SessionLocal',factory)
            async with factory() as db:
                for i in (2,3,4):db.add(ImportBatch(id=i,filename='Aviatorov-fixture.html',stored_path='synthetic-only.html',file_size=0))
                await db.commit()
            for i in (2,3):await imports._process_import(i,time.monotonic())
            async with factory() as db:
                batches=[await db.get(ImportBatch,i) for i in (2,3)]
                assert all((b.status,b.total_rows,b.processed_rows,b.skipped_rows,b.error_rows)==('completed',4,4,2,1) for b in batches)
                assert [b.added_rows for b in batches]==[1,0]
                assert [b.duplicate_rows for b in batches]==[0,1]
                assert await db.scalar(select(func.count()).select_from(Sale))==1
                assert await db.scalar(select(func.count()).select_from(ImportError))==2
                # Missing customer/phone are permitted import values, not mandatory-field errors.
                sale=(await db.scalars(select(Sale))).one();assert sale.client is None and sale.phone is None
            def header_failure(path):raise ValueError('Synthetic header not found')
            monkeypatch.setattr(imports,'read_sales',header_failure)
            await imports._process_import(4,time.monotonic())
            async with factory() as db:
                batch=await db.get(ImportBatch,4)
                assert (batch.status,batch.total_rows,batch.processed_rows,batch.error_rows)==('failed',0,0,0)
                assert await db.scalar(select(func.count()).select_from(ImportError).where(ImportError.import_id==4))==0
    asyncio.run(scenario())
