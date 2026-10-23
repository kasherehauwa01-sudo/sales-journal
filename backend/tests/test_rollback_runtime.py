"""Opt-in reconstructed-old Docker rollback against prepared synthetic schema24.

Requires the isolated stand documented in ROLLBACK_COMPATIBILITY_REPORT.md.
Never uses app DATABASE_URL, Docker contexts, server folders or production data.
"""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time
from uuid import uuid4

import pytest

@pytest.mark.skipif(os.environ.get('ROLLBACK_COMPAT_E2E')!='1',reason='Explicit isolated schema24 Docker stand required')
def test_checked_old_runtime_preserves_all_data_and_restarts():
    env=dict(os.environ)
    for key in ('DOCKER_HOST','DOCKER_CONTEXT','DOCKER_TLS','DOCKER_TLS_VERIFY','DOCKER_CERT_PATH'):env.pop(key,None)
    docker=['docker','--host=unix:///var/run/docker.sock']
    def call(args,input=None,check=True):
        p=subprocess.run(docker+args,input=input,text=True,capture_output=True,env=env,timeout=90)
        if check:assert p.returncode==0,(args,p.stdout,p.stderr)
        return p
    base=['--network','readiness-churn-test','-e','DATABASE_URL=postgresql+asyncpg://fixture:synthetic-only@readiness-churn-pg:5432/readiness_test',
          '-e','CLIENTS_VR_API_URL=','-e','VRCATALOG_API_URL=','-e','CALLTRACK_INTEGRATION_TOKEN=synthetic-only-token',
          '-e','ANALYTICS_SIGNALS_ENABLED=false','-e','ANALYTICS_SIGNALS_MODE=SHADOW','-e','ANALYTICS_AUTOMATION_ENABLED=false']
    repo=Path(__file__).resolve().parents[2]
    merged=call(['compose','--env-file','/dev/null','-f',str(repo/'docker-compose.yml'),'-f',str(repo/'deploy/rollback.checked.override.yml'),'config','--no-interpolate','--format','json'])
    service=json.loads(merged.stdout)['services']['backend']
    assert not service.get('build') and service['pull_policy']=='never'
    assert '--rollback' in service['command'] and service['stop_grace_period']=='30s'
    image='rollback-de0a0c8:rehearsal'
    script=Path(__file__).resolve().parents[1]/'start_backend.py'
    assert script.is_file()
    image_id=call(['image','inspect',image,'--format','{{.Id}}']).stdout.strip()
    def sql(text):return call(['exec','-i','readiness-churn-pg','psql','-X','-At','-v','ON_ERROR_STOP=1','-U','fixture','-d','readiness_test'],text).stdout
    assert sql("SELECT version_num FROM alembic_version; SELECT count(*) FROM sales; SELECT count(*) FROM sales WHERE client='Fixture Alpha';").splitlines()==['20261022_24','46','16']
    def snapshot():
        return sql(r"""BEGIN READ ONLY;
SELECT format($q$SELECT %L,coalesce(jsonb_agg(j ORDER BY j::text),'[]'::jsonb) FROM (SELECT to_jsonb(t) j FROM %I t) s;$q$,tablename,tablename) FROM pg_tables WHERE schemaname='public' ORDER BY tablename
\gexec
ROLLBACK;
""")
    before=snapshot()
    original=call(['run','--rm',*base,image],check=False)
    assert original.returncode==255 and "Can't locate revision identified by '20261022_24'" in original.stdout+original.stderr
    health="import urllib.request;assert urllib.request.urlopen('http://localhost:8000/api/health',timeout=1).status==200"
    probe="""import urllib.request,json
paths=['/api/health','/api/sales?page_size=50','/api/sales/1','/api/sales/1/items','/api/reports/rfm?date_from=2026-07-01&date_to=2026-10-06','/api/integrations/calltrack/sales/1','/api/filters','/api/suggestions?field=client&q=Fixture','/api/reports/product-sales/products/search?search=Synthetic']
for path in paths:
 req=urllib.request.Request('http://localhost:8000'+path,headers={'Authorization':'Bearer synthetic-only-token'})
 with urllib.request.urlopen(req) as response:
  assert response.status==200
  if '/products/search' in path:assert json.load(response)['total']==1
print('Nine business endpoints HTTP200; products verified')
"""
    names=[]
    def start():
        name='rollback-regression-'+uuid4().hex;names.append(name)
        call(['run','--rm','-d','--name',name,'--stop-timeout','30',*base,'-v',str(script)+':/run/start_backend.py:ro',image,'python','-B','/run/start_backend.py','--rollback'])
        return name
    def ready(name):
        for _ in range(60):
            if call(['exec',name,'python','-B','-c',health],check=False).returncode==0:return
            time.sleep(.2)
        pytest.fail('Checked rollback did not start: '+call(['logs',name],check=False).stdout)
    def stop(name):
        waiter=subprocess.Popen(docker+['wait',name],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,env=env)
        started=time.monotonic();call(['stop','--timeout','30',name]);out,err=waiter.communicate(timeout=40)
        assert out.strip()=='0',(out,err)
        assert time.monotonic()-started<35
        names.remove(name)
    try:
        # Both containers exist before either readiness wait; no migration at either start.
        first=start();second=start()
        for name in (first,second):ready(name);call(['exec','-i',name,'python','-B','-'],probe)
        assert snapshot()==before
        for name in (first,second):stop(name)
        restarted=start();ready(restarted);call(['exec','-i',restarted,'python','-B','-'],probe);stop(restarted)
        assert snapshot()==before
        print('PASS reconstructed image',image_id,'all-data SHA256',hashlib.sha256(before.encode()).hexdigest())
    finally:
        for name in names:call(['stop','--timeout','30',name],check=False)
