#!/usr/bin/env python3
"""Opt-in root host oneshot. Fixed commands only; never imported by the backend.

Uses a local PHP transport to the existing task service and a private independent
evidence provider. The provider must be separately reviewed; no signer is invented.
"""
import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import time

from collect_churn_status import command, private_json, integrity, ObservationError

MAX_BYTES = 4*1024*1024
EVIDENCE_PROVIDER = Path('/usr/local/libexec/sales-churn-evidence')
REQUIRED_FILES = {'api/analytics_integration.php', 'api/manager_tasks_service.php',
    'api/analytics_permanent.php', 'api/analytics_permanent_cli.php',
    'api/analytics_verified_approval.php', 'api/analytics_signed_evidence.php',
    'api/analytics_budget_service.php', 'api/analytics_task_policy.php',
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


def verify_code(config):
    root = Path(config['calltrack_root'])
    manifest = Path(config['manifest'])
    reviewed = private_json(manifest)
    files = reviewed.get('files', reviewed)
    if not isinstance(files, dict):
        raise ValueError('INTEGRITY_MANIFEST_INVALID')
    covered = {str(Path(name).relative_to(root)) if Path(name).is_absolute() and Path(name).is_relative_to(root)
               else name for name in files}
    if not REQUIRED_FILES <= covered or not REQUIRED_HOST_FILES <= set(files):
        raise ValueError('REVIEWED_CODE_COVERAGE_INCOMPLETE')
    if integrity(manifest, root) != 'match':
        raise ValueError('REVIEWED_CODE_CHANGED')


def load_config(path):
    data = private_json(path)
    if set(data) != {'calltrack_root', 'manifest', 'php_environment'}:
        raise ValueError('INVALID_HOST_CONFIGURATION')
    for key in ('calltrack_root', 'manifest', 'php_environment'):
        if not isinstance(data[key], str) or not Path(data[key]).is_absolute():
            raise ValueError('INVALID_HOST_CONFIGURATION')
    environment = private_json(Path(data['php_environment']))
    info = os.stat(data['php_environment'], follow_symlinks=False)
    if info.st_mode & 0o077 or set(environment)-ENV_KEYS or any(not isinstance(v, str) for v in environment.values()):
        raise ValueError('PRIVATE_ENVIRONMENT_REQUIRED')
    if environment.get('CALLTRACK_ANALYTICS_MODE') != 'enabled':
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
    runner(['/usr/bin/curl', '--fail', '--silent', '--show-error', '--max-time', '3',
            'http://127.0.0.1:8010/api/health'], timeout=5)
    health = json.loads(runner(['/usr/bin/php', str(Path(config['calltrack_root'])/'api/analytics_permanent_cli.php'),
        'health'], input_text='{}', timeout=5, environment=environment))
    if health != {'receiver':'reachable'}:
        raise ValueError('RECEIVER_UNAVAILABLE')
    deadline = time.monotonic()+max_seconds
    state = backend('tick', runner=runner)
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
    except (OSError, ValueError, KeyError, TypeError, ObservationError):
        try:
            backend('failure', job_id=job['id'], runner=runner)
        except (OSError, ValueError, KeyError, TypeError, ObservationError):
            pass
        raise


def process_job(job, config, environment, *, runner, deadline, verify):
    if job['status'] in {'AWAITING_EVIDENCE', 'BLOCKED'}:
        if not EVIDENCE_PROVIDER.is_file():
            raise ValueError('EVIDENCE_PROVIDER_NOT_CONFIGURED')
        # The trusted provider fetches independent source/identity evidence. It receives
        # only run context, not raw sales or the upstream signing key from this process.
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
        if (preview.get('job_id') != page['job_id'] or preview.get('release_id') != page['release_id']
                or len(preview.get('receipts', [])) != len(page['events'])):
            raise ValueError('PREFLIGHT_RESPONSE_INVALID')
        # Receiver rechecks every gate under transaction even after successful preview.
        # A BLOCKED preview is delivered too: only receiver decisions are authoritative,
        # and valid existing tasks must still be acknowledged. No gate is bypassed.
        receipt = json.loads(runner(argv+['deliver'], input_text=json.dumps(page), timeout=30,
            environment=environment, max_output=MAX_BYTES))
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
    except (OSError, ValueError, KeyError, TypeError, ObservationError):
        # Failures never echo token-bearing command output or customer evidence.
        print('{"status":"BLOCKED","code":"HOST_AUTOMATION_FAILED"}')
        raise SystemExit(1)
    finally:
        os.close(fd)


if __name__ == '__main__':
    main()
