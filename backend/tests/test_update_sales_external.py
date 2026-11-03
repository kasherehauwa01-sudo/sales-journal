"""Audit an operator-supplied updater without storing production shell code in Git.

SALES_UPDATE_TEST_SCRIPT points to the locally downloaded/fixed external artifact.
All Docker/Git/HTTP/disk commands below are fake; production paths are never used.
"""
import json
import os
from pathlib import Path
import subprocess

import pytest
import yaml
from test_schema25_startup import pg24

SCRIPT = os.environ.get('SALES_UPDATE_TEST_SCRIPT')
pytestmark = pytest.mark.skipif(not SCRIPT, reason='External production updater is intentionally not stored in Git')

FAKE = r'''#!/usr/bin/env python3
import copy,json,os,re,signal,subprocess,sys
from pathlib import Path
import yaml
root=Path(os.environ['FAKE_ROOT']);path=root/'state.json'
args=sys.argv[1:];kind=Path(sys.argv[0]).name;failure=os.environ.get('FAILURE','')
def read():return json.loads(path.read_text())
def save(value):
 temp=path.with_name('state.'+str(os.getpid()));temp.write_text(json.dumps(value));os.replace(temp,path)
def event(name,**values):
 with (root/'events').open('a') as f:f.write(json.dumps(dict(event=name,**values))+'\n')
def fail():
 print('SECRET_SENTINEL must stay private',file=sys.stderr);sys.exit(1)
def merge(a,b):
 for k,v in b.items():
  if k=='volumes' and isinstance(v,list) and isinstance(a.get(k),list):
   old={x['target']:x for x in a[k]};old.update({x['target']:x for x in v});a[k]=list(old.values())
  elif isinstance(v,dict) and isinstance(a.get(k),dict):merge(a[k],v)
  else:a[k]=v
def config():
 files=[args[i+1] for i,x in enumerate(args) if x=='-f'];expected=[str(root/'project'/p) for p in ('docker-compose.yml','docker-compose.override.yml','docker-compose.churn-v3u.override.yml')]+[str(root/'bindings.json')]
 assert files[:4]==expected,files
 assert len(files) in (5,6),files
 result={}
 for file in files:merge(result,yaml.safe_load(Path(file).read_text()) or {})
 if failure=='command-alembic':result['services']['backend']['command']=['sh','-c','alembic upgrade head']
 if failure=='recovery-settings-drift' and files[-1].endswith('docker-compose.schema25-recovery.override.yml'):
  result['services']['backend']['environment']['CHURN']='unexpected'
 result['name']='fixture'
 for service in result['services'].values():
  if 'volumes' in service:service['volumes']=sorted(service['volumes'],key=lambda v:v['target'])
 return result,files
if kind=='git':
 if args[0]=='symbolic-ref':print('main')
 elif args[0]=='ls-files':sys.exit(1)
 elif args[0]=='rev-parse':print('a123456789bc')
 elif args[0]=='diff' and failure=='dirty':fail()
 elif args[0]=='pull' and failure=='pull':fail()
 sys.exit(0)
if kind=='df':
 print('Filesystem 1024-blocks Used Available Capacity Mounted on')
 print('fixture 9000000 1 '+('100' if failure=='disk' else '8999999')+' 1% /');sys.exit(0)
if kind=='curl':
 state=read();service='backend' if '8010' in args[-1] else 'frontend';image=state['containers'][service]
 bad=(service=='backend' and image=='new-backend' and failure in ('backend-health','rollback-health','rollback-up-before','rollback-wrong-runtime','recovery-settings-drift','pg-lost','pg-rollback-busy')) or (service=='frontend' and image=='new-frontend' and failure=='frontend-health')
 if service=='backend' and image=='old-backend' and state.get('old_recreated'):
  bad=bad or failure=='rollback-health' or (failure!='rollback-wrong-runtime' and state['revision']=='20261030_25' and not state['runtime']['backend'].get('entrypoint'))
 print('503' if bad else '200',end='');sys.exit(0)
if kind=='mv':
 result=subprocess.run(['/usr/bin/mv',*args]);sys.exit(result.returncode)
if kind!='docker':raise AssertionError(kind)
if args[:2]==['info','--format']:print(root);sys.exit(0)
if args[0]=='exec':
 state=read();session=state.get('sessions',0)+1;state['sessions']=session;state['held']=[];save(state)
 held=[]
 event('guard-start',session=session)
 try:
  for line in sys.stdin:
   if line.strip()==r'\q':break
   if 'GUARD_OK' in line:
    state=read()
    if failure in ('pg-lost','pg-rollback-busy') and state.get('containers',{}).get('backend')=='new-backend' and session==1:sys.exit(2)
    assert '734190117' in line and '=3' in line
    print('GUARD_OK' if len(held)==3 else 'LOST',flush=True)
   else:
    key=int(re.search(r'pg_try_advisory_lock\((\d+)\)',line).group(1))
    assert key==[734190221,734190220,734190117][len(held)]
    if (failure=='lock-busy' and key==734190117) or (failure=='pg-rollback-busy' and session>1 and key==734190117):print('BUSY',flush=True);continue
    held.append(key);state=read();state['held']=held;save(state)
    print({734190221:'WORKER_OK',734190220:'SOURCE_OK',734190117:'CATALOG_OK'}[key],flush=True)
 finally:
  state=read();state['held']=[];save(state);event('guard-stop',session=session)
 sys.exit(0)
state=read()
if args[0]=='ps':
 if state.get('job'):print(state['job']['name'])
 sys.exit(0)
if args[0]=='stop':
 assert state.get('job') and args[-1]==state['job']['name']
 state['job']=None;save(state);event('migration-stop');sys.exit(0)
if args[0]=='inspect':
 name=args[-1]
 if name.startswith('sales-schema25-migrate-'):
  job=state.get('job')
  if not job or job['name']!=name:sys.exit(1)
  print(job['label'] if 'Labels' in args[2] else 'true');sys.exit(0)
 service=name.removeprefix('sales-journal-')
 if service not in state['containers']:sys.exit(1)
 if '-f' in args:
  print('true' if 'Running' in args[args.index('-f')+1] else state['containers'][service]);sys.exit(0)
 runtime=state['runtime'][service]
 print(json.dumps([{'Config':{'Entrypoint':runtime.get('entrypoint'),'Cmd':runtime.get('command'),
  'Env':[k+'='+str(v) for k,v in runtime.get('environment',{}).items()]},
  'Mounts':[{'Destination':v['target'],'Source':v['source'],'RW':not v.get('read_only',False)} for v in runtime.get('volumes',[])]}]))
 sys.exit(0)
if args[:2]==['image','inspect']:
 if 'entrypoint' in args[args.index('-f')+1]:
  candidate=args[-1]=='new-backend'
  print(json.dumps({'entrypoint':(['/unsafe-entrypoint'] if failure=='image-entrypoint' or (failure=='old-entrypoint' and not candidate) else None),
   'cmd':(['sh','-c','alembic upgrade head'] if candidate and failure=='image-alembic' else ['python','-B','start_backend.py']),
   'workdir':'/app'}));sys.exit(0)
 ref=args[-1]
 if ref in state['images']:print(state['images'][ref]);sys.exit(0)
 if ref in ('old-backend','old-frontend','new-backend','new-frontend'):print(ref);sys.exit(0)
 sys.exit(1)
if args[:2]==['image','tag']:
 ref,tag=args[2:];state['images'][tag]=state['images'].get(ref,ref);save(state);event('tag');sys.exit(0)
if args[0]!='compose':raise AssertionError(args)
merged,files=config();verb=next(x for x in args if x in ('config','build','run','up'))
if verb=='config':
 if failure=='applied-config' and files[-1]==str(root/'project/docker-compose.report-fix.override.yml') and 'release-' in merged['services']['backend']['image']:fail()
 if '--format' in args:print(json.dumps(merged))
 sys.exit(0)
assert state['held']==[734190221,734190220,734190117],(verb,state)
if verb=='build':
 service=args[-1];event('build',service=service)
 if failure=='build':fail()
 assert len(files)==6 and 'build' in merged['services'][service]
 state['images'][merged['services'][service]['image']]='new-'+service;save(state);sys.exit(0)
if verb=='run':
 code=args[args.index('-c')+1]
 if code.startswith('import app.main;'):event('imports');sys.exit(0)
 action=args[args.index('-c')+2];expected=args[args.index('-c')+3]
 launcher=args[args.index('-c')+5];image=state['images'][merged['services']['backend']['image']]
 assert 'pg_stat_activity' in code and '734190117' in code
 assert 'ANALYTICS_AUTOMATION_ENABLED=false' in args
 event('schema-job',action=action,image=image,expected=expected)
 if action=='status':print(state['revision']);sys.exit(0)
 if action=='check':
  if (failure=='old-preflight' and image=='old-backend') or (failure=='candidate-check' and image=='new-backend' and state['revision']=='20261030_25'):fail()
  assert expected==state['revision']
  sys.exit(0)
 if action=='migrate':
  assert state['revision']=='20261022_24'
  if failure=='migration-24':fail()
  if failure in ('migration-running','migration-running-25'):
   state['job']={'name':args[args.index('--name')+1],'label':args[args.index('--label')+1].split('=',1)[1]}
   if failure=='migration-running-25':state['revision']='20261030_25'
   save(state);fail()
  state['revision']='20261030_25';save(state)
  if failure=='migration-25':fail()
  if failure in ('migration-term','migration-int','migration-kill'):os.kill(os.getppid(),{'migration-term':signal.SIGTERM,'migration-int':signal.SIGINT,'migration-kill':signal.SIGKILL}[failure])
  sys.exit(0)
 raise AssertionError(action)
if verb=='up':
 service=args[-1];image=state['images'][merged['services'][service]['image']];event('up',service=service,image=image)
 if failure=='rollback-up-before' and image=='old-backend':fail()
 state['containers'][service]=image;state['runtime'][service]=copy.deepcopy(merged['services'][service])
 if failure=='rollback-wrong-runtime' and image=='old-backend':state['runtime'][service].pop('entrypoint',None)
 if service=='backend' and image=='old-backend':state['old_recreated']=True
 save(state)
 if failure=='backend-up' and image=='new-backend':fail()
 if failure in ('backend-term','backend-int') and image=='new-backend':os.kill(os.getppid(),signal.SIGTERM if failure=='backend-term' else signal.SIGINT)
 sys.exit(0)
raise AssertionError(args)
'''


