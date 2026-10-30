"""Opt-in real PostgreSQL worker -> release -> HTTP -> PHP/MariaDB tasks.

Only ClientsVR and its independent signer are synthetic fixture services. No fake
SQL adapter, synthetic approved event or bypass of producer authorization.
Requires dedicated local containers described in the phase-1 runbook.
"""
import asyncio
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hmac
import json
import os
from pathlib import Path
import subprocess
import threading
import time

import pytest
from app.services.churn_pilot import ALGORITHM_POLICY
import uvicorn
from fastapi import FastAPI, Header, HTTPException
from sqlalchemy import insert, select, text, func
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy.pool import NullPool
from app.config import settings
from app.database import get_db
from app.models import (Sale, AnalyticsSignalRun, AnalyticsSignal, AnalyticsClientEvaluation,
    AnalyticsAutomationPolicy as Policy, AnalyticsAutomationIdentity as Identity,
    AnalyticsAutomationEvent as Event, AnalyticsAutomationRelease as Release)
from app.routers.analytics_delivery import router
from app.services.analytics_signal_worker import last_closed_day
from app.services.analytics_automation import (release_churn, source_manifest_facts, digest, canonical, identity_key)
from test_inactivity_shadow import database, finish

@pytest.mark.skipif(os.environ.get('CHURN_PHASE1_E2E')!='1',reason='Explicit isolated PostgreSQL/MariaDB/HTTP stand required')
@pytest.mark.parametrize('pilot',[False,True])
def test_real_worker_release_http_mariadb_budget_replay_and_completion(monkeypatch,pilot):
    candidates=6 if pilot else 2
    root=Path(os.environ.get('CALLTRACK_ROOT','/workspace/calltrack-verification')).resolve()
    assert (root/'tests/churn_automation_e2e.php').is_file()
    port=18769
    app=FastAPI();app.include_router(router,prefix='/api')
    @app.get('/integration/buyer-types/{kind}/client-names')
    def names(kind:str,authorization:str=Header('')):
        if authorization!='Bearer synthetic-vr-token':raise HTTPException(401)
        return ['Fixture '+str(i) for i in range(8 if pilot else 4)] if kind=='Оптовик' else []
    from app.services import clients_vr
    monkeypatch.setattr(clients_vr,'cache',{})
    monkeypatch.setattr(settings,'clients_vr_api_url',f'http://127.0.0.1:{port}')
    monkeypatch.setattr(settings,'clients_vr_api_token','synthetic-vr-token')
    monkeypatch.setattr(settings,'calltrack_integration_token','synthetic-integration-token')
    monkeypatch.setattr(settings,'analytics_automation_enabled',True)
    monkeypatch.setattr(settings,'analytics_automation_manifest_key','synthetic-independent-signer-only-32bytes')
    server=uvicorn.Server(uvicorn.Config(app,host='0.0.0.0',port=port,log_level='error',access_log=False))
    thread=threading.Thread(target=server.run,daemon=True);thread.start()
    for _ in range(100):
        if server.started:break
        time.sleep(.05)
    assert server.started
    async def run():
        async with database() as (engine,factory):
            async with factory() as db:
                schema=await db.scalar(text('select current_schema()'))
            http_engine=create_async_engine(engine.url,poolclass=NullPool,connect_args={'server_settings':{'search_path':schema}})
            http_factory=async_sessionmaker(http_engine,expire_on_commit=False)
            async def sessions():
                async with http_factory() as db:yield db
            app.dependency_overrides[get_db]=sessions
            day=last_closed_day();now=datetime.now(timezone.utc)
            rows=[]
            for i,delay in enumerate([45]*6+[5,2] if pilot else [45,46,5,2]):
                for n in range(3 if pilot else 21):
                    rows.append(dict(id=len(rows)+1,sale_date=day-timedelta(days=delay+5*n),
                        document_number=str(len(rows)),client='Fixture '+str(i),department='Авиаторов',
                        total_amount=1000,fingerprint='synthetic-'+str(len(rows)),import_id=1))
            async with factory() as db:
                await db.execute(insert(Sale),rows);await db.commit()
            result=await finish(factory,engine,day)
            rid=result['run_id']
            async with factory() as db:
                run=await db.get(AnalyticsSignalRun,rid)
                assert run.metadata_json['source_decision']['coverage_status']=='UNKNOWN'
                signals=(await db.scalars(select(AnalyticsSignal))).all()
                assert sum(s.signal_type=='CHURN_RISK' for s in signals)==candidates
                assert not any(s.signal_type=='PURCHASE_OVERDUE' for s in signals)
                fullscope=run.metadata_json['scope'];scope={k:v for k,v in fullscope.items() if k!='client_keys'}
                evaluations=(await db.scalars(select(AnalyticsClientEvaluation).where(AnalyticsClientEvaluation.run_id==rid,AnalyticsClientEvaluation.signal_type=='CHURN_RISK').order_by(AnalyticsClientEvaluation.client_key))).all()
                db.add(Policy(id=1,enabled=True,approved=True,revision=1,sequence=0,
                    settings_json={'algorithm_policy':ALGORITHM_POLICY,'min_confidence':(.3 if pilot else .5),'max_manifest_age_seconds':7200,'issuer':'synthetic-upstream','required_sources':['synthetic-1c']}))
                assignments=[]
                for i,c in enumerate(evaluations):
                    binding=Identity(subject_key=identity_key(c.client_key,scope),client_id=f'fixture-id-{i}',manager_id=3+i%2,revision=1,status='VERIFIED',
                        evidence=dict(all_sales_reviewed=True,complete_client_history_reviewed=True,client_unique=True,manager_unique=True,nontechnical=True,no_name_phone_conflict=True,reviewer='synthetic-reviewer',reason='independent synthetic source identity'))
                    db.add(binding);assignments.append(dict(subject_hash=digest(c.client_key),client_id=binding.client_id,manager_id=binding.manager_id,revision=1))
                facts=await source_manifest_facts(db,run)
                # This expectation is independently derived from fixture input sales.
                expected_facts=sorted([dict(client_key=row['client'].lower(),day=row['sale_date'],checks=1,positive_checks=1,revenue='1000.00') for row in rows],key=lambda x:(x['client_key'],x['day']))
                assert digest(expected_facts)==digest(facts)
                payload=dict(version=1,issuer='synthetic-upstream',source_run_id=rid,source_revision=run.source_revision,
                    as_of=day.isoformat(),scope_hash=digest(fullscope),identities_hash=digest(assignments),complete=True,clients_current=True,
                    clients_revision='synthetic-vr-revision',complete_through=day.isoformat(),source_revisions={'synthetic-1c':'fixture-source-rev-1'},fact_count=len(expected_facts),facts_hash=digest(expected_facts),
                    issued_at=(now-timedelta(minutes=1)).isoformat(),valid_until=(now+timedelta(hours=1)).isoformat())
                envelope=dict(payload=payload,signature=hmac.new(settings.analytics_automation_manifest_key.encode(),canonical(payload).encode(),'sha256').hexdigest())
                await db.commit()
            async with factory() as db:
                # Missing signature and disabled feature are independently fail-closed.
                blocked=await release_churn(db,rid,{},preview=True,now=now)
                assert blocked['status']=='BLOCKED' and 'UPSTREAM_SIGNATURE_INVALID' in blocked['reasons']
                malformed=await release_churn(db,rid,dict(payload=envelope['payload'],signature='я'*64),preview=True,now=now)
                assert malformed['status']=='BLOCKED' and 'UPSTREAM_SIGNATURE_INVALID' in malformed['reasons']
                assert await db.scalar(select(func.count()).select_from(Release))==0
                await db.rollback()
            # Independently signed but invalid/expired/mismatching evidence cannot authorize.
            for field,value in [('complete',False),('clients_current',False),('clients_revision','UNKNOWN'),('source_revisions',{}),('facts_hash','wrong'),('source_revision',999),('valid_until',(now-timedelta(seconds=1)).isoformat())]:
                invalid=deepcopy(envelope);invalid['payload'][field]=value
                invalid['signature']=hmac.new(settings.analytics_automation_manifest_key.encode(),canonical(invalid['payload']).encode(),'sha256').hexdigest()
                async with factory() as db:
                    refused=await release_churn(db,rid,invalid,preview=True,now=now)
                    assert refused['status']=='BLOCKED',(field,refused)
                    await db.rollback()
            monkeypatch.setattr(settings,'analytics_automation_enabled',False)
            async with factory() as db:
                assert (await release_churn(db,rid,envelope,preview=True,now=now))['status']=='BLOCKED'
                await db.rollback()
            monkeypatch.setattr(settings,'analytics_automation_enabled',True)
            for broken in ('policy','algorithm','confidence','identity','name_phone_conflict','aliases'):
                async with factory() as db:
                    policy=await db.get(Policy,1)
                    binding=await db.get(Identity,identity_key(evaluations[0].client_key,scope))
                    if broken=='policy':policy.approved=False
                    elif broken=='algorithm':policy.settings_json={k:v for k,v in policy.settings_json.items() if k!='algorithm_policy'}
                    elif broken=='confidence':policy.settings_json={**policy.settings_json,'min_confidence':None}
                    elif broken=='identity':binding.status='AMBIGUOUS'
                    elif broken=='name_phone_conflict':binding.evidence={**binding.evidence,'no_name_phone_conflict':False}
                    else:db.add(Identity(subject_key=digest('extra-alias'),client_id=binding.client_id,manager_id=binding.manager_id,revision=1,status='VERIFIED',evidence=binding.evidence))
                    await db.flush()
                    refused=await release_churn(db,rid,envelope,preview=True,now=now)
                    if broken in ('identity','name_phone_conflict','aliases'):assert refused['ready']==candidates-1 and refused['decisions'][0]['reasons']
                    else:assert refused['status']=='BLOCKED'
                    await db.rollback()
            async with factory() as db:
                preview=await release_churn(db,rid,envelope,preview=True,now=now)
                assert preview['status']=='READY' and preview['ready']==candidates,preview
                assert await db.scalar(select(func.count()).select_from(Event))==0
                await db.rollback()
            # Concurrent release requests commit exactly one immutable release.
            async def publish():
                async with factory() as db:
                    r=await release_churn(db,rid,envelope,now=now);await db.commit();return r
            released=await asyncio.gather(publish(),publish())
            assert sorted(r['status'] for r in released)==['EXISTING','RELEASED'],released
            async with factory() as db:
                assert await db.scalar(select(func.count()).select_from(Release))==1
                assert await db.scalar(select(func.count()).select_from(Event))==candidates
                original=await db.get(AnalyticsSignalRun,rid)
                assert original.metadata_json['source_decision']['coverage_status']=='UNKNOWN'
            docker=['docker','--host=unix:///var/run/docker.sock']
            def command(args):
                env=os.environ.copy()
                for name in ('DOCKER_HOST','DOCKER_CONTEXT','DOCKER_TLS','DOCKER_TLS_VERIFY','DOCKER_CERT_PATH'):env.pop(name,None)
                return subprocess.run(docker+args,check=True,text=True,capture_output=True,env=env)
            mariadb_container=os.environ.get('CHURN_TEST_MARIADB_CONTAINER','churn-phase1-mariadb')
            assert mariadb_container in {'churn-phase1-mariadb','churn-audit-mariadb-1186'}
            command(['exec',mariadb_container,'mariadb','-uroot','-psynthetic-only','-e','DROP DATABASE IF EXISTS analytics_sync_test; CREATE DATABASE analytics_sync_test'])
            gateway=command(['network','inspect','churn-phase1-test','--format','{{(index .IPAM.Config 0).Gateway}}']).stdout.strip()
            args=['run','--rm','--network','churn-phase1-test','--read-only','--tmpfs','/tmp',
                '-e','ANALYTICS_TASK_TEST_DSN=mysql:host=analytics-test-mariadb;dbname=analytics_sync_test',
                '-e','ANALYTICS_TASK_TEST_USER=root','-e','ANALYTICS_TASK_TEST_PASSWORD=synthetic-only',
                '-e','CALLTRACK_STORAGE_DIR=/tmp/churn','-e','CALLTRACK_ANALYTICS_FEED=approved_churn',
                '-e',f'SALES_JOURNAL_BASE_URL=http://{gateway}:{port}',
                '-e','CALLTRACK_INTEGRATION_TOKEN=synthetic-integration-token','-e',f'NO_PROXY={gateway},analytics-test-mariadb','-e',f'no_proxy={gateway},analytics-test-mariadb',
                '-v',str(root)+':/code:ro','-w','/code','analytics-php-test:local','php','tests/churn_pilot_e2e.php' if pilot else 'tests/churn_automation_e2e.php']
            try:output=command(args)
            except subprocess.CalledProcessError as exc:
                raise AssertionError(exc.stdout+'\n'+exc.stderr) from None
            print(output.stdout)
            # Revocation/changed source cannot serve an old approved release.
            import httpx
            async with httpx.AsyncClient(base_url=f'http://127.0.0.1:{port}',headers={'Authorization':'Bearer synthetic-integration-token'}) as client:
                url=f"/api/integrations/calltrack/analytics/automation/runs/{released[0]['release_id']}/changes"
                assert (await client.get(url,headers={'Authorization':'Bearer wrong'})).status_code==401
                async with factory() as db:
                    policy=await db.get(Policy,1);saved=dict(policy.settings_json)
                    policy.settings_json={k:v for k,v in saved.items() if k!='algorithm_policy'};await db.commit()
                assert (await client.get(url)).status_code==503
                async with factory() as db:
                    policy=await db.get(Policy,1);policy.settings_json=saved
                    release=await db.get(Release,released[0]['release_id']);saved_release=dict(release.metadata_json)
                    release.metadata_json={k:v for k,v in saved_release.items() if k!='algorithm_policy'};await db.commit()
                assert (await client.get(url)).status_code==410
                async with factory() as db:
                    release=await db.get(Release,released[0]['release_id']);release.metadata_json=saved_release;await db.commit()
                async with factory() as db:
                    policy=await db.get(Policy,1);policy.revision=2;await db.commit()
                assert (await client.get(url)).status_code==410
                async with factory() as db:
                    policy=await db.get(Policy,1);policy.revision=1;await db.commit()
                    from app.models import ImportBatch
                    batch=await db.get(ImportBatch,1);batch.status='processing';await db.commit()
                assert (await client.get(url)).status_code==410
                async with factory() as db:
                    batch=await db.get(ImportBatch,1);batch.status='completed'
                    binding=await db.get(Identity,identity_key(evaluations[0].client_key,scope))
                    db.add(Identity(subject_key=digest('newly-discovered-alias'),client_id=binding.client_id,manager_id=binding.manager_id,revision=1,status='VERIFIED',evidence=binding.evidence))
                    await db.commit()
                response=await client.get(url)
                assert response.status_code==410 and response.json()['detail']=='Release identity aliases require history merge'
                async with factory() as db:
                    from sqlalchemy import delete
                    await db.execute(delete(Identity).where(Identity.subject_key==digest('newly-discovered-alias')))
                    binding=await db.get(Identity,identity_key(evaluations[0].client_key,scope));binding.status='REVIEW';await db.commit()
                assert (await client.get(url)).status_code==410
            await http_engine.dispose()
    try:asyncio.run(run())
    finally:server.should_exit=True;thread.join(timeout=10)
