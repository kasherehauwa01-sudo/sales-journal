"""No production access: local files, command doubles and disposable test databases."""
import base64
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT/'backend/scripts'))
import run_churn_automation as host
import collect_churn_status as observer


@pytest.fixture
def reviewed(tmp_path, monkeypatch):
    # These doubles affect ownership/private JSON loading only; integrity reads/hashes real bytes.
    read = lambda path: json.loads(Path(path).read_text())
    monkeypatch.setattr(host, 'private_json', read)
    monkeypatch.setattr(observer, 'private_json', read)
    hostfiles = {str(tmp_path/'host'/str(n)) for n in range(5)}
    monkeypatch.setattr(host, 'REQUIRED_HOST_FILES', hostfiles)
    policy = tmp_path/'policy.json'
    policy.write_text('{}')
    environment = tmp_path/'environment.json'
    environment.write_text(json.dumps({'CALLTRACK_ANALYTICS_POLICY_FILE': str(policy)}))
    config = dict(calltrack_root=str(tmp_path/'calltrack'), manifest=str(tmp_path/'manifest.json'),
                  php_environment=str(environment))
    entries = {}
    for name in host.required_paths(config):
        path = Path(name)
        path.parent.mkdir(parents=True, exist_ok=True)
        if not path.exists():
            path.write_text('reviewed synthetic source')
        entries[name] = hashlib.sha256(path.read_bytes()).hexdigest()
    def save():
        Path(config['manifest']).write_text(json.dumps({'files': entries}))
    save()
    return config, entries, save


@pytest.mark.parametrize('relative', sorted(host.REQUIRED_FILES))
def test_each_mandatory_php_file_must_be_present(reviewed, relative):
    config, entries, save = reviewed
    entries.pop(str(Path(config['calltrack_root'])/relative))
    save()
    with pytest.raises(ValueError, match='COVERAGE_INCOMPLETE'):
        host.verify_code(config)


@pytest.mark.parametrize('relative', sorted(host.REQUIRED_FILES))
def test_each_mandatory_php_mutation_blocks_before_any_command(reviewed, relative):
    config, entries, save = reviewed
    host.verify_code(config)
    Path(config['calltrack_root'], relative).write_text('changed')
    calls = []
    with pytest.raises(ValueError, match='REVIEWED_CODE_CHANGED'):
        host.run(config, {}, runner=lambda *a, **k: calls.append(a))
    assert calls == []


def test_local_executable_configuration_cannot_appear_unreviewed(reviewed):
    config, entries, save = reviewed
    Path(config['calltrack_root'], 'api/config.local.php').write_text('<?php // synthetic')
    with pytest.raises(ValueError, match='COVERAGE_INCOMPLETE'):
        host.verify_code(config)


def test_duplicate_relative_absolute_manifest_alias_is_rejected(reviewed):
    config, entries, save = reviewed
    entries['api/analytics_permanent.php'] = entries[str(Path(config['calltrack_root'])/'api/analytics_permanent.php')]
    save()
    with pytest.raises(ValueError, match='AMBIGUOUS'):
        host.verify_code(config)


def test_host_and_environment_mutations_are_checked(reviewed):
    config, entries, save = reviewed
    target = next(iter(host.REQUIRED_HOST_FILES))
    Path(target).write_text('modified host code')
    with pytest.raises(ValueError, match='REVIEWED_CODE_CHANGED'):
        host.verify_code(config)


def test_missing_config_is_not_silently_defaulted(tmp_path):
    with pytest.raises(OSError):
        host.load_config(tmp_path/'missing.json')


@pytest.mark.parametrize('mode', ['disabled', 'dry_run', '', None])
def test_disabled_receiver_blocks_locally(mode):
    with pytest.raises(ValueError, match='RECEIVER_DISABLED'):
        host.validate_runtime({}, {'CALLTRACK_ANALYTICS_MODE': mode})