@pytest.fixture
def updater(tmp_path):
    root=tmp_path;project=root/'project';project.mkdir()
    for service in ('backend','frontend'):
        (project/service).mkdir();(project/service/'Dockerfile').write_text('# fixture\n')
    backend=Path(__file__).parents[1]
    (project/'backend/start_backend.py').write_bytes((backend/'start_backend.py').read_bytes())
    (project/'.env').write_text('SECRET=SECRET_SENTINEL\n')
    runtime={s:{'image':'old-'+s,'build':{'context':'./'+s}} for s in ('backend','frontend')}
    runtime['backend'].update(environment={'KEEP':'mtls','PRIVATE_TOKEN':'SECRET_SENTINEL'},
        volumes=[{'type':'bind','source':'/private/helper','target':'/run/mtls','read_only':True}],restart='unless-stopped')
    (project/'docker-compose.yml').write_text(yaml.safe_dump({'services':runtime}))
    (project/'docker-compose.override.yml').write_text('services: {backend: {group_add: ["980"]}}\n')
    (project/'docker-compose.churn-v3u.override.yml').write_text('services: {backend: {environment: {CHURN: SHADOW}}}\n')
    (root/'bindings.json').write_text('{"services":{"backend":{"environment":{"CALLTRACK_SECRET":"SECRET_SENTINEL"}}}}')
    active=project/'docker-compose.report-fix.override.yml';active.write_text('# preserve comments\nservices:\n  backend:\n    image: old-backend\n')
    original=active.read_bytes()
    (root/'meminfo').write_text('MemAvailable: 999999 kB\n')
    (root/'working-update_sales.sh').write_text('DO NOT MODIFY\n')
    state={'revision':'20261022_24','images':{'old-backend':'old-backend','old-frontend':'old-frontend','prepared':'new-backend'},
        'containers':{'backend':'old-backend','frontend':'old-frontend'},'runtime':runtime,'held':[]}
    (root/'state.json').write_text(json.dumps(state))
    binpath=root/'bin';binpath.mkdir()
    for name in ('docker','git','curl','df','mv'):
        file=binpath/name;file.write_text(FAKE);file.chmod(0o755)
    env=dict(os.environ,PATH=str(binpath)+os.pathsep+os.environ['PATH'],FAKE_ROOT=str(root),
        SALES_PROJECT_DIR=str(project),SALES_CALLTRACK_COMPOSE=str(root/'bindings.json'),
        SALES_UPDATE_LOCK_FILE=str(root/'lock'),SALES_UPDATE_BACKUP_ROOT=str(root/'backup'),
        SALES_UPDATE_MEMINFO_FILE=str(root/'meminfo'),SALES_UPDATE_HEALTH_ATTEMPTS='2',SALES_UPDATE_HEALTH_DELAY='0',SALES_UPDATE_GUARD_TIMEOUT='2')
    def run(failure='',args=('--backend-only','--migrate-24-to-25'),revision='20261022_24',prepared=False):
        current=json.loads((root/'state.json').read_text());current['revision']=revision;(root/'state.json').write_text(json.dumps(current))
        if prepared:env['SALES_UPDATE_BACKEND_IMAGE']='prepared'
        result=subprocess.run(['bash',str(SCRIPT),*args],env=dict(env,FAILURE=failure),capture_output=True,text=True,timeout=25)
        assert 'SECRET_SENTINEL' not in result.stdout+result.stderr
        assert (root/'working-update_sales.sh').read_text()=='DO NOT MODIFY\n'
        events=[json.loads(line) for line in (root/'events').read_text().splitlines()] if (root/'events').exists() else []
        final=json.loads((root/'state.json').read_text())
        return result,final,events
    return root,project,active,original,run


