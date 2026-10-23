"""Native scoped SHADOW -> existing admin UI/API review -> independent release.

Opt-in PostgreSQL/MariaDB/PHP fixture. No production data or new binding model.
"""
import asyncio
from datetime import datetime, timedelta, timezone
import hmac
import http.cookiejar
import json
import os
from pathlib import Path
import subprocess
import threading
import time
import urllib.request
import urllib.error

import pytest
import uvicorn
from fastapi import FastAPI, Header, HTTPException
from sqlalchemy import insert, select
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy.pool import NullPool
from app.config import settings
from app.database import get_db
from app.models import Sale, AnalyticsSignalRun, AnalyticsClientEvaluation
from app.routers.analytics_delivery import router, changes
from app.services.analytics_signal_worker import last_closed_day
from app.services.analytics_automation import (review_identity, manage_policy, release_churn, digest, canonical)
from test_inactivity_shadow import database, finish


@pytest.mark.skipif(os.environ.get('REVIEW_BOOTSTRAP_E2E')!='1',reason='Explicit isolated admin-review stand required')
def test_native_shadow_existing_admin_review_and_external_identity(monkeypatch):
    artifacts=Path('/workspace/scratch/churn-real-data-audit')
    assert artifacts.is_dir()
    base='http://127.0.0.1:8890'
    env=os.environ.copy()
    for k in ('DOCKER_HOST','DOCKER_CONTEXT','DOCKER_TLS','DOCKER_TLS_VERIFY','DOCKER_CERT_PATH'):env.pop(k,None)
    def docker(*args,input=None):
        p=subprocess.run(['docker','--host=unix:///var/run/docker.sock',*args],input=input,text=True,capture_output=True,env=env,timeout=60)
        assert p.returncode==0,p.stderr
        return p.stdout
    def action(name):return json.loads(docker('exec','churn-real-review-http','php','tests/analytics_review_bootstrap.php',name))
    def api(session,route,body=None,csrf=True):
        opener,token=session;headers={'Content-Type':'application/json'}
        if csrf:headers['X-CSRF-Token']=token
        req=urllib.request.Request(base+'/vr/calltrack/api/'+route,headers=headers,data=json.dumps(body).encode() if body is not None else None)
        try:r=opener.open(req,timeout=10)
        except urllib.error.HTTPError as error:r=error
        return r.code,json.load(r)
    def login(i):
        opener=urllib.request.build_opener(urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))
        return opener,json.load(opener.open(base+'/fixture-login?id='+str(i)))['csrf']
    app=FastAPI();app.include_router(router,prefix='/api')
    @app.get('/integration/buyer-types/{kind}/client-names')
    def names(kind:str,authorization:str=Header('')):
        if authorization!='Bearer synthetic-vr-token':raise HTTPException(401)
        return ['Synthetic Customer'] if kind=='Оптовик' else []
    from app.services import clients_vr, analytics_scope
    monkeypatch.setattr(clients_vr,'cache',{})
    monkeypatch.setattr('app.services.analytics_signal_worker.load_analytics_scope',analytics_scope.load_analytics_scope)
    monkeypatch.setattr(settings,'clients_vr_api_url','http://127.0.0.1:18771')
    monkeypatch.setattr(settings,'clients_vr_api_token','synthetic-vr-token')
    monkeypatch.setattr(settings,'calltrack_integration_token','synthetic-integration-token')
    monkeypatch.setattr(settings,'analytics_automation_enabled',True)
    monkeypatch.setattr(settings,'analytics_automation_manifest_key','synthetic-independent-signer-only-32bytes')
    server=uvicorn.Server(uvicorn.Config(app,host='0.0.0.0',port=18771,access_log=False,log_level='error'))
    thread=threading.Thread(target=server.run,daemon=True);thread.start()
    for _ in range(100):
        if server.started:break
        time.sleep(.05)
    assert server.started
    async def scenario():
        async with database() as (engine,factory):
            async with factory() as db:
                from sqlalchemy import text
                schema=await db.scalar(text('select current_schema()'))
            http_engine=create_async_engine(engine.url,poolclass=NullPool,connect_args={'server_settings':{'search_path':schema}})
            http_factory=async_sessionmaker(http_engine,expire_on_commit=False)
            async def sessions():
                async with http_factory() as db:yield db
            app.dependency_overrides[get_db]=sessions
            try:
                day=last_closed_day();now=datetime.now(timezone.utc)
                rows=[dict(id=n+1,sale_date=day-timedelta(days=30+5*n),document_number=str(n),client='Synthetic Customer',department='Авиаторов',total_amount=1000,fingerprint='synthetic-'+str(n),import_id=1) for n in range(21)]
                async with factory() as db:await db.execute(insert(Sale),rows);await db.commit()
                result=await finish(factory,engine,day);rid=result['run_id']
                async with factory() as db:
                    page=await changes(rid,after=0,page_size=50,db=db);shadow=page['items'][0]
                    assert shadow['mode']=='SHADOW' and shadow['automation_allowed'] is False
                    run=await db.get(AnalyticsSignalRun,rid);full_scope=run.metadata_json['scope'];scope={k:v for k,v in full_scope.items() if k!='client_keys'}
                    candidate=(await db.scalars(select(AnalyticsClientEvaluation).where(AnalyticsClientEvaluation.run_id==rid))).one()
                (artifacts/'native-shadow-event.json').write_text(json.dumps(shadow))
                # Reset only this explicitly named local synthetic fixture database.
                docker('exec','churn-audit-mariadb-1186','mariadb','-uroot','-psynthetic-only','-e','DROP DATABASE IF EXISTS analytics_http_test; CREATE DATABASE analytics_http_test')
                docker('exec','churn-real-review-http','php','-r',"file_put_contents('/tmp/review-policy.json','{}');")
                docker('exec','churn-real-review-http','php','tests/fixtures/analytics/http_seed.php')
                admin=login(1);manager=login(3)
                assert api(manager,'analytics_admin.php')[0]==403
                shadow_row=api(admin,'analytics_admin.php')[1]['items'][0]
                assert shadow_row['subject']['kind']=='normalized_name'
                bind=dict(signal_key=shadow_row['signal_key'],client_id='client-100',binding_status='VERIFIED',technical=False,
                          confirm_all_sales_scope=True,confirm_nontechnical=True,evidence='Synthetic complete scoped history reviewed',expected_revision=None)
                assert api(admin,'analytics_admin.php',bind,csrf=False)[0]==403
                assert api(admin,'analytics_admin.php',{**bind,'confirm_all_sales_scope':False})[0]==422
                status,response=api(admin,'analytics_admin.php',bind);assert status==200,response
                assert response['decision']['decision']=='BLOCKED' and 'SHADOW_OR_AUTOMATION_NOT_APPROVED' in response['decision']['reasons']
                assert action('inspect')['tasks']==0
                identity=dict(client_key=candidate.client_key,scope=scope,client_id='client-100',manager_id=3,status='VERIFIED',evidence={'reason':'Synthetic independent reviewed history'})
                async with factory() as db:
                    with pytest.raises(ValueError):await review_identity(db,identity,actor='synthetic-admin',expected_revision=0)
                    await db.rollback()
                identity['evidence'].update({k:True for k in ('all_sales_reviewed','complete_client_history_reviewed','client_unique','manager_unique','nontechnical','no_name_phone_conflict')})
                async with factory() as db:
                    await review_identity(db,identity,actor='synthetic-admin',expected_revision=0)
                    await manage_policy(db,'draft',dict(min_confidence=.5,max_manifest_age_seconds=600,issuer='synthetic-upstream',required_sources=['synthetic-1c']),actor='synthetic-admin',expected_revision=0)
                    await manage_policy(db,'approve',{'confirm_business_approval':True},actor='synthetic-admin',expected_revision=1);await db.commit()
                expected=[dict(client_key='synthetic customer',day=r['sale_date'],checks=1,positive_checks=1,revenue='1000.00') for r in sorted(rows,key=lambda r:r['sale_date'])]
                assignments=[dict(subject_hash=digest(candidate.client_key),client_id='client-100',manager_id=3,revision=1)]
                payload=dict(version=1,issuer='synthetic-upstream',source_run_id=rid,source_revision=run.source_revision,as_of=day.isoformat(),scope_hash=digest(full_scope),identities_hash=digest(assignments),complete=True,clients_current=True,clients_revision='synthetic-vr',complete_through=day.isoformat(),source_revisions={'synthetic-1c':'fixture-1'},fact_count=len(expected),facts_hash=digest(expected),issued_at=(now-timedelta(seconds=10)).isoformat(),valid_until=(now+timedelta(minutes=5)).isoformat())
                envelope=dict(payload=payload,signature=hmac.new(settings.analytics_automation_manifest_key.encode(),canonical(payload).encode(),'sha256').hexdigest())
                incomplete={**payload,'complete':False}
                incomplete_envelope=dict(payload=incomplete,signature=hmac.new(settings.analytics_automation_manifest_key.encode(),canonical(incomplete).encode(),'sha256').hexdigest())
                async with factory() as db:
                    blocked=await release_churn(db,rid,incomplete_envelope,preview=True,now=now)
                    assert blocked['status']=='BLOCKED' and 'COMPLETENESS_OR_CLIENTS_UNCONFIRMED' in blocked['reasons']
                    await db.rollback()
                async with factory() as db:
                    released=await release_churn(db,rid,envelope,now=now);assert released['status']=='RELEASED',released;await db.commit()
                    from app.routers.analytics_delivery import automation_changes
                    approved=(await automation_changes(released['release_id'],after=0,page_size=50,db=db))['items'][0]
                    original=await db.get(AnalyticsSignalRun,rid)
                    assert original.metadata_json['automation_allowed'] is False and original.metadata_json['source_decision']['coverage_status']=='UNKNOWN'
                (artifacts/'native-approved-event.json').write_text(json.dumps(approved))
                docker('exec','churn-real-review-http','php','tests/analytics_review_bootstrap.php','configure_policy')
                action('sync_approved')
                items=api(admin,'analytics_admin.php')[1]['items'];external=next(r for r in items if r['subject']['kind']=='external_id')
                assert external['binding'] is None # Shadow review never silently grants external-ID approval.
                wrong={**bind,'signal_key':external['signal_key'],'client_id':'client-101'}
                status,response=api(admin,'analytics_admin.php',wrong)
                assert status==422,('A contradictory external client ID must not be stored as VERIFIED',status,response)
                assert action('inspect')['bindings']==1 # Only the earlier normalized-name review remains.
                external_bind={**bind,'signal_key':external['signal_key']}
                status,response=api(admin,'analytics_admin.php',external_bind);assert status==200,response
                assert response['decision']['decision']=='BLOCKED' # Policy, budget and manager still absent.
                assert action('inspect')['tasks']==0
                assert api(manager,'manager_bindings.php',dict(designation='Legacy Owner',web_user_id=3,expected_revision=None))[0]==403
                status,response=api(admin,'manager_bindings.php',dict(designation='Legacy Owner',web_user_id=3,expected_revision=None));assert status==200,response
                status,response=api(admin,'analytics_policy_admin.php',dict(revision=1,action='save_draft',min_confidence=.5,types=[dict(signal_type='CHURN_RISK',enabled=True,priority='HIGH',due_days=1),dict(signal_type='PURCHASE_OVERDUE',enabled=False,priority=None,due_days=None)]));assert status==200,response
                status,response=api(admin,'analytics_policy_admin.php',dict(revision=2,action='approve',confirm_business_approval=True));assert status==200,response
                status,response=api(admin,'analytics_admin.php',dict(signal_key=external['signal_key'],action='dry_run'));assert status==200 and 'BUDGET_NOT_APPROVED' in response['decision']['reasons']
                status,response=api(admin,'analytics_budget.php',dict(revision=1,approved=True,max_tasks=1));assert status==200,response
                status,response=api(admin,'analytics_admin.php',dict(signal_key=external['signal_key'],action='dry_run'));assert status==200 and response['decision']['decision']=='DRY_RUN_READY',response
                assert action('inspect')['tasks']==0
                assert action('process_disabled')['decision']=='DISABLED' and action('inspect')['tasks']==0
                # Explicit trusted consumer re-evaluation in this test only; never reset a feed cursor.
                assert action('process_enabled')['decision']=='TASK_CREATED'
                assert action('process_enabled')['decision']=='TASK_EXISTS'
                assert action('inspect')['tasks']==1 and action('inspect')['correct_tasks']==1
                print('REVIEW BOOTSTRAP PASS: native shadow -> existing admin+CSRF/full-history review; independent producer release; explicit external review+manager/policy/budget;0diagnostic tasks,1authorizedtask,replay1')
            finally:await http_engine.dispose()
    try:asyncio.run(scenario())
    finally:server.should_exit=True;thread.join(timeout=10)
