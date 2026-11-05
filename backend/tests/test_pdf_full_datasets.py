"""Full export flags reuse normal analytics; no real DB/directory queries."""
import asyncio
from datetime import date
from unittest.mock import AsyncMock
import httpx
from fastapi import FastAPI
from app.database import get_db
from app.routers import rfm_report, product_reports, product_analytics, promotion_candidates_report


def call(router,path,payload=None):
    app=FastAPI();app.include_router(router)
    async def db(): yield AsyncMock()
    app.dependency_overrides[get_db]=db
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
            response=await (client.get(path) if payload is None else client.post(path,json=payload))
            assert response.status_code==200,response.text
            return response.json()
    return asyncio.run(run())


def test_rfm_full_includes_all_550_clients_preserving_kpi(monkeypatch):
    rows=[{'client':f'Клиент {i}','client_key':str(i),'segment':'Новые','r_score':5,'f_score':5,'m_score':5,'rfm_code':'555','monetary':10,'frequency':2,'recency_days':1} for i in range(550)]
    monkeypatch.setattr(rfm_report,'_dataset',AsyncMock(return_value=(rows,{},True)))
    path='/reports/rfm?date_from=2026-01-01&date_to=2026-10-30'
    normal=call(rfm_report.router,path);full=call(rfm_report.router,path+'&full=true')
    assert len(normal['clients']['items'])==50
    assert len(full['clients']['items'])==full['clients']['total']==550
    assert normal['kpi']==full['kpi'] and normal['segments']==full['segments']


def test_products_full_has_all_rows_and_same_values(monkeypatch):
    rows=[{'key':str(i),'revenue':i,'units':i} for i in range(550)]
    monkeypatch.setattr(product_reports,'_products',AsyncMock(side_effect=lambda *args:[dict(row) for row in rows]))
    body={'date_from':'2026-01-01','date_to':'2026-10-30','products':[{'code':'1','name':'Тестовый товар'}]}
    normal=call(product_reports.router,'/reports/product-sales/products',body)
    full=call(product_reports.router,'/reports/product-sales/products?full=true',body)
    assert len(normal['items'])==100 and len(full['items'])==full['total']==550
    assert normal['items']==full['items'][:100]


def test_product_analytics_full_does_not_change_summary_or_classification(monkeypatch):
    rows=[{'key':str(i),'revenue':i,'units':i} for i in range(550)]
    monkeypatch.setattr(product_analytics,'_dataset',AsyncMock(return_value=(rows,date(2025,1,1),date(2025,10,30),None)))
    monkeypatch.setattr(product_analytics,'summary',lambda _: {'revenue':sum(row['revenue'] for row in rows)})
    monkeypatch.setattr(product_analytics,'_unique_checks',AsyncMock(return_value=550))
    monkeypatch.setattr(product_analytics,'coverage',lambda _: {})
    body={'date_from':'2026-01-01','date_to':'2026-10-30','brands':['Тест']}
    normal=call(product_analytics.router,'/reports/product-analytics/report',body)
    full=call(product_analytics.router,'/reports/product-analytics/report?full=true',body)
    assert len(normal['items'])==20 and len(full['items'])==full['total']==550
    assert normal['items']==full['items'][:20] and normal['summary']==full['summary']


def test_promotion_full_retains_all_candidates_and_same_counters(monkeypatch):
    rows=[{'key':str(i),'status':'Кандидат','reasons':['Продажи падают']} for i in range(550)]
    monkeypatch.setattr(promotion_candidates_report,'_dataset',AsyncMock(return_value=(rows,True)))
    monkeypatch.setattr(promotion_candidates_report,'metadata',AsyncMock(return_value={}))
    body={'date_from':'2026-01-01','date_to':'2026-10-30'}
    normal=call(promotion_candidates_report.router,'/reports/promotion-candidates',body)
    full=call(promotion_candidates_report.router,'/reports/promotion-candidates?full=true',body)
    assert len(normal['items'])==300 and len(full['items'])==full['total']==550
    assert normal['summary']==full['summary'] and normal['items']==full['items'][:300]


def test_seasonality_full_uses_same_snapshot_filters_and_ignores_navigation_cursors(monkeypatch):
    from app.routers import seasonality_report
    from app.services.seasonality import identity
    from test_seasonality_worker import fixture, finish
    monkeypatch.setattr(seasonality_report,'today',lambda:date(2026,10,12))
    async def run():
        async with fixture() as (engine,factory):
            await finish(factory,engine)
            async with factory() as db:
                digest=identity('code:001')
                normal=await seasonality_report.history(digest,limit=5,db=db)
                full=await seasonality_report.history(digest,after_year=2024,after_month=12,limit=5,db=db,full=True)
                assert len(normal['months'])==5 and len(full['months'])==24
                assert full['next_cursor'] is None and normal['profile']==full['profile']
                selected=await seasonality_report.history(digest,years=[2025],limit=5,db=db,full=True)
                assert len(selected['months'])==12 and {m['year'] for m in selected['months']}=={2025}
                listing=await seasonality_report.products(after=digest,limit=1,db=db,full=True)
                assert len(listing['items'])==1 and listing['next_cursor'] is None
    asyncio.run(run())