@pytest.mark.parametrize('failure',['build','old-preflight','migration-24','dirty','disk','pull','lock-busy','image-entrypoint','image-alembic','command-alembic','old-entrypoint'])
def test_failures_before_commit_preserve_old_container_and_yaml(updater,failure):
    root,project,active,original,run=updater
    result,state,events=run(failure)
    assert result.returncode!=0, result.stdout+result.stderr
    assert state['revision']=='20261022_24' and state['containers']['backend']=='old-backend'
    assert active.read_bytes()==original
    assert not any(e['event']=='up' for e in events)
    assert not (project/'docker-compose.schema25-recovery.override.yml').exists()


@pytest.mark.parametrize('failure',['migration-25','migration-term','migration-int','backend-up','backend-health','backend-term','backend-int','applied-config','candidate-check','pg-lost'])
def test_failure_after_schema_commit_restores_old_image_with_compatible_runtime(updater,failure):
    root,project,active,original,run=updater
    result,state,events=run(failure)
    assert result.returncode!=0
    assert state['revision']=='20261030_25'
    assert state['containers']['backend']=='old-backend', result.stdout+result.stderr
    assert state['runtime']['backend']['entrypoint']==['python','-B','/run/sales-schema25-recovery/start_backend.py'], result.stdout+result.stderr
    assert (project/'docker-compose.schema25-recovery.override.yml').is_file()
    assert 'MAINTENANCE' in result.stdout and 'КРИТИЧЕСКАЯ' not in result.stdout
    assert active.read_bytes()==original


