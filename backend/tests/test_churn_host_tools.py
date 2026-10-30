"""Host utilities tested against local doubles/files only; no real services/DB."""
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import time

import pytest

ROOT=Path(__file__).resolve().parents[1]

def module(name):
    spec=importlib.util.spec_from_file_location(name,ROOT/'scripts'/f'{name}.py')
    result=importlib.util.module_from_spec(spec);spec.loader.exec_module(result)
    return result

observer=module('collect_churn_status')
patcher=module('prepare_churn_integrity_patch')
NOW=datetime(2026,10,30,7,tzinfo=timezone.utc)


def runner_for(*,message='PILOT_STOP: Reviewed code changed: api/manager_tasks_service.php SECRET_TOKEN',result='exit-code'):
    calls=[]
    def runner(args,**kwargs):
        calls.append((args,kwargs))
        if args[0]=='journalctl':
            return json.dumps({'_SYSTEMD_INVOCATION_ID':'a'*32,'__REALTIME_TIMESTAMP':str(int(NOW.timestamp()*1000000)),'MESSAGE':message})
        if observer.TIMER in args:
            return 'LoadState=loaded\nActiveState=active\nUnitFileState=enabled\nNextElapseUSecRealtime=Sat 2026-10-31 06:00:00 UTC\nTimersCalendar={ OnCalendar=*-*-* 09:00:00 Europe/Moscow ; next_elapse=... }\n'
        return f'LoadState=loaded\nActiveState=inactive\nResult={result}\nExecMainStartTimestamp=Fri 2026-10-30 07:00:00 UTC\nExecMainExitTimestamp=Fri 2026-10-30 07:00:01 UTC\nInvocationID={"a"*32}\n'
    return runner,calls


def test_host_status_uses_fixed_read_only_commands_and_redacts_logs():
    runner,calls=runner_for()
    systemd,codes,history=observer.host_observation(runner)
    assert systemd['timer_enabled'] and systemd['schedule']=='09:00 Europe/Moscow'
    assert systemd['next_run']=='2026-10-31T06:00:00+00:00'
    assert 'REVIEWED_CODE_CHANGED' in codes and 'PILOT_STOP' in codes
    assert 'SECRET_TOKEN' not in json.dumps([systemd,codes,history])
    assert all(args[0] in {'systemctl','journalctl'} for args,_ in calls)
    assert all('start' not in args and 'restart' not in args and 'enable' not in args for args,_ in calls)


def test_lifetime_log_is_blocked_not_success_or_delivery():
    runner,_=runner_for(message='PILOT_COMPLETE: total lifetime budget exhausted',result='success')
    state,codes,history=observer.host_observation(runner)
    assert codes==['LIFETIME_BUDGET_EXHAUSTED']
    assert history[0]['result']=='blocked' and state['last_success'] is None
    assert 'delivered' not in history[0]


def test_failed_observation_never_claims_timer_enabled():
    def fail(*a,**kw):raise observer.ObservationError('AUTHORIZATION_FAILED')
    state,codes,history=observer.host_observation(fail)
    assert state['timer_enabled'] is None and history==[]
    assert codes


def test_current_success_does_not_reuse_historical_stop():
    runner,_=runner_for(message='previous failure')
    def current(args,**kwargs):
        if args[0]=='journalctl':
            return json.dumps({'_SYSTEMD_INVOCATION_ID':'b'*32,'__REALTIME_TIMESTAMP':str(int(NOW.timestamp()*1000000)-1000000),'MESSAGE':'PILOT_STOP: Reviewed code changed SECRET'})
        return runner(args,**kwargs).replace('Result=exit-code','Result=success')
    state,codes,history=observer.host_observation(current)
    assert not codes and state['last_success'] is not None
    assert any(h['result']=='failed' for h in history)


def test_integrity_checks_all_entries_and_refuses_unknown_schema(tmp_path,monkeypatch):
    api=tmp_path/'api';api.mkdir();a=api/'manager_tasks_service.php';a.write_bytes(b'reviewed source')
    b=api/'other.php';b.write_bytes(b'other reviewed source')
    entries={str(a):hashlib.sha256(a.read_bytes()).hexdigest(),str(b):hashlib.sha256(b.read_bytes()).hexdigest()}
    monkeypatch.setattr(observer,'private_json',lambda p:entries)
    assert observer.integrity(tmp_path/'manifest',tmp_path)=='match'
    b.write_bytes(b'changed')
    assert observer.integrity(tmp_path/'manifest',tmp_path)=='mismatch'
    monkeypatch.setattr(observer,'private_json',lambda p:{'unexpected':['schema']})
    assert observer.integrity(tmp_path/'manifest',tmp_path)=='unknown'


