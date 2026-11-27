#!/usr/bin/env python3
"""Opt-in root host oneshot. Fixed commands only; never imported by the backend.

Uses a local PHP transport to the existing task service and a private independent
evidence provider. The provider must be separately reviewed; no signer is invented.
"""
import argparse
import base64
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import time

from collect_churn_status import command, private_json, integrity, ObservationError

MAX_BYTES = 4*1024*1024
EVIDENCE_PROVIDER = Path('/usr/local/libexec/sales-churn-evidence')
REQUIRED_FILES = {'api/analytics_integration.php', 'api/manager_tasks_service.php',
    'api/analytics_permanent.php', 'api/analytics_permanent_cli.php',
    'api/analytics_verified_approval.php', 'api/analytics_signed_evidence.php',
    'api/analytics_manager_pilot_budget.php', 'api/client_manager.php',
    'api/email_sync.php', 'api/personal_contacts_filter.php', 'api/analytics_budget_service.php', 'api/analytics_task_policy.php',
    'api/analytics_verified_bindings.php', 'api/analytics_preliminary.php',
    'api/analytics_source_trust.php', 'api/manager_bindings_service.php',
    'api/client_directory.php', 'api/client_catalog_cache.php', 'api/client_catalog_json_stream.php',
    'api/config.php', 'api/web_auth.php', 'api/analytics_sync.php', 'api/sales_journal.php',
    'api/analytics_preview_cursor.php'}
REQUIRED_HOST_FILES = {'/usr/local/libexec/run_churn_automation.py',
    '/usr/local/libexec/collect_churn_status.py', str(EVIDENCE_PROVIDER),
    '/etc/systemd/system/sales-churn-permanent.service', '/etc/systemd/system/sales-churn-permanent.timer'}
ENV_KEYS = {'CALLTRACK_ANALYTICS_MODE', 'CALLTRACK_ANALYTICS_POLICY_FILE',
    'CALLTRACK_UPSTREAM_PUBLIC_KEYS', 'CALLTRACK_BINDINGS_PUBLIC_KEYS',
    'CALLTRACK_STORAGE_DIR',
    'CALLTRACK_DB_HOST', 'CALLTRACK_DB_PORT', 'CALLTRACK_DB_NAME', 'CALLTRACK_DB_USER', 'CALLTRACK_DB_PASS'}


def required_paths(config, environment=None):
    """Code closure at Calltrack PR #518/00a8c9c; optional PHP is executable too."""
    root = Path(config['calltrack_root'])
    paths = {str(root / name) for name in REQUIRED_FILES} | REQUIRED_HOST_FILES
    if 'trusted_import' in config:
        paths = (paths - {str(EVIDENCE_PROVIDER)}) | {'/usr/local/libexec/churn_trusted_ftp.py'}
    paths.add(config['php_environment'])
    environment = environment if environment is not None else private_json(Path(config['php_environment']))
    policy = environment.get('CALLTRACK_ANALYTICS_POLICY_FILE')
    if not isinstance(policy, str) or not Path(policy).is_absolute():
        raise ValueError('RECEIVER_POLICY_REQUIRED')
    paths.add(policy)
    for path in (Path('/etc/calltrack/config.local.php'), root/'api/config.local.php', root/'config.local.php'):
        if path.exists() or path.is_symlink():
            paths.add(str(path))
    if 'trusted_import' in config:
        paths.add(config['trusted_import'])
    return paths


def manifest_entries(config):
    reviewed = private_json(Path(config['manifest']))
    files = reviewed['files'] if isinstance(reviewed, dict) and set(reviewed) == {'files'} else reviewed
    if not isinstance(files, dict) or not 1 <= len(files) <= 100:
        raise ValueError('INTEGRITY_MANIFEST_INVALID')
    root = Path(config['calltrack_root'])
    normalized = {}
    for name, digest in files.items():
        path = Path(name)
        if '..' in path.parts or not isinstance(digest, str) or not re.fullmatch('[a-f0-9]{64}', digest):
            raise ValueError('INTEGRITY_MANIFEST_INVALID')
        path = path if path.is_absolute() else root/path
        if str(path) in normalized:
            raise ValueError('INTEGRITY_MANIFEST_AMBIGUOUS')
        normalized[str(path)] = digest
    if not required_paths(config) <= normalized.keys():
        raise ValueError('REVIEWED_CODE_COVERAGE_INCOMPLETE')
    return normalized


