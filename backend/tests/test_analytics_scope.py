"""Owner-confirmed list[str] contract, synthetic HTTP and v2 scope tests."""
import asyncio
import io
import json
from copy import deepcopy
from urllib.error import HTTPError, URLError
from urllib.parse import unquote
from unittest.mock import AsyncMock, Mock
from types import SimpleNamespace
import pytest
from app.services import clients_vr, analytics_scope
from app.services.analytics_signal_worker import resume_blocker, begin_run
from app.models import AnalyticsSignalState
from test_stored_sales_shadow import active, decision, SCOPE

W,C,R='Оптовик','Корпоративный','Розница'
PREFIX='analytics-buyer-names-list-v1:'

@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    clients_vr.cache.clear()
    def forbidden(*args,**kwargs):raise AssertionError('Real network/full catalogue forbidden')
    monkeypatch.setattr(clients_vr,'urlopen',forbidden)
    monkeypatch.setattr(clients_vr,'_request',forbidden)
    yield
    clients_vr.cache.clear()

def endpoints(monkeypatch,groups):
    paths=[]
    def request(encoded):
        path=unquote(encoded);paths.append(path)
        assert path.startswith('/integration/buyer-types/') and path.endswith('/client-names')
        kind=path.split('/')[-2];assert kind in (W,C)
        value=groups[kind]
        if isinstance(value,Exception):raise value
        return deepcopy(value)
    monkeypatch.setattr(clients_vr,'_request_analytics_buyer_names',request)
    return paths

@pytest.mark.parametrize('kinds,included',[
    ([W],True),([C],True),([R],False),([W,R],True),([C,R],True),([W,C],False),([W,C,R],False)])
@pytest.mark.parametrize('copies',[1,3])
def test_business_combinations_and_duplicates_real_list_format(monkeypatch,kinds,included,copies):
    records=[('  ООО\u00a0АЛЬФА\t',kind) for kind in kinds for _ in range(copies)]
    paths=endpoints(monkeypatch,{kind:[name for name,k in records if k==kind] for kind in (W,C)})
    scope=asyncio.run(analytics_scope.load_analytics_scope())
    assert scope['client_keys']==(['ооо альфа'] if included else [])
    assert scope['department']=='Авиаторов' and scope['buyer_types']==[W,C]
    assert scope['policy']=='aviatorov-wholesale-corporate/2'
    assert paths==[f'/integration/buyer-types/{kind}/client-names' for kind in (W,C)]

def test_unicode_v1_collisions_and_case(monkeypatch):
    endpoints(monkeypatch,{W:[' A\u00a0 B ','A  B','Straße','Ёлка','Елка','Alpha  Plus'],
                          C:['a\u2003b','STRASSE',' alpha\tplus ']})
    assert asyncio.run(analytics_scope.load_analytics_scope())['client_keys']==['strasse','straße','елка','ёлка']

@pytest.mark.parametrize('payload',[
    None,{},'',True,42,('A',),{'clients':[]},{'clients':['A'],'total':1,'complete':True},
    ['A',None],[{'name':'A'}],[' \t'],['\u00a0\u2003'],[1],[False],[['A']]])
def test_malformed_list_or_element_rejected(monkeypatch,payload):
    endpoints(monkeypatch,{W:payload,C:['B']})
    with pytest.raises(clients_vr.ClientsVrError):asyncio.run(analytics_scope.load_analytics_scope())
    assert not any(k.startswith(PREFIX) for k in clients_vr.cache)

@pytest.mark.parametrize('wholesale,corporate,expected',[
    ([],[],[]),([],['B'],['b']),(['A'],[],['a'])])
def test_successful_empty_array_is_valid_not_a_network_error(monkeypatch,wholesale,corporate,expected):
    endpoints(monkeypatch,{W:wholesale,C:corporate})
    assert asyncio.run(analytics_scope.load_analytics_scope())['client_keys']==expected
    # Empty combined selection stays empty; worker's no-scoped-sales guard blocks.

@pytest.mark.parametrize('failed_kind',[W,C])
@pytest.mark.parametrize('error',[TimeoutError('synthetic'),URLError('synthetic'),ValueError('invalid JSON')])
def test_either_endpoint_failure_no_partial_bundle(monkeypatch,failed_kind,error):
    groups={W:['A'],C:['B']};groups[failed_kind]=error
    endpoints(monkeypatch,groups)
    with pytest.raises(clients_vr.ClientsVrError):asyncio.run(analytics_scope.load_analytics_scope())
    assert not any(k.startswith(PREFIX) for k in clients_vr.cache)