def test_migration_requires_explicit_consent(updater):
    root,project,active,original,run=updater
    result,state,events=run(args=('--backend-only',))
    assert result.returncode!=0 and state['revision']=='20261022_24'
    assert not any(e.get('action')=='migrate' for e in events)


@pytest.mark.parametrize('revision,args',[('20261022_24',('--backend-only','--migrate-24-to-25')),('20261030_25',('--backend-only',))])
def test_success_and_idempotent_schema25_do_not_enable_automation_or_activate_recovery(updater,revision,args):
    root,project,active,original,run=updater
    result,state,events=run(revision=revision,args=args)
    assert result.returncode==0,result.stdout+result.stderr
    assert state['revision']=='20261030_25' and state['containers']['backend']=='new-backend'
    assert not (project/'docker-compose.schema25-recovery.override.yml').exists()
    assert sum(e.get('action')=='migrate' for e in events)==int(revision=='20261022_24')
    assert state['runtime']['backend']['environment']['CHURN']=='SHADOW'
    assert not (project/'.sales-update-recovery-required').exists()


def test_prepared_image_avoids_backend_build(updater):
    root,project,active,original,run=updater
    result,state,events=run(prepared=True)
    assert result.returncode==0,result.stdout+result.stderr
    assert not any(e['event']=='build' for e in events)