def verify_code(config):
    manifest_entries(config)
    if integrity(Path(config['manifest']), Path(config['calltrack_root'])) != 'match':
        raise ValueError('REVIEWED_CODE_CHANGED')


def validate_runtime(config, environment, *, require_enabled=True, evidence_provider=EVIDENCE_PROVIDER):
    """Local checks before Docker/PHP/evidence. No signing, networking or DB writes."""
    if require_enabled and environment.get('CALLTRACK_ANALYTICS_MODE') != 'enabled':
        raise ValueError('RECEIVER_DISABLED')
    policy = private_json(Path(environment['CALLTRACK_ANALYTICS_POLICY_FILE']))
    expected_mode = 'TRUSTED_IMPORT' if 'trusted_import' in config else 'INDEPENDENT'
    if policy.get('source_trust_mode', 'INDEPENDENT') != expected_mode:
        raise ValueError('SOURCE_TRUST_MODE_MISMATCH')
    for name in ('CALLTRACK_UPSTREAM_PUBLIC_KEYS', 'CALLTRACK_BINDINGS_PUBLIC_KEYS'):
        keys = json.loads(environment.get(name, '{}'))
        if not isinstance(keys, dict) or not keys:
            raise ValueError('RECEIVER_PUBLIC_KEYS_REQUIRED')
        for key_id, value in keys.items():
            if (not re.fullmatch('[A-Za-z0-9_.-]{1,80}', key_id) or not isinstance(value, str)
                    or len(base64.b64decode(value, validate=True)) != 32):
                raise ValueError('INVALID_RECEIVER_PUBLIC_KEY')
    if 'trusted_import' in config:
        from churn_trusted_ftp import read_private
        trusted = json.loads(read_private(config['trusted_import']))
        if set(trusted) != {'export_url', 'export_token_file', 'ed25519_key_file', 'key_id', 'hmac_key_file'}:
            raise ValueError('INVALID_TRUSTED_HOST_CONFIG')
        for name in ('export_token_file', 'ed25519_key_file', 'hmac_key_file'):
            data = read_private(trusted[name])
            if len(data.strip()) < 32:
                raise ValueError('INVALID_PRIVATE_KEY_OR_TOKEN')
    elif not evidence_provider.is_file() or not os.access(evidence_provider, os.X_OK):
        raise ValueError('EVIDENCE_PROVIDER_NOT_CONFIGURED')


def validate_receipts(page, result, *, preview=False):
    """Never forward an unrelated/partial/malformed local response as an ACK."""
    if (not isinstance(result, dict) or set(result) != {'job_id', 'release_id', 'receipts'}
            or result['job_id'] != page['job_id'] or result['release_id'] != page['release_id']
            or not isinstance(result['receipts'], list) or len(result['receipts']) != len(page['events'])):
        raise ValueError('RECEIVER_RESPONSE_INVALID')
    events = {e['event_id']: e['event_hash'] for e in page['events']}
    seen = set()
    decisions = {'TASK_CREATED', 'TASK_EXISTS', 'BLOCKED', 'REVIEW'}
    if preview:
        decisions = {'DRY_RUN_READY', 'TASK_EXISTS', 'BLOCKED', 'REVIEW', 'OLD_REVISION'}
    for receipt in result['receipts']:
        if not isinstance(receipt, dict):
            raise ValueError('RECEIVER_RESPONSE_INVALID')
        key = receipt.get('event_id')
        if (type(key) is not int or key in seen or key not in events
                or receipt.get('event_hash') != events[key] or receipt.get('decision') not in decisions
                or type(receipt.get('created_for_release')) is not bool
                or not isinstance(receipt.get('reasons'), list)
                or len(receipt['reasons']) > 30
                or any(not isinstance(r, str) or not re.fullmatch('[A-Z0-9_]{1,64}', r) for r in receipt['reasons'])):
            raise ValueError('RECEIVER_RESPONSE_INVALID')
        task = receipt.get('task_id')
        if ((task is not None and (not isinstance(task, str) or not re.fullmatch('[A-Za-z0-9-]{1,64}', task)))
                or (receipt['decision'] in {'TASK_CREATED', 'TASK_EXISTS'} and not task)):
            raise ValueError('RECEIVER_RESPONSE_INVALID')
        seen.add(key)


