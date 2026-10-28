"""Autonomous HoReCa lifetime tests: ASGI only, no DB, SMTP or catalog requests."""
import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

import httpx
import pytest
from fastapi import FastAPI
from app.database import get_db
from app.models import HorecaReport, SmtpConfig
from app.routers import horeca_reports
from app.services import scenarios
from app.services.horeca_links import (
    HORECA_LINK_LIFETIME, HORECA_LINK_PREFIX, create_horeca_link,
    horeca_link_expires_at,
)


def request(monkeypatch, *, elapsed, path='', legacy=False, created=None, strip_prefix=False):
    token, actual_created = create_horeca_link()
    created = created or actual_created
    if legacy:token='legacy_opaque_token_without_a_dot'
    row=SimpleNamespace(token=token,created_at=created,period_start=created.date(),
                        period_end=created.date(),products=[dict(key='one',name='Synthetic',photo=None)])
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):return (created+elapsed).astimezone(tz)
    monkeypatch.setattr(horeca_reports,'datetime',Clock)
    class DB:
        async def scalar(self, query):return row if list(query.compile().params.values())==[token] else None
        async def get(self,*args):raise AssertionError('Expired request must not load mail settings')
        async def commit(self):raise AssertionError('Expired request must not mutate a report')
    db=DB()
    async def session():yield db
    app=FastAPI();app.include_router(horeca_reports.router,prefix='/api');app.dependency_overrides[get_db]=session
    presented=token.removeprefix(HORECA_LINK_PREFIX) if strip_prefix else token
    async def scenario():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),base_url='http://test') as client:
            if path=='/send':return await client.post(f'/api/reports/horeca/{presented}/send',json={'keys':['one']})
            return await client.get(f'/api/reports/horeca/{presented}{path}')
    return asyncio.run(scenario())


@pytest.mark.parametrize('elapsed',[timedelta(),timedelta(days=9,hours=23),timedelta(hours=240)-timedelta(microseconds=1)])
def test_new_link_grants_access_until_exact_expiry(monkeypatch,elapsed):
    response=request(monkeypatch,elapsed=elapsed)
    assert response.status_code==200
    assert response.json()['products'][0]['name']=='Synthetic'


@pytest.mark.parametrize('elapsed',[timedelta(hours=240),timedelta(hours=240,microseconds=1),timedelta(days=11)])
@pytest.mark.parametrize('path',['','/images','/send'])
def test_expired_link_denies_every_operation_before_side_effects(monkeypatch,elapsed,path):
    async def no_catalog(*args):raise AssertionError('No external image requests')
    def no_mail(*args):raise AssertionError('No email may be sent')
    monkeypatch.setattr(horeca_reports,'get_catalog_images',no_catalog)
    monkeypatch.setattr(horeca_reports,'_send',no_mail)
    response=request(monkeypatch,elapsed=elapsed,path=path)
    assert response.status_code==410
    assert 'истёк' in response.json()['detail']
    assert 'products' not in response.json()


def test_existing_horeca_links_are_not_changed_retroactively(monkeypatch):
    assert request(monkeypatch,elapsed=timedelta(days=365),legacy=True).status_code==200


def test_token_and_timestamp_are_created_together_without_entropy_reduction():
    before=datetime.now(timezone.utc);token,created=create_horeca_link();after=datetime.now(timezone.utc)
    assert before<=created<=after and created.tzinfo==timezone.utc
    assert token.startswith(HORECA_LINK_PREFIX) and len(token)<=64
    assert len(token.removeprefix(HORECA_LINK_PREFIX))==43
    assert horeca_link_expires_at(token,created)==created+timedelta(hours=240)
    assert HORECA_LINK_LIFETIME.total_seconds()==864000


@pytest.mark.parametrize('created',[datetime(2026,10,28,12,tzinfo=ZoneInfo('America/New_York')),
                                    datetime(2026,10,28,12,tzinfo=timezone(timedelta(hours=3)))])
def test_expiry_is_240_hours_in_utc_including_dst(created):
    expiry=horeca_link_expires_at('h1.synthetic',created)
    assert expiry==created.astimezone(timezone.utc)+timedelta(hours=240)
    assert expiry.tzinfo==timezone.utc


def test_naive_driver_datetime_is_treated_as_utc():
    created=datetime(2026,10,28,12)
    assert horeca_link_expires_at('h1.synthetic',created)==datetime(2026,11,7,12,tzinfo=timezone.utc)


def test_other_scenario_and_auth_expiration_are_unchanged():
    from app.services.settings_auth_crypto import SESSION_TTL_SECONDS,create_session,verify_session
    from app.services.access_devices import effective_status
    assert SESSION_TTL_SECONDS==8*60*60
    token=create_session('synthetic-secret',now=1000)
    assert verify_session(token,'synthetic-secret',now=1001)
    assert not verify_session(token,'synthetic-secret',now=1000+SESSION_TTL_SECONDS+1)
    now=datetime.now(timezone.utc)
    assert effective_status(SimpleNamespace(status='active',expires_at=now),now)=='expired'


def test_scenario_persists_creation_time_and_only_adds_email_lifetime_note(monkeypatch):
    token,created=create_horeca_link();captured=[];sent=[]
    class DB:
        async def __aenter__(self):return self
        async def __aexit__(self,*args):pass
        async def get(self,model,key):assert model is SmtpConfig;return SimpleNamespace()
        async def execute(self,query):return SimpleNamespace(all=lambda:[])
        def add(self,row):captured.append(row)
        async def commit(self):pass
    monkeypatch.setattr(scenarios,'SessionLocal',DB)
    monkeypatch.setattr(scenarios,'create_horeca_link',lambda:(token,created))
    monkeypatch.setattr(scenarios,'get_manager_clients',AsyncMock(return_value=[]))
    monkeypatch.setattr(scenarios,'get_catalog_batch_info',AsyncMock(return_value={}))
    monkeypatch.setattr(scenarios,'_send',lambda *args:sent.append(args))
    async def run_send_inline(fn,*args):return fn(*args)
    monkeypatch.setattr(scenarios.asyncio,'to_thread',run_send_inline)
    scenario=SimpleNamespace(id=1,manager='Synthetic',email='recipient@example.test',message_text='Original message')
    day=created.date()
    result=asyncio.run(scenarios.run_scenario(scenario,day,(day,day)))
    assert len(captured)==1 and isinstance(captured[0],HorecaReport)
    assert captured[0].token==token and captured[0].created_at==created
    assert captured[0].products==[] and result['products']==0
    assert sent[0][1]==['recipient@example.test'] and sent[0][2]=='Продажи HoReCa'
    assert 'Original message' in sent[0][3] and '10 суток (240 часов)' in sent[0][3]
    assert result['link'] in sent[0][3]


def test_removing_version_prefix_cannot_bypass_expiration(monkeypatch):
    response=request(monkeypatch,elapsed=timedelta(days=11),strip_prefix=True)
    assert response.status_code==404
    assert 'products' not in response.json()


def test_unknown_version_and_missing_creation_time_fail_closed():
    with pytest.raises(ValueError):horeca_link_expires_at('h2.synthetic',datetime.now(timezone.utc))
    with pytest.raises(ValueError):horeca_link_expires_at('h1.synthetic',None)