def test_frontend_failure_keeps_committed_backend_and_schema(updater):
    root,project,active,original,run=updater
    result,state,events=run('frontend-health',args=('--migrate-24-to-25',))
    assert result.returncode!=0
    assert state['revision']=='20261030_25' and state['containers']['backend']=='new-backend'
    assert state['containers']['frontend']=='old-frontend'
    assert not any(e['event']=='up' and e.get('image')=='old-backend' for e in events)
    assert not (project/'docker-compose.schema25-recovery.override.yml').exists()


@pytest.mark.parametrize('failure',['rollback-health','rollback-up-before','rollback-wrong-runtime','recovery-settings-drift','pg-rollback-busy'])
def test_failed_rollback_is_critical_preserves_artifacts_and_does_not_claim_success(updater,failure):
    root,project,active,original,run=updater
    # rollback-up-before needs an initiating failure as well; that case also makes
    # the new health probe fail in the fixture below.
    result,state,events=run(failure)
    if failure=='rollback-up-before' and result.returncode==0:
        pytest.fail('Fixture must trigger candidate failure before rollback failure')
    assert result.returncode!=0 and 'КРИТИЧЕСКАЯ' in result.stdout,result.stdout+result.stderr
    assert list((root/'backup').glob('update.*/checkpoint.yml'))
    assert list((root/'backup').glob('update.*/rollback-start_backend.py'))
    assert (project/'.sales-update-recovery-required').is_file()


def test_recovery_file_blocks_repeat_update_without_deleting_it(updater):
    root,project,active,original,run=updater
    recovery=project/'docker-compose.schema25-recovery.override.yml';recovery.write_text('preserve recovery\n')
    result,state,events=run()
    assert result.returncode!=0 and recovery.read_text()=='preserve recovery\n'
    assert not events


def test_sigkill_after_schema_commit_leaves_durable_marker_and_blocks_retry(updater):
    root,project,active,original,run=updater
    result,state,events=run('migration-kill')
    assert result.returncode!=0 and state['revision']=='20261030_25'
    marker=project/'.sales-update-recovery-required'
    assert marker.is_file() and marker.read_text().startswith('run_id=')
    result,state,again=run(revision='20261030_25')
    assert result.returncode!=0 and len(again)==len(events)
    assert state['containers']['backend']=='old-backend'


def test_low_memory_stops_before_build_or_migration(updater):
    root,project,active,original,run=updater
    (root/'meminfo').write_text('MemAvailable: 100000 kB\n')
    result,state,events=run()
    assert result.returncode!=0 and state['revision']=='20261022_24'
    assert not any(e['event'] in {'build','up','schema-job'} for e in events)


@pytest.mark.parametrize('committed',[False,True])
def test_orphaned_migration_is_stopped_before_revision_classification_and_recovery(updater,committed):
    root,project,active,original,run=updater
    result,state,events=run('migration-running-25' if committed else 'migration-running')
    assert result.returncode!=0
    assert any(e['event']=='migration-stop' for e in events)
    assert not state.get('job')
    assert state['revision']==('20261030_25' if committed else '20261022_24')
    assert state['containers']['backend']=='old-backend'
    if committed:
        assert 'MAINTENANCE' in result.stdout and 'КРИТИЧЕСКАЯ' not in result.stdout
    else:
        assert not any(e['event']=='up' for e in events)
        assert not (project/'.sales-update-recovery-required').exists()


