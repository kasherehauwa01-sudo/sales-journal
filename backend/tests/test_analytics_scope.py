"""Synthetic contract tests: no real HTTP, PostgreSQL or ClientsVR catalogue."""
import asyncio
import io
import json
from copy import deepcopy
from urllib.error import HTTPError
from urllib.parse import unquote
from unittest.mock import AsyncMock, Mock
from types import SimpleNamespace
import pytest
from app.services import clients_vr, analytics_scope
from app.services.analytics_signal_worker import resume_blocker, begin_run
from app.models import AnalyticsSignalState
from test_stored_sales_shadow import active, decision, SCOPE

W,C,R='Оптовик','Корпоративный','Розница'

@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    clients_vr.cache.clear()
    def forbidden(*args,**kwargs):raise AssertionError('Real network forbidden')
    monkeypatch.setattr(clients_vr,'urlopen',forbidden)
    yield
    clients_vr.cache.clear()

def envelope(names,**extra):return dict(clients=names,total=len(names),complete=True,**extra)

def endpoints(monkeypatch,groups):
    paths=[]
    def request(candidates):
        assert len(candidates)==1
        path=unquote(candidates[0]);paths.append(path)
        assert path.startswith('/integration/buyer-types/') and path.endswith('/client-names')
        kind=path.split('/')[-2];assert kind in (W,C)
        value=groups[kind]
        if isinstance(value,Exception):raise value
        return deepcopy(value)
    monkeypatch.setattr(clients_vr,'_request',request)
    return paths

@pytest.mark.parametrize('kinds,included',[
    ([W],True),([C],True),([R],False),([W,R],True),([C,R],True),([W,C],False),([W,C,R],False)])
@pytest.mark.parametrize('copies',[1,3])
def test_business_combinations_and_duplicates(monkeypatch,kinds,included,copies):
    records=[('  ООО\u00a0АЛЬФА\t',kind) for kind in kinds for _ in range(copies)]
    paths=endpoints(monkeypatch,{kind:envelope([name for name,k in records if k==kind]) for kind in (W,C)})
    scope=asyncio.run(analytics_scope.load_analytics_scope())
    assert scope['client_keys']==(['ооо альфа'] if included else [])
    assert scope['department']=='Авиаторов' and scope['buyer_types']==[W,C]
    assert scope['policy']=='aviatorov-wholesale-corporate/2'
    assert len(paths)==2 and all('/integration/clients' not in p for p in paths)

def test_unicode_v1_collisions_and_case(monkeypatch):
    endpoints(monkeypatch,{W:envelope([' A\u00a0 B ','A  B','Straße','Ёлка','Елка','Alpha  Plus']),
                          C:envelope(['a\u2003b','STRASSE',' alpha\tplus '])})
    assert asyncio.run(analytics_scope.load_analytics_scope())['client_keys']==['strasse','straße','елка','ёлка']

@pytest.mark.parametrize('payload',[
    None,[],{},'',{'clients':[]},{'clients':[],'total':0},
    {'clients':[],'total':0,'complete':False},
    {'clients':[],'total':True,'complete':True},
    {'clients':[],'total':'0','complete':True},
    {'clients':[],'total':-1,'complete':True},
    {'clients':['A'],'total':2,'complete':True},
    {'clients':['A',None],'total':2,'complete':True},
    {'clients':[{'name':'A'}],'total':1,'complete':True},
    {'clients':[' \t'],'total':1,'complete':True},
    envelope([],has_more=True),envelope([],has_more=0),envelope([],next_cursor='cursor'),
    envelope([],next_page=2),envelope([],pages=2),envelope([],page=2),
    envelope([],pagination={'has_more':True}),envelope([],error='failed'),
    envelope([],status='error'),envelope([],client_names=[]),envelope([],buyer_type=R)])
def test_partial_malformed_unconfirmed_empty_rejected(monkeypatch,payload):
    endpoints(monkeypatch,{W:payload,C:envelope(['B'])})
    with pytest.raises(clients_vr.ClientsVrError):asyncio.run(analytics_scope.load_analytics_scope())
    assert not any(k.startswith('analytics-complete-buyer-names:') for k in clients_vr.cache)

def test_confirmed_empty_distinct_from_invalid_empty(monkeypatch):
    endpoints(monkeypatch,{W:envelope([]),C:envelope([])})
    assert asyncio.run(analytics_scope.load_analytics_scope())['client_keys']==[]
    # Worker separately blocks an empty scoped source, never expands to all.