def load_config(path, *, require_enabled=True):
    data = private_json(path)
    if not isinstance(data, dict) or set(data) - {'calltrack_root', 'manifest', 'php_environment', 'trusted_import'} or not {'calltrack_root', 'manifest', 'php_environment'} <= set(data):
        raise ValueError('INVALID_HOST_CONFIGURATION')
    for key in data:
        if not isinstance(data[key], str) or not Path(data[key]).is_absolute():
            raise ValueError('INVALID_HOST_CONFIGURATION')
    environment = private_json(Path(data['php_environment']))
    info = os.stat(data['php_environment'], follow_symlinks=False)
    if not isinstance(environment, dict) or info.st_mode & 0o077 or set(environment)-ENV_KEYS or any(not isinstance(v, str) for v in environment.values()):
        raise ValueError('PRIVATE_ENVIRONMENT_REQUIRED')
    if require_enabled and environment.get('CALLTRACK_ANALYTICS_MODE') != 'enabled':
        raise ValueError('RECEIVER_DISABLED')
    return data, environment


def backend(action, *, job_id=None, payload=None, runner=command):
    argv = ['/usr/bin/docker', 'exec', '-i', 'sales-journal-backend',
            'python', '-B', '-m', 'app.churn_automation', action]
    if job_id:
        if not re.fullmatch('[a-zA-Z0-9-]{1,36}', job_id):
            raise ValueError('INVALID_JOB_ID')
        argv += ['--job-id', job_id]
    return json.loads(runner(argv, input_text=json.dumps(payload or {}), timeout=90, max_output=MAX_BYTES))


def run(config, environment, *, runner=command, max_seconds=180, verify=verify_code):
    verify(config)
    for unit in ('calltrack-churn-pilot.timer', 'calltrack-churn-pilot.service'):
        active = runner(['/usr/bin/systemctl', 'show', unit, '--property=ActiveState', '--value'], timeout=5).strip()
        if active not in {'inactive', 'failed'}:
            raise ValueError('OLD_PILOT_STILL_ACTIVE')
    validate_runtime(config, environment)
    runner(['/usr/bin/curl', '--fail', '--silent', '--show-error', '--max-time', '3',
            'http://127.0.0.1:8010/api/health'], timeout=5)
    health = json.loads(runner(['/usr/bin/php', str(Path(config['calltrack_root'])/'api/analytics_permanent_cli.php'),
        'health'], input_text='{}', timeout=5, environment=environment))
    if health != {'receiver':'reachable'}:
        raise ValueError('RECEIVER_UNAVAILABLE')
    deadline = time.monotonic()+max_seconds
    state = backend('tick', runner=runner)
    policy = state.get('policy', {})
    if policy.get('enabled') is not True or policy.get('approved') is not True:
        return {'status': 'DISABLED', 'tasks_created': None}
    from datetime import datetime
    from zoneinfo import ZoneInfo
    today = datetime.now(ZoneInfo('Europe/Moscow')).date().isoformat()
    candidates = [j for j in reversed(state['history']) if j['kind']=='daily' and j['business_date']==today
                  and j['status'] in {'AWAITING_EVIDENCE', 'BLOCKED', 'RELEASED', 'DELIVERING'}]
    if not candidates:
        return {'status': 'IDLE', 'tasks_created': None}
    job = candidates[0]
    if job['counters'].get('retry_after'):
        retry = datetime.fromisoformat(job['counters']['retry_after'])
        if datetime.now(retry.tzinfo) < retry:
            return {'status': 'RETRY_DELAY', 'job_id': job['id']}
    try:
        return process_job(job, config, environment, runner=runner, deadline=deadline, verify=verify)
    except (OSError, ValueError, KeyError, TypeError, ObservationError, subprocess.SubprocessError):
        try:
            backend('failure', job_id=job['id'], runner=runner)
        except (OSError, ValueError, KeyError, TypeError, ObservationError, subprocess.SubprocessError):
            pass
        raise


