"""Synthetic sales -> production evaluator/reconciliation -> production export.

SQLite source aggregation and captured ORM writes replace PostgreSQL persistence.
Optional fixture is consumed by the isolated PHP test; SHADOW is never promoted here.
"""
import asyncio
import json
import os
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from sqlalchemy import create_engine, text

from app.models import AnalyticsSignalRun, AnalyticsSignalChange
from app.routers.analytics_delivery import changes
from app.services import analytics_signal_store as store
from app.services.analytics_source import resolve_stored_sales_source
from app.services.client_features import interval_statistics


def test_synthetic_sales_reconciliation_export_stays_shadow(monkeypatch):
    day=date(2026,10,19)
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None): return datetime(2026,10,20,10,tzinfo=timezone.utc)
    monkeypatch.setattr(store,'datetime',Clock)
    engine=create_engine('sqlite://')
    with engine.begin() as db:
        db.execute(text('CREATE TABLE sales(sale_date TEXT, amount NUMERIC)'))
        dates=[day-timedelta(days=30+7*i) for i in range(16)]
        db.execute(text('INSERT INTO sales VALUES (:day,:amount)'),[{'day':d.isoformat(),'amount':100} for d in dates])
        rows=db.execute(text('SELECT sale_date, sum(amount) FROM sales WHERE amount>0 GROUP BY sale_date ORDER BY sale_date')).all()
    engine.dispose()
    days=[date.fromisoformat(row[0]) for row in rows]
    f=dict(interval_statistics(days),client_key='synthetic customer',display_name='Synthetic Customer',
           last_purchase_date=max(days),first_purchase_date=min(days),history_span_days=(max(days)-min(days)).days,
           revenue=Decimal('1600'),reference_revenue_180=Decimal('1600'),frequency=16,average_check=Decimal('100'),known_phones=[])
    decision=resolve_stored_sales_source(day,last_closed_business_day=day,source_revision=7,
                                         timezone='Europe/Moscow',has_sales=True,upper_sale_id=16)
    run=AnalyticsSignalRun(id='fixture-run',as_of=day,status='RUNNING',source_revision=7,counters={},
        metadata_json=dict(source_decision=decision.to_json(),scope={'department':'Авиаторов','buyer_types':['Оптовик','Корпоративный'],
            'policy':'aviatorov-wholesale-corporate/2','client_keys':[f['client_key']]},mode='SHADOW',automation_allowed=False,
            coverage_status='UNKNOWN',freshness={'status':'UNKNOWN'}))
    class DB:
        def __init__(self): self.rows=[]
        async def scalars(self, query):
            rows=[row for row in self.rows if isinstance(row,AnalyticsSignalChange)]
            return SimpleNamespace(all=lambda:rows)
        def add(self, row): self.rows.append(row)
        async def flush(self): pass
        async def get(self, model, key): return run if model is AnalyticsSignalRun and key==run.id else None
    async def scenario():
        db=DB()
        await store.reconcile_batch(db,run,[f])
        event=next(row for row in db.rows if isinstance(row,AnalyticsSignalChange))
        assert event.event_type=='CREATED' and event.payload['signal_type']=='CHURN_RISK'
        event.id=1
        run.status='SUCCEEDED';run.finished_at=Clock.now()
        result=await changes(run.id,after=0,page_size=50,db=db)
        exported=result['items'][0]
        assert exported['mode']=='SHADOW' and exported['automation_allowed'] is False
        assert exported['run']['source_decision']['coverage_status']=='UNKNOWN'
        assert exported['run']['source_decision']['business_freshness']=='UNKNOWN'
        assert exported['metrics']['median_interval']==7 and exported['metrics']['days_without_purchase']==30
        assert 'client_keys' not in exported['run']['scope']
        if target:=os.environ.get('SYNTHETIC_SIGNAL_EXPORT'):
            path=Path(target).resolve()
            assert path.is_relative_to('/workspace/scratch')
            path.write_text(json.dumps(exported,ensure_ascii=False))
    asyncio.run(scenario())


def test_current_policy_is_inactivity_not_volume_drop_or_rfm_change():
    from app.services.inactivity import evaluate
    day=date(2026,10,19)
    dates=[day-timedelta(days=5+7*i) for i in range(16)]
    base=dict(interval_statistics(dates),last_purchase_date=max(dates),history_span_days=105,
              reference_revenue_180=Decimal('1600'))
    # Reduced revenue alone is not a supported contact-trigger rule.
    assert evaluate(base,day)['signal_type'] is None
    assert evaluate(dict(base,reference_revenue_180=Decimal('1')),day)['signal_type'] is None
    # An RFM segment is not an input to INACTIVITY; it cannot invent a new signal family.
    assert evaluate(dict(base,segment='Потерянные'),day)['signal_type'] is None
    assert evaluate(dict(base,last_purchase_date=day-timedelta(days=11)),day)['signal_type']=='PURCHASE_OVERDUE'
    assert evaluate(dict(base,last_purchase_date=day-timedelta(days=30)),day)['signal_type']=='CHURN_RISK'