def runtime_fixture(tmp_path, monkeypatch):
    monkeypatch.setattr(host, 'private_json', lambda p: json.loads(Path(p).read_text()))
    policy = tmp_path/'policy.json'
    policy.write_text('{}')
    provider = tmp_path/'provider'
    provider.write_text('#!/bin/sh\nexit 1\n')
    provider.chmod(0o700)
    keyring = json.dumps({'synthetic': base64.b64encode(bytes(32)).decode()})
    env = {'CALLTRACK_ANALYTICS_MODE': 'enabled', 'CALLTRACK_ANALYTICS_POLICY_FILE': str(policy),
           'CALLTRACK_UPSTREAM_PUBLIC_KEYS': keyring, 'CALLTRACK_BINDINGS_PUBLIC_KEYS': keyring}
    return env, provider


@pytest.mark.parametrize('name', ['CALLTRACK_UPSTREAM_PUBLIC_KEYS', 'CALLTRACK_BINDINGS_PUBLIC_KEYS'])
def test_missing_public_keyring_blocks(tmp_path, monkeypatch, name):
    env, provider = runtime_fixture(tmp_path, monkeypatch)
    env.pop(name)
    with pytest.raises(ValueError, match='PUBLIC_KEYS_REQUIRED'):
        host.validate_runtime({}, env, evidence_provider=provider)


def test_missing_policy_and_provider_block(tmp_path, monkeypatch):
    env, provider = runtime_fixture(tmp_path, monkeypatch)
    provider.unlink()
    with pytest.raises(ValueError, match='EVIDENCE_PROVIDER'):
        host.validate_runtime({}, env, evidence_provider=provider)
    Path(env['CALLTRACK_ANALYTICS_POLICY_FILE']).unlink()
    with pytest.raises(OSError):
        host.validate_runtime({}, env, evidence_provider=provider)


@pytest.mark.parametrize('missing', ['export_token_file', 'ed25519_key_file', 'hmac_key_file'])
def test_missing_trusted_secret_blocks_before_network(tmp_path, monkeypatch, missing):
    import churn_trusted_ftp as trusted
    env, provider = runtime_fixture(tmp_path, monkeypatch)
    Path(env['CALLTRACK_ANALYTICS_POLICY_FILE']).write_text('{"source_trust_mode":"TRUSTED_IMPORT"}')
    monkeypatch.setattr(trusted, 'read_private', lambda p: Path(p).read_bytes())
    config = {'export_url': 'https://example.test/api/analytics_verified_export.php', 'key_id': 'synthetic'}
    for key in ['export_token_file', 'ed25519_key_file', 'hmac_key_file']:
        path = tmp_path/key
        path.write_bytes(b'synthetic-key-material-'*3)
        config[key] = str(path)
    Path(config[missing]).unlink()
    path = tmp_path/'trusted.json'
    path.write_text(json.dumps(config))
    with pytest.raises(OSError):
        host.validate_runtime({'trusted_import': str(path)}, env)


def transport():
    page = {'job_id': 'job', 'release_id': 'release', 'events': [
        {'event_id': 1, 'event_hash': 'a'*64, 'event_json': '{"immutable":true}'}]}
    receipt = {'job_id': 'job', 'release_id': 'release', 'receipts': [
        {'event_id': 1, 'event_hash': 'a'*64, 'decision': 'TASK_EXISTS', 'task_id': 'task-1',
         'created_for_release': True, 'reasons': []}]}
    return page, receipt


