"""Read-only integration HTTP contracts; synthetic fixtures, no database connections."""
import asyncio
from datetime import date, datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI
from sqlalchemy.dialects import postgresql

from app.config import settings
from app.database import get_db
from app.models import AnalyticsSignalRun, AnalyticsSignalState
from app.routers.calltrack_integration import router
from app.routers.analytics_delivery import router as delivery_router
from app.services import calltrack_buyers
from app.services.clients_vr import ClientsVrError


class ReadOnlyDB:
    """Only read methods exist; SQL pagination is interpreted for synthetic rows."""
    def __init__(self, publications=(), changes=(), run=None):
        self.publications, self.changes, self.run = publications, changes, run
        self.reads = 0

    async def get(self, model, key):
        self.reads += 1
        if model is AnalyticsSignalState:
            return None
        if model is AnalyticsSignalRun and self.run and key == self.run.id:
            return self.run
        return None

    async def scalars(self, query):
        self.reads += 1
        sql = query.compile(dialect=postgresql.dialect())
        assert str(sql).startswith('SELECT ')
        params = sql.params
        if 'analytics_delivery_publications' in str(sql):
            rows = [r for r in self.publications if r.sequence > params['sequence_1']]
        else:
            rows = [r for r in self.changes if r.id > params['id_1']]
        return SimpleNamespace(all=lambda: rows[:params['param_1']])


async def http(monkeypatch, db, check):
    monkeypatch.setattr(settings, 'calltrack_integration_token', 'fixture-only-token')
    app = FastAPI()
    app.include_router(router, prefix='/api')
    app.include_router(delivery_router, prefix='/api')
    async def session():
        yield db
    app.dependency_overrides[get_db] = session
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://fixture',
                                headers={'Authorization': 'Bearer fixture-only-token'}) as client:
        await check(client)


def test_all_read_endpoints_require_auth_before_data_access(monkeypatch):
    db = ReadOnlyDB()
    async def check(client):
        for method, path in [('POST', p) for p in ('buyers','client-sales','client-insights','client-category-details','dashboard-sales')] + [('GET', p) for p in ('analytics/latest','analytics/publications','analytics/runs/missing/changes')]:
            for token in ('', 'Bearer wrong'):
                response = await client.request(method, '/api/integrations/calltrack/' + path,
                                                headers={'Authorization': token}, **({'json': {}} if method == 'POST' else {}))
                assert response.status_code == 401
        assert db.reads == 0
    asyncio.run(http(monkeypatch, db, check))


def test_buyers_empty_and_directory_failure_are_distinct(monkeypatch):
    class EmptyDB:
        async def scalar(self, query): return 0
        async def execute(self, query):
            return SimpleNamespace(mappings=lambda: SimpleNamespace(all=lambda: []))
    directory = AsyncMock(return_value={})
    monkeypatch.setattr(calltrack_buyers, 'get_client_managers', directory)
    async def check(client):
        url = '/api/integrations/calltrack/buyers'
        response = await client.post(url, json={'search':'Missing fixture', 'page':10, 'page_size':2})
        assert response.status_code == 200
        assert response.json() == {'items':[], 'total':0, 'page':1, 'page_size':2}
        directory.side_effect = ClientsVrError('unavailable')
        assert (await client.post(url,json={})).status_code == 503
        for body in ({'page':0}, {'page_size':101}, {'activity':'forged'}):
            assert (await client.post(url,json=body)).status_code == 422
    asyncio.run(http(monkeypatch, EmptyDB(), check))


def test_delivery_empty_and_missing_run_without_calculation(monkeypatch):
    async def check(client):
        root='/api/integrations/calltrack/analytics'
        assert (await client.get(root+'/latest')).json() == {'run':None,'mode':'SHADOW','automation_allowed':False}
        assert (await client.get(root+'/publications')).json() == {'items':[],'next_after':None,'automation_allowed':False}
        assert (await client.get(root+'/runs/missing/changes')).status_code == 404
        for suffix in ('/publications?after=-1', '/publications?page_size=101', '/runs/missing/changes?page_size=0'):
            assert (await client.get(root+suffix)).status_code == 422
    asyncio.run(http(monkeypatch, ReadOnlyDB(), check))