def test_receiver_missing_or_unavailable_never_returns_fake_counts(tmp_path,monkeypatch):
    assert observer.receiver_observation(None,None,now=NOW)=={'status':'unknown'}
    defaults=tmp_path/'db.cnf';defaults.write_text('private synthetic connection');defaults.chmod(0o600)
    real_lstat=Path.lstat
    def root_owner(path):
        info=real_lstat(path)
        from types import SimpleNamespace
        return SimpleNamespace(st_mode=info.st_mode,st_uid=0)
    monkeypatch.setattr(Path,'lstat',root_owner)
    def fail(*a,**k):raise observer.ObservationError('AUTHORIZATION_FAILED')
    assert observer.receiver_observation(defaults,None,now=NOW,runner=fail)=={'status':'unauthorized'}


def test_receiver_queries_are_read_only_and_bound_to_moscow_day(tmp_path,monkeypatch):
    defaults=tmp_path/'db.cnf';defaults.write_text('not used');defaults.chmod(0o600)
    from types import SimpleNamespace
    monkeypatch.setattr(Path,'lstat',lambda path:SimpleNamespace(st_mode=0o100600,st_uid=0))
    calls=[]
    def runner(args,**kw):
        calls.append((args,kw))
        return '\n'.join(json.dumps(row) for row in [
            dict(kind='policy',approved=1,min_confidence='.4'),
            dict(kind='lifetime',approved=1,limit=10,used=2),
            dict(kind='daily',approved=1,limit=5,used=2,ledger_used=2,business_date='2026-10-31',policy_version='aviator-churn-pilot/1'),
            dict(kind='summary',accepted_signals=3,blocked_signals=1,tasks_created=2),
            dict(kind='receipt',release_id='release-1',accepted=3,blocked=1,tasks=2)])
    result=observer.receiver_observation(defaults,None,now=datetime(2026,10,30,22,tzinfo=timezone.utc),runner=runner)
    assert result['status']=='available' and result['receipts'][0]['accepted']==3
    sql=calls[0][1]['input_text']
    assert "business_date='2026-10-31'" in sql and 'SET TRANSACTION READ ONLY' in sql and 'ROLLBACK;' in sql
    assert not any(word in sql.upper() for word in ['UPDATE ','INSERT ','DELETE ','ALTER ','CREATE ','FOR UPDATE'])
    assert calls[0][0][1].startswith('--defaults-file=')
    assert 'not used' not in str(calls)


def test_bounded_command_and_cleanup_of_real_local_children():
    assert observer.command([sys.executable,'-c','print("ok")']).strip()=='ok'
    with pytest.raises(observer.ObservationError):
        observer.command([sys.executable,'-c','print("x"*200000)'])
    started=time.monotonic()
    with pytest.raises(observer.ObservationError):
        observer.command([sys.executable,'-c','import time;time.sleep(10)'],timeout=.1)
    assert time.monotonic()-started<2
    with pytest.raises(observer.ObservationError) as error:
        observer.command([sys.executable,'-c','import sys;sys.stderr.write("Access denied SECRET");sys.exit(1)'])
    assert error.value.code=='AUTHORIZATION_FAILED' and 'SECRET' not in str(error.value)


def test_prepare_patch_changes_only_reviewed_hash_and_keeps_input(monkeypatch,tmp_path):
    content=b'<?php synthetic reviewed copy\n'
    digest=hashlib.sha256(content).hexdigest()
    monkeypatch.setattr(patcher,'NEW',digest)
    manifest=json.dumps({'/path/api/manager_tasks_service.php':patcher.OLD,'/other.php':'a'*64},indent=2)+'\n'
    result=patcher.prepare(manifest.encode(),content,'synthetic-reviewer')
    assert patcher.OLD in result and digest in result and 'Reviewed-by: synthetic-reviewer' in result
    original=tmp_path/'runtime-code-sha256.json';original.write_text(manifest)
    patch=tmp_path/'review.patch';patch.write_text(result)
    process=subprocess.run(['patch','--dry-run','--batch','-p0','-i',str(patch)],cwd=tmp_path,capture_output=True,text=True)
    assert process.returncode==0,process.stderr+process.stdout
    assert original.read_text()==manifest