def process_job(job, config, environment, *, runner, deadline, verify):
    if job['status'] in {'AWAITING_EVIDENCE', 'BLOCKED'}:
        if 'trusted_import' in config:
            from churn_trusted_ftp import build
            context = backend('trusted-context', job_id=job['id'], runner=runner)
            if context.get('job_id') != job['id'] or context.get('statement', {}).get('source_run_id') != job['run_id']:
                raise ValueError('TRUSTED_CONTEXT_MISMATCH')
            evidence = build(context, config['trusted_import'])
        else:
            if not EVIDENCE_PROVIDER.is_file():
                raise ValueError('EVIDENCE_PROVIDER_NOT_CONFIGURED')
            evidence = json.loads(runner([str(EVIDENCE_PROVIDER)],
                input_text=json.dumps({'job_id':job['id'], 'run_id':job['run_id']}), timeout=30, max_output=MAX_BYTES))
        result = backend('authorize', job_id=job['id'], payload=evidence, runner=runner)
        if result['status'] != 'RELEASED':
            backend('failure', job_id=job['id'], runner=runner)
            return result
    root = Path(config['calltrack_root'])
    pages = 0
    while time.monotonic() < deadline and pages < 4:
        verify(config)  # detect code changes before each bounded page
        page = backend('page', job_id=job['id'], runner=runner)
        if not page['events']:
            return {'status': 'NO_PENDING_EVENTS', 'job_id': job['id']}
        argv = ['/usr/bin/php', str(root/'api/analytics_permanent_cli.php')]
        preview = json.loads(runner(argv+['preflight'], input_text=json.dumps(page), timeout=30,
            environment=environment, max_output=MAX_BYTES))
        validate_receipts(page, preview, preview=True)
        # Receiver rechecks every gate under transaction even after successful preview.
        # A BLOCKED preview is delivered too: only receiver decisions are authoritative,
        # and valid existing tasks must still be acknowledged. No gate is bypassed.
        receipt = json.loads(runner(argv+['deliver'], input_text=json.dumps(page), timeout=30,
            environment=environment, max_output=MAX_BYTES))
        validate_receipts(page, receipt)
        result = backend('ack', job_id=job['id'], payload=receipt, runner=runner)
        pages += 1
        if result['status'] in {'SUCCEEDED', 'PARTIAL'}:
            return result
    return {'status': 'RESUMABLE', 'job_id': job['id'], 'pages': pages}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, default=Path('/etc/sales/churn-permanent/host.json'))
    args = parser.parse_args()
    if os.geteuid() != 0:
        parser.exit(1, 'A separately authorized host execution context is required\n')
    # systemd creates the private RuntimeDirectory. Do not touch arbitrary lock paths.
    lock_path = '/run/sales-churn-permanent/worker.lock'
    fd = os.open(lock_path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o077:
            raise ValueError('UNTRUSTED_LOCK')
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        config, environment = load_config(args.config)
        print(json.dumps(run(config, environment)))
    except (OSError, ValueError, KeyError, TypeError, ObservationError, subprocess.SubprocessError):
        # Failures never echo token-bearing command output or customer evidence.
        print('{"status":"BLOCKED","code":"HOST_AUTOMATION_FAILED"}')
        raise SystemExit(1)
    finally:
        os.close(fd)


if __name__ == '__main__':
    main()