def test_separate_coherent_cache_no_stale_or_old_contract_fallback(monkeypatch):
    clients_vr.cache['client-buyer-types']=(clients_vr.time.monotonic(),{'A':R})
    clients_vr.cache['analytics-complete-buyer-names:'+json.dumps((W,C),ensure_ascii=False)]=(clients_vr.time.monotonic(),{W:('Old',),C:()})
    paths=endpoints(monkeypatch,{W:['A'],C:['a']})
    assert asyncio.run(analytics_scope.load_analytics_scope())['client_keys']==[]
    assert asyncio.run(analytics_scope.load_analytics_scope())['client_keys']==[]
    assert len(paths)==2 and clients_vr.cache['client-buyer-types'][1]=={'A':R}
    for key,(at,value) in list(clients_vr.cache.items()):
        if key.startswith(PREFIX):clients_vr.cache[key]=(at-clients_vr.CACHE_TTL_SECONDS-1,value)
    endpoints(monkeypatch,{W:['A'],C:TimeoutError()})
    with pytest.raises(clients_vr.ClientsVrError):asyncio.run(analytics_scope.load_analytics_scope())

class Response(io.BytesIO):
    status=200
    def __enter__(self):return self
    def __exit__(self,*args):self.close()

def test_http_transport_only_two_specialized_gets(monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings,'clients_vr_api_url','https://clients.example/api')
    monkeypatch.setattr(settings,'clients_vr_api_token','synthetic-test-token')
    urls=[]
    def urlopen(request,timeout):
        urls.append(unquote(request.full_url));assert request.get_method()=='GET' and timeout==15
        assert request.get_header('Authorization')=='Bearer synthetic-test-token'
        return Response(json.dumps(['A']).encode())
    monkeypatch.setattr(clients_vr,'urlopen',urlopen)
    assert asyncio.run(analytics_scope.load_analytics_scope())['client_keys']==[]
    assert urls==[f'https://clients.example/api/integration/buyer-types/{kind}/client-names' for kind in (W,C)]

@pytest.mark.parametrize('status',[401,403,404,429,500,503])
def test_http_error_never_becomes_empty_or_full_fallback(monkeypatch,status):
    calls=[]
    def urlopen(request,timeout):
        calls.append(request.full_url);raise HTTPError(request.full_url,status,'synthetic',{},None)
    monkeypatch.setattr(clients_vr,'urlopen',urlopen)
    with pytest.raises(clients_vr.ClientsVrError):asyncio.run(analytics_scope.load_analytics_scope())
    assert len(calls)==1 and '/integration/clients' not in calls[0]
    assert not any(k.startswith(PREFIX) for k in clients_vr.cache)

@pytest.mark.parametrize('status',[201,204,206,301,302])
def test_non_200_response_rejected_even_if_body_is_valid_array(monkeypatch,status):
    def urlopen(*args,**kwargs):
        response=Response(b'[]');response.status=status;return response
    monkeypatch.setattr(clients_vr,'urlopen',urlopen)
    with pytest.raises(clients_vr.ClientsVrError):asyncio.run(analytics_scope.load_analytics_scope())

@pytest.mark.parametrize('body',[b'',b'[',b'["A"',b'null',b'{}',b'""',b'\xff'])
def test_empty_malformed_json_or_utf8_cannot_be_successful_zero(monkeypatch,body):
    monkeypatch.setattr(clients_vr,'urlopen',lambda *args,**kwargs:Response(body))
    with pytest.raises(clients_vr.ClientsVrError):asyncio.run(analytics_scope.load_analytics_scope())
    assert not any(k.startswith(PREFIX) for k in clients_vr.cache)

def test_truncated_second_http_response_cannot_cache_first_list(monkeypatch):
    from http.client import IncompleteRead
    class Truncated(Response):
        def read(self,*args,**kwargs):raise IncompleteRead(b'["A"',10)
    count=0
    def urlopen(*args,**kwargs):
        nonlocal count
        count+=1
        return Response(b'["A"]') if count==1 else Truncated()
    monkeypatch.setattr(clients_vr,'urlopen',urlopen)
    with pytest.raises(clients_vr.ClientsVrError):asyncio.run(analytics_scope.load_analytics_scope())
    assert count==2 and not any(k.startswith(PREFIX) for k in clients_vr.cache)

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