def test_frontend_only_never_migrates_or_restarts_backend(updater):
    root,project,active,original,run=updater
    result,state,events=run(args=('--frontend-only',))
    assert result.returncode==0,result.stdout+result.stderr
    assert state['revision']=='20261022_24' and state['containers']['backend']=='old-backend'
    assert not any(e.get('action')=='migrate' or e.get('service')=='backend' for e in events)


def test_explicit_legacy_all_mode_still_supported(updater):
    root,project,active,original,run=updater
    result,state,events=run(args=('all','--migrate-24-to-25'))
    assert result.returncode==0,result.stdout+result.stderr
    assert state['containers']=={'backend':'new-backend','frontend':'new-frontend'}


def external_prefix():
    # Read the reviewed external artifact at test time; never commit its contents.
    return Path(SCRIPT).read_text().rsplit('\nmain "$@"',1)[0]


@pytest.mark.parametrize('eof',[False,True])
def test_real_coproc_duplicate_fds_survive_original_close(tmp_path,eof):
    source=Path(SCRIPT).read_text()
    functions=source[source.index('guard_send() ('):source.index('ensure_rollback_guard() {')]
    harness=r'''
set -Eeuo pipefail
BACKUP_DIR=$1
GUARD_TIMEOUT=2
GUARD_PID='' GUARD_WRITE='' GUARD_READ='' GUARD_APPNAME=coproc-test
trap 'release_guard' EXIT
docker() {
 bash --noprofile --norc -c '
  while IFS= read -r query; do
   case "$query" in
    *pg_try_advisory_lock\(734190221\)*) response=WORKER_OK ;;
    *pg_try_advisory_lock\(734190220\)*) response=SOURCE_OK ;;
    *pg_try_advisory_lock\(734190117\)*) response=CATALOG_OK ;;
    *count\(DISTINCT\ objid\)*) response=GUARD_OK ;;
    "\q") printf "quit\n" >> "$1"; exit 0 ;;
    *) exit 42 ;;
   esac
   printf "%s\n" "$response"
   printf "answered\n" >> "$1"
  done
  printf "eof\n" >> "$1"
 ' child "$BACKUP_DIR/child-events"
}
export -f docker
mkdir "$BACKUP_DIR/bin"
printf '#!/bin/bash\ndocker "$@"\n' > "$BACKUP_DIR/bin/docker"
chmod +x "$BACKUP_DIR/bin/docker"
export BACKUP_DIR
export PATH="$BACKUP_DIR/bin:$PATH"
start_seasonality_guard
saved_pid=$GUARD_PID
[[ ! -e /proc/$$/fd/${SEASON_GUARD[1]} ]]
[[ ! -e /proc/$$/fd/${SEASON_GUARD[0]} ]]
[[ -e /proc/$$/fd/$GUARD_WRITE && -e /proc/$$/fd/$GUARD_READ ]]
for ((i=0;i<12;i++)); do assert_guard; done
'''
    if eof:
        harness+=r'''
exec {GUARD_WRITE}>&-
GUARD_WRITE=''
if IFS= read -r -t 2 unexpected <&"$GUARD_READ"; then exit 43; fi
wait "$saved_pid"
'''
    harness+=r'''
release_guard
! kill -0 "$saved_pid" 2>/dev/null
[[ -z "$GUARD_PID$GUARD_WRITE$GUARD_READ" ]]
trap - EXIT
'''
    result=subprocess.run(['bash','--noprofile','--norc','-c',functions+harness,'test',str(tmp_path)],
        capture_output=True,text=True,timeout=12)
    assert result.returncode==0,result.stdout+result.stderr
    events=(tmp_path/'child-events').read_text().splitlines()
    assert events.count('answered')==16
    assert events[-1]==('eof' if eof else 'quit')


def schema_payload():
    result=subprocess.run(['bash','-c',external_prefix()+'\nschema_python\n'],capture_output=True,text=True,timeout=5)
    assert result.returncode==0
    compile(result.stdout,'external-schema-payload','exec')
    return result.stdout


def test_schema_python_payload_compiles_and_suppresses_raw_errors():
    payload=schema_payload()
    assert 'pg_stat_activity' in payload and 'SET TRANSACTION READ ONLY' in payload
    assert 'DEPLOY_SCHEMA_JOB_FAILED' in payload