def test_delivery_cursor_replay_and_redaction_without_publication(monkeypatch):
    run=SimpleNamespace(id='fixture-run',status='SUCCEEDED',as_of=date(2026,10,6),source_revision=1,
                        finished_at=datetime(2026,10,7,tzinfo=timezone.utc),
                        metadata_json={'scope':{'policy':'fixture','client_keys':['private']},
                                       'source_decision':{'evidence':{'private':True}}})
    payload=dict(status='ACTIVE',family='INACTIVITY',signal_type='CHURN_RISK',identity_kind='normalized_name',
                 normalization_version=1,analytical_client_key='synthetic',score=80,confidence=.8,
                 anchor_purchase_date='2026-09-01',generator_version='inactivity/1',as_of='2026-10-06',
                 known_phones=['private'],metrics={'median_interval':5,'known_phones':['private']})
    rows=[SimpleNamespace(id=i,signal_id=f'signal-{i}',signal_revision=1,event_type='CREATED',payload=payload,
                          changed_at=run.finished_at) for i in (1,2,3)]
    pubs=[SimpleNamespace(sequence=i,run_id=f'run-{i}') for i in (1,2,3)]
    async def check(client):
        root='/api/integrations/calltrack/analytics'
        for path, ids in [('/publications', 'sequence'),('/runs/fixture-run/changes','signal_id')]:
            collected=[]; after=0
            while True:
                url=root+path+f'?page_size=1&after={after}'
                response=await client.get(url)
                assert response.status_code==200
                page=response.json()
                assert page == (await client.get(url)).json()
                assert page['automation_allowed'] is False
                collected.extend(item[ids] for item in page['items'])
                if page['next_after'] is None: break
                assert page['next_after'] > after
                after=page['next_after']
            assert len(collected)==len(set(collected))==3
        page=(await client.get(root+'/runs/fixture-run/changes')).json()
        for item in page['items']:
            assert item['mode']=='SHADOW' and item['automation_allowed'] is False
            assert 'known_phones' not in item and 'known_phones' not in item['metrics']
            assert 'client_keys' not in item['run']['scope']
            assert 'evidence' not in item['run']['source_decision']
    asyncio.run(http(monkeypatch, ReadOnlyDB(pubs,rows,run), check))


def test_category_http_matches_synthetic_sale_lines_and_empty_results(monkeypatch):
    """Real aggregation SQL on SQLite; PostgreSQL isolation/streaming remain unverified."""
    from decimal import Decimal
    from sqlalchemy import create_engine, text
    from app.services import calltrack_category_details as service
    engine=create_engine('sqlite://')
    catalog=AsyncMock(side_effect=lambda rows, db: {r['key']:{'catalog_category':'Fixture category','section':'Fixture section','product_id':1} for r in rows})
    monkeypatch.setattr(service,'local_catalog',catalog)
    class Stream:
        def __init__(self, rows): self.rows=rows
        def mappings(self): return self
        async def partitions(self, size):
            for index in range(0,len(self.rows),size): yield self.rows[index:index+size]
    async def run():
        with engine.begin() as conn:
            conn.execute(text('CREATE TABLE sales(id INTEGER, client TEXT)'))
            conn.execute(text('CREATE TABLE sale_items(sale_id INTEGER, code TEXT, article TEXT, name TEXT, quantity NUMERIC, actual_price NUMERIC)'))
            conn.execute(text("INSERT INTO sales VALUES (1,'Fixture buyer'),(2,'Fixture buyer'),(3,'Other buyer')"))
            conn.execute(text("INSERT INTO sale_items VALUES (1,'P','A','Product',2,10.25),(2,'P','A','Product',-1,10.25),(3,'P','A','Product',99,10.25)"))
            class DB:
                async def connection(self, execution_options):
                    assert execution_options == {'isolation_level':'REPEATABLE READ'}
                async def stream(self, query):
                    return Stream(conn.execute(query).mappings().all())
            async def check(client):
                url='/api/integrations/calltrack/client-category-details'
                body={'names':['Fixture buyer','Fixture buyer'],'category':'Fixture category'}
                response=await client.post(url,json=body)
                assert response.status_code==200, response.text
                data=response.json()
                assert data['total_count']==1 and data['level']=='subcategories'
                assert Decimal(data['parent_revenue'])==Decimal('10.25')
                assert Decimal(data['parent_quantity'])==1
                body['subcategory']='Fixture section'
                data=(await client.post(url,json=body)).json()
                assert data['level']=='products' and data['items'][0]['product_id']==1
                assert Decimal(data['items'][0]['revenue'])==Decimal('10.25')
                body['names']=['Missing buyer']
                data=(await client.post(url,json=body)).json()
                assert data['items']==[] and data['total_count']==0
                assert Decimal(data['parent_revenue'])==0
            await http(monkeypatch,DB(),check)
        engine.dispose()
    asyncio.run(run())


def test_conflicting_sale_identity_returns_422_not_another_clients_sale(monkeypatch):
    from decimal import Decimal
    from app.services import calltrack_integration as service
    monkeypatch.setattr(service,'get_cached_client_managers',lambda:{})
    monkeypatch.setattr(service,'find_client_sales',AsyncMock(return_value=[SimpleNamespace(
        id=1,phone='+79991234567',client='Fixture Beta',sale_date=date(2026,9,1),
        document_number='fixture',department='Fixture',total_amount=Decimal('10'))]))
    async def check(client):
        response=await client.post('/api/integrations/calltrack/client-sales',json={
            'clients':[{'key':'alpha','phone':'+79991234567','name':'Fixture Alpha'},
                       {'key':'beta','name':'Fixture Beta'}],
            'date_from':'2026-09-01','date_to':'2026-09-30'})
        assert response.status_code==422
        assert 'items' not in response.json()
    asyncio.run(http(monkeypatch,ReadOnlyDB(),check))