@pytest.mark.parametrize('manifest,php',[
    ({'api/manager_tasks_service.php':'a'*64},b'wrong'),
    ({'api/manager_tasks_service.php':patcher.OLD,'token':'DO_NOT_EXPOSE'},b'wrong'),
])
def test_patch_refuses_unreviewed_source_and_secret_containing_schema(manifest,php):
    with pytest.raises(ValueError):patcher.prepare((json.dumps(manifest)+'\n').encode(),php,'reviewer')


def test_numeric_customer_data_is_not_misread_as_http_authorization():
    assert 'AUTHORIZATION_FAILED' not in observer.log_codes('client 401587 processed')
    assert 'AUTHORIZATION_FAILED' in observer.log_codes('HTTP 401: rejected')


def test_secret_schema_is_rejected_even_with_a_reviewed_php_copy(monkeypatch):
    content=b'synthetic reviewed bytes'
    monkeypatch.setattr(patcher,'NEW',hashlib.sha256(content).hexdigest())
    with pytest.raises(ValueError):
        patcher.prepare((json.dumps({'api/manager_tasks_service.php':patcher.OLD,'token':'SECRET'})+'\n').encode(),content,'reviewer')


def test_docker_source_permissions_do_not_widen_secret_files(tmp_path):
    import os,re,stat
    text=(ROOT/'Dockerfile').read_text()
    prefix=re.search(r'RUN (find /app/.*?)(?= && useradd)',text,re.S).group(1)
    image=tmp_path/'image';image.mkdir()
    for name in ['app','alembic','scripts']:
        directory=image/name;directory.mkdir(mode=0o700)
        (directory/'module.py').write_text('pass\n');(directory/'module.py').chmod(0o600)
        (directory/'private.pem').write_text('SYNTHETIC');(directory/'private.pem').chmod(0o600)
    protected=image/'app'/'keys';protected.mkdir(mode=0o700)
    (protected/'private.py').write_text('SYNTHETIC');(protected/'private.py').chmod(0o600)
    for name in ['start_backend.py','alembic.ini']:
        (image/name).write_text('synthetic');(image/name).chmod(0o600)
    (image/'.env').write_text('SYNTHETIC');(image/'.env').chmod(0o600)
    command=prefix.replace('/app/',str(image)+'/')
    result=subprocess.run(['sh','-c',command],capture_output=True,text=True)
    assert result.returncode==0,result.stderr
    for name in ['app','alembic','scripts']:
        assert stat.S_IMODE((image/name).stat().st_mode)==0o755
        assert stat.S_IMODE((image/name/'module.py').stat().st_mode)==0o644
        assert stat.S_IMODE((image/name/'private.pem').stat().st_mode)==0o600
    assert stat.S_IMODE((image/'.env').stat().st_mode)==0o600
    assert stat.S_IMODE((image/'alembic.ini').stat().st_mode)==0o600
    assert stat.S_IMODE(protected.stat().st_mode)==0o700
    assert stat.S_IMODE((protected/'private.py').stat().st_mode)==0o600
    assert '777' not in text and 'CMD ["python", "-B", "start_backend.py"]' in text


def test_observer_public_cli_refuses_overwriting_unrelated_files(tmp_path):
    target=tmp_path/'policy.json';target.write_text('DO NOT CHANGE')
    args=[sys.executable,str(ROOT/'scripts'/'collect_churn_status.py'),'--output',str(target),
          '--manifest',str(tmp_path/'manifest'),'--calltrack-root',str(tmp_path)]
    result=subprocess.run(args,capture_output=True,text=True)
    assert result.returncode==1 and 'dedicated monitoring output' in result.stderr
    assert target.read_text()=='DO NOT CHANGE'


def test_observer_children_do_not_inherit_service_tokens(monkeypatch):
    monkeypatch.setenv('INTERNAL_API_TOKEN','SECRET-DO-NOT-FORWARD')
    result=observer.command([sys.executable,'-c','import os;print("present" if "INTERNAL_API_TOKEN" in os.environ else "absent")'])
    assert result.strip()=='absent'


def test_weekday_schedule_is_not_reported_as_the_approved_daily_schedule():
    runner,_=runner_for()
    def weekdays(args,**kwargs):return runner(args,**kwargs).replace('OnCalendar=*-*-*','OnCalendar=Mon..Fri')
    state,_,_=observer.host_observation(weekdays)
    assert state['schedule'] is None