@pytest.mark.parametrize('mutation', ['context', 'hash', 'id', 'duplicate', 'decision', 'shape'])
@pytest.mark.parametrize('stage', ['preflight', 'deliver'])
def test_malformed_receipt_is_never_acknowledged(mutation, stage):
    page, receipt = transport()
    calls = []
    if mutation == 'context': receipt['release_id'] = 'other'
    if mutation == 'hash': receipt['receipts'][0]['event_hash'] = 'b'*64
    if mutation == 'id': receipt['receipts'][0]['event_id'] = True
    if mutation == 'duplicate': receipt['receipts'] *= 2
    if mutation == 'decision': receipt['receipts'][0]['decision'] = 'READY'
    if mutation == 'shape': receipt['receipts'] = None
    def runner(argv, **kw):
        action = argv[argv.index('app.churn_automation')+1] if 'app.churn_automation' in argv else argv[-1]
        calls.append(action)
        if action == 'page': return json.dumps(page)
        if action in ('preflight', 'deliver'):
            return json.dumps(receipt if action == stage else transport()[1])
        raise AssertionError('Must not ACK malformed response')
    with pytest.raises(ValueError, match='RESPONSE_INVALID'):
        host.process_job({'id': 'job', 'status': 'RELEASED'}, {'calltrack_root': '/synthetic'}, {},
                         runner=runner, deadline=host.time.monotonic()+5, verify=lambda c: None)
    assert calls == (['page', 'preflight'] if stage == 'preflight' else ['page', 'preflight', 'deliver'])


def test_lost_ack_replays_exact_event_and_does_not_convert_failure_to_success():
    page, receipt = transport()
    deliveries = []
    fail_ack = True
    def runner(argv, **kw):
        nonlocal fail_ack
        action = argv[argv.index('app.churn_automation')+1] if 'app.churn_automation' in argv else argv[-1]
        if action == 'page': return json.dumps(page)
        if action == 'preflight': return json.dumps(receipt)
        if action == 'deliver':
            deliveries.append(kw['input_text'])
            return json.dumps(receipt)
        if action == 'ack':
            if fail_ack:
                fail_ack = False
                raise observer.ObservationError('RUNTIME_ERROR')
            return '{"status":"SUCCEEDED"}'
        raise AssertionError(action)
    args = ({'id': 'job', 'status': 'RELEASED'}, {'calltrack_root': '/synthetic'}, {})
    with pytest.raises(observer.ObservationError):
        host.process_job(*args, runner=runner, deadline=host.time.monotonic()+5, verify=lambda c: None)
    assert host.process_job(*args, runner=runner, deadline=host.time.monotonic()+5,
                            verify=lambda c: None)['status'] == 'SUCCEEDED'
    assert len(deliveries) == 2 and deliveries[0] == deliveries[1]


def test_changed_code_mid_job_blocks_next_page():
    page, receipt = transport()
    checks = []
    calls = []
    def verify(config):
        checks.append(1)
        if len(checks) > 1: raise ValueError('REVIEWED_CODE_CHANGED')
    def runner(argv, **kw):
        action = argv[argv.index('app.churn_automation')+1] if 'app.churn_automation' in argv else argv[-1]
        calls.append(action)
        return json.dumps(page if action == 'page' else receipt if action in ('preflight', 'deliver')
                          else {'status': 'DELIVERING'})
    with pytest.raises(ValueError, match='CODE_CHANGED'):
        host.process_job({'id': 'job', 'status': 'RELEASED'}, {'calltrack_root': '/synthetic'}, {},
                         runner=runner, deadline=host.time.monotonic()+5, verify=verify)
    assert calls == ['page', 'preflight', 'deliver', 'ack']