@pytest.mark.skipif(os.environ.get('SCHEMA25_COMPOSE_TESTS')!='1',reason='Explicit local Compose client test opt-in')
def test_recovery_delta_matches_real_compose_normalization(updater):
    root,project,active,original,run=updater
    result,state,events=run('migration-25')
    assert result.returncode!=0 and 'MAINTENANCE' in result.stdout
    env=dict(os.environ)
    for key in ('DOCKER_HOST','DOCKER_CONTEXT','DOCKER_TLS','DOCKER_TLS_VERIFY','DOCKER_CERT_PATH','COMPOSE_PROFILES'):
        env.pop(key,None)
    files=[project/'docker-compose.yml',project/'docker-compose.override.yml',
        project/'docker-compose.churn-v3u.override.yml',root/'bindings.json',active]
    base=['docker','--host=unix:///var/run/docker.sock','compose','--env-file','/dev/null']
    for file in files:base+=['-f',str(file)]
    metadata=subprocess.run(['bash','-c',external_prefix()+'\nconfig_metadata_python\n'],capture_output=True,text=True,timeout=5)
    assert metadata.returncode==0
    def inspect(extra):
        config=subprocess.run(base+extra+['config','--format','json'],env=env,capture_output=True,text=True,timeout=15)
        assert config.returncode==0
        summary=subprocess.run(['python3','-c',metadata.stdout],input=config.stdout,capture_output=True,text=True,timeout=5)
        assert summary.returncode==0
        return json.loads(summary.stdout)
    baseline=inspect([])
    recovery=inspect(['-f',str(project/'docker-compose.schema25-recovery.override.yml')])
    assert baseline['recovery_invariants_sha256']==recovery['recovery_invariants_sha256']
    assert all(recovery['recovery_contract'].values())


@pytest.mark.parametrize('matching_session',[True,False])
def test_native_schema_job_requires_the_same_three_lock_session(pg24,matching_session):
    import hashlib
    import sys
    from uuid import uuid4
    import sqlalchemy as sa
    from start_backend import revision, SCHEMA_LOCK
    engine,schema=pg24
    launcher=Path(__file__).parents[1]/'start_backend.py'
    checksum=hashlib.sha256(launcher.read_bytes()).hexdigest()
    nonce='schema-update-'+uuid4().hex
    payload=schema_payload()
    def run(action,expected=''):
        return subprocess.run([sys.executable,'-B','-c',payload,action,expected,
            nonce if matching_session else nonce+'-wrong',str(launcher),checksum],
            env=dict(os.environ),capture_output=True,text=True,timeout=30)
    with engine.connect() as guard:
        guard.execute(sa.text("SELECT set_config('application_name',:nonce,false)"),{'nonce':nonce})
        for key in (734190221,734190220,734190117):
            assert guard.scalar(sa.text('SELECT pg_try_advisory_lock(:key)'),{'key':key})
        guard.commit()
        try:
            status=run('status')
            if not matching_session:
                assert status.returncode==1 and 'DEPLOY_SCHEMA_JOB_FAILED' in status.stderr
                with engine.connect() as conn: assert revision(conn)=='20261022_24'
                return
            assert status.returncode==0 and status.stdout.strip()=='20261022_24',status.stderr
            guard.execute(sa.text('SELECT pg_advisory_lock(:key)'),{'key':SCHEMA_LOCK});guard.commit()
            try:
                blocked=run('status')
                assert blocked.returncode==1 and 'DEPLOY_SCHEMA_JOB_FAILED' in blocked.stderr
            finally:
                guard.execute(sa.text('SELECT pg_advisory_unlock(:key)'),{'key':SCHEMA_LOCK});guard.commit()
            result=run('migrate','20261030_25')
            assert result.returncode==0,result.stdout+result.stderr
            assert run('check','20261030_25').returncode==0
            with engine.connect() as conn: assert revision(conn)=='20261030_25'
        finally:
            for key in (734190117,734190220,734190221):
                guard.execute(sa.text('SELECT pg_advisory_unlock(:key)'),{'key':key})
            guard.commit()