@pytest.mark.parametrize('failed_kind',[W,C])
def test_one_endpoint_failure_no_partial_bundle(monkeypatch,failed_kind):
    groups={W:envelope(['A']),C:envelope(['B'])};groups[failed_kind]=TimeoutError('synthetic')
    endpoints(monkeypatch,groups)
    with pytest.raises(clients_vr.ClientsVrError):asyncio.run(analytics_scope.load_analytics_scope())
    assert not any(k.startswith('analytics-complete-buyer-names:') for k in clients_vr.cache)

def test_separate_coherent_cache_no_stale_fallback(monkeypatch):
    clients_vr.cache['client-buyer-types']=(clients_vr.time.monotonic(),{'A':R})
    paths=endpoints(monkeypatch,{W:envelope(['A']),C:envelope(['a'])})
    assert asyncio.run(analytics_scope.load_analytics_scope())['client_keys']==[]
    assert asyncio.run(analytics_scope.load_analytics_scope())['client_keys']==[]
    assert len(paths)==2 and clients_vr.cache['client-buyer-types'][1]=={'A':R}
    for key,(at,value) in list(clients_vr.cache.items()):
        if key.startswith('analytics-complete-buyer-names:'):
            clients_vr.cache[key]=(at-clients_vr.CACHE_TTL_SECONDS-1,value)
    endpoints(monkeypatch,{W:envelope(['A']),C:TimeoutError()})
    with pytest.raises(clients_vr.ClientsVrError):asyncio.run(analytics_scope.load_analytics_scope())

@pytest.mark.parametrize('pair',[(1,2),(1,None),(None,1),('',''),(True,True)])
def test_inconsistent_optional_revision_blocks(monkeypatch,pair):
    endpoints(monkeypatch,{kind:envelope(['A'],revision=rev) for kind,rev in zip((W,C),pair)})
    with pytest.raises(clients_vr.ClientsVrError):asyncio.run(analytics_scope.load_analytics_scope())

def test_real_http_function_only_two_specialized_gets(monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings,'clients_vr_api_url','https://clients.example/api')
    monkeypatch.setattr(settings,'clients_vr_api_token','synthetic-test-token')
    urls=[]
    class Response(io.BytesIO):
        def __enter__(self):return self
        def __exit__(self,*args):self.close()
    def urlopen(request,timeout):
        urls.append(unquote(request.full_url));assert request.get_method()=='GET' and timeout==15
        return Response(json.dumps(envelope(['A'])).encode())
    monkeypatch.setattr(clients_vr,'urlopen',urlopen)
    assert asyncio.run(analytics_scope.load_analytics_scope())['client_keys']==[]
    assert urls==[f'https://clients.example/api/integration/buyer-types/{kind}/client-names' for kind in (W,C)]

def test_404_never_falls_back_to_full_catalogue(monkeypatch):
    calls=[]
    def urlopen(request,timeout):
        calls.append(request.full_url);raise HTTPError(request.full_url,404,'synthetic',{},None)
    monkeypatch.setattr(clients_vr,'urlopen',urlopen)
    with pytest.raises(clients_vr.ClientsVrError):asyncio.run(analytics_scope.load_analytics_scope())
    assert len(calls)==1 and '/integration/clients' not in calls[0]

def test_scope_policy_transition_preserves_old_publication():
    run=active();run.metadata_json['scope']=dict(SCOPE,policy='aviatorov-wholesale-corporate/1')
    scope=dict(SCOPE,policy=analytics_scope.SCOPE_POLICY)
    assert resume_blocker(run,decision(),scope)=='ANALYTIC_SCOPE_CHANGED'
    async def scenario():
        run.status='SUCCEEDED';control=AnalyticsSignalState(id=1,enabled=True,published_run_id=run.id)
        db=SimpleNamespace(get=AsyncMock(return_value=run),scalar=AsyncMock(return_value=100),add=Mock(),flush=AsyncMock())
        result=await begin_run(db,control,decision(),scope)
        assert result.status=='BLOCKED'
        assert 'ANALYTIC_SCOPE_CHANGED_REQUIRES_ISOLATED_RUN' in result.metadata_json['source_decision']['reason_codes']
        assert control.published_run_id==run.id and run.status=='SUCCEEDED'
    asyncio.run(scenario())