def installer():
    spec = importlib.util.spec_from_file_location('install_churn_host', ROOT/'deploy/churn-permanent/install_host.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_installer_default_only_checks(monkeypatch, tmp_path, capsys):
    module = installer()
    calls = []
    monkeypatch.setattr(module.os, 'geteuid', lambda: 0)
    monkeypatch.setattr(module, 'check_host', lambda: calls.append('check'))
    monkeypatch.setattr(module, 'plan', lambda *a: {tmp_path/'must-not-exist': b'synthetic'})
    monkeypatch.setattr(module, 'apply', lambda p: pytest.fail('default must not apply'))
    monkeypatch.setattr(sys, 'argv', ['install_host.py', '--source-mode', 'INDEPENDENT'])
    module.main()
    assert calls == ['check'] and not (tmp_path/'must-not-exist').exists()
    assert 'CHECK ONLY' in capsys.readouterr().out


def test_partial_install_failure_restores_original_files(monkeypatch, tmp_path):
    module = installer()
    original_mkdtemp = module.tempfile.mkdtemp
    monkeypatch.setattr(module.tempfile, 'mkdtemp',
                        lambda **kw: original_mkdtemp(prefix=kw['prefix'], dir=tmp_path))
    a, b = tmp_path/'first', tmp_path/'second'
    a.write_bytes(b'old'); a.chmod(0o640)
    original_copy = module.atomic_copy
    def fail_second(source, target, mode):
        if target == b and isinstance(source, bytes):
            raise OSError('synthetic disk failure')
        return original_copy(source, target, mode)
    monkeypatch.setattr(module, 'atomic_copy', fail_second)
    with pytest.raises(OSError):
        module.apply({a: b'new', b: b'new'})
    assert a.read_bytes() == b'old' and a.stat().st_mode & 0o777 == 0o640 and not b.exists()


def test_pinned_receiver_static_dependency_closure():
    directory = os.environ.get('CALLTRACK_COMPAT_ROOT')
    if not directory:
        pytest.skip('CI supplies the exact reviewed receiver checkout')
    root = Path(directory)
    assert subprocess.check_output(['git', '-C', str(root), 'rev-parse', 'HEAD'], text=True).strip() == '00a8c9c85c7c03835187e152f5b7fd9a60714971'
    pending = ['api/analytics_permanent_cli.php']
    seen = set()
    while pending:
        name = pending.pop()
        if name in seen: continue
        seen.add(name)
        source = (root/name).read_text()
        for dependency in re.findall(r"(?:require|include)(?:_once)?\s+__DIR__\s*\.\s*['\"]/([^'\"]+\.php)['\"]", source):
            if dependency != 'config.local.php':
                pending.append('api/'+dependency)
    assert seen <= host.REQUIRED_FILES, sorted(seen-host.REQUIRED_FILES)
    assert 'api/analytics_manager_pilot_budget.php' in seen


def test_disabled_producer_does_not_resume_historical_job(monkeypatch):
    calls = []
    monkeypatch.setattr(host, 'validate_runtime', lambda *a: None)
    def runner(argv, **kw):
        calls.append(argv)
        if argv[0] == '/usr/bin/systemctl': return 'inactive'
        if argv[0] == '/usr/bin/curl': return '{}'
        if argv[0] == '/usr/bin/php': return '{"receiver":"reachable"}'
        if 'tick' in argv: return json.dumps({'policy': {'enabled': False, 'approved': True},
            'history': [{'id': 'old', 'status': 'RELEASED'}]})
        raise AssertionError('Disabled schedule must not deliver')
    assert host.run({'calltrack_root': '/synthetic'}, {}, runner=runner,
                    verify=lambda c: None)['status'] == 'DISABLED'
    assert not any('page' in argv or 'authorize' in argv or 'deliver' in argv for argv in calls)


def test_job_error_records_failure_and_never_logs_evidence(monkeypatch):
    from datetime import datetime
    from zoneinfo import ZoneInfo
    monkeypatch.setattr(host, 'validate_runtime', lambda *a: None)
    calls = []
    def runner(argv, **kw):
        calls.append(argv)
        if argv[0] == '/usr/bin/systemctl': return 'inactive'
        if argv[0] == '/usr/bin/curl': return '{}'
        if argv[0] == '/usr/bin/php': return '{"receiver":"reachable"}'
        if 'tick' in argv: return json.dumps({'policy': {'enabled': True, 'approved': True},
            'history': [{'id': 'job', 'status': 'RELEASED', 'kind': 'daily', 'counters': {},
                         'business_date': datetime.now(ZoneInfo('Europe/Moscow')).date().isoformat()}]})
        if 'page' in argv: raise observer.ObservationError('RUNTIME_ERROR')
        if 'failure' in argv: return '{}'
        raise AssertionError(argv)
    with pytest.raises(observer.ObservationError):
        host.run({'calltrack_root': '/synthetic'}, {}, runner=runner, verify=lambda c: None)
    assert any('failure' in argv for argv in calls)
    assert not any('ack' in argv for argv in calls)


def test_installation_config_can_be_checked_while_receiver_is_disabled(tmp_path, monkeypatch):
    environment = tmp_path/'environment.json'
    environment.write_text('{"CALLTRACK_ANALYTICS_MODE":"disabled"}')
    environment.chmod(0o600)
    config = tmp_path/'host.json'
    config.write_text(json.dumps({'calltrack_root': str(tmp_path/'root'),
                                  'manifest': str(tmp_path/'manifest'), 'php_environment': str(environment)}))
    monkeypatch.setattr(host, 'private_json', lambda p: json.loads(Path(p).read_text()))
    assert host.load_config(config, require_enabled=False)[1]['CALLTRACK_ANALYTICS_MODE'] == 'disabled'
    with pytest.raises(ValueError, match='RECEIVER_DISABLED'):
        host.load_config(config)


def test_install_plan_checks_package_and_approved_hashes_without_writes(tmp_path, monkeypatch):
    module = installer()
    package = tmp_path/'package'
    root, storage, monitor = tmp_path/'application', tmp_path/'storage', tmp_path/'monitor'
    for directory in (root, storage, monitor): directory.mkdir()
    mapping = {name: str(tmp_path/'targets'/Path(target).name) for name, target in module.MAPPING.items()}
    hashes = {}
    for name in set(mapping) | {'deploy/churn-permanent/install_host.py'}:
        path = package/name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(('reviewed '+name).encode())
        hashes[name] = module.digest(path)
    (package/'deploy/churn-permanent/host-package.sha256.json').write_text(json.dumps(hashes))
    entries = {target: hashes[name] for name, target in mapping.items()}
    config = {'calltrack_root': str(root), 'trusted_import': str(tmp_path/'trusted.json')}
    environment = {'CALLTRACK_ANALYTICS_MODE': 'disabled', 'CALLTRACK_STORAGE_DIR': str(storage)}
    monkeypatch.setattr(module, 'PACKAGE', package)
    monkeypatch.setattr(module, 'ROOT', root)
    monkeypatch.setattr(module, 'STORAGE', storage)
    monkeypatch.setattr(module, 'MONITOR', monitor)
    monkeypatch.setattr(module, 'MAPPING', mapping)
    monkeypatch.setattr(module, 'load_config', lambda *a, **k: (config, environment))
    monkeypatch.setattr(module, 'manifest_entries', lambda c: entries)
    monkeypatch.setattr(module, 'validate_runtime', lambda *a, **k: None)
    original_lstat = Path.lstat
    def root_owner(path):
        values = list(original_lstat(path))
        values[4] = 0
        values[0] &= ~0o022  # synthetic protected installation ancestors
        return os.stat_result(values)
    monkeypatch.setattr(Path, 'lstat', root_owner)
    planned = module.plan(tmp_path/'host.json', 'TRUSTED_IMPORT')
    assert set(planned) == {Path(p) for p in mapping.values()}
    assert all(isinstance(content, bytes) for content in planned.values())
    assert not (tmp_path/'targets').exists()
    first = next(iter(mapping))
    entries[mapping[first]] = '0'*64
    with pytest.raises(ValueError, match='NOT_APPROVED'):
        module.plan(tmp_path/'host.json', 'TRUSTED_IMPORT')
    entries[mapping[first]] = hashes[first]
    (package/first).write_bytes(b'unreviewed change')
    with pytest.raises(ValueError, match='PACKAGE_CHANGED'):
        module.plan(tmp_path/'host.json', 'TRUSTED_IMPORT')
    assert not (tmp_path/'targets').exists()
