#!/usr/bin/env python3
"""Read-only host observer for the EXISTING pilot. Never starts/enables services."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import selectors
import stat
import subprocess
import tempfile
import time
from zoneinfo import ZoneInfo

MAX_OUTPUT = 131072
SERVICE = 'calltrack-churn-pilot.service'
TIMER = 'calltrack-churn-pilot.timer'


class ObservationError(Exception):
    def __init__(self, code):
        self.code = code


def command(args, *, input_text=None, timeout=5):
    """Fixed caller-owned argv, bounded pipes and deadline; never echo stderr."""
    with tempfile.TemporaryFile() as source:
        source.write((input_text or '').encode()); source.seek(0)
        process = subprocess.Popen(args, stdin=source, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, env={'TZ': 'UTC', 'LC_ALL': 'C'})
        selector = selectors.DefaultSelector()
        data = {process.stdout: bytearray(), process.stderr: bytearray()}
        try:
            for stream in data:
                os.set_blocking(stream.fileno(), False)
                selector.register(stream, selectors.EVENT_READ)
            deadline = time.monotonic() + timeout
            while selector.get_map():
                left = deadline - time.monotonic()
                if left <= 0:
                    raise ObservationError('RUNTIME_ERROR')
                for event, _ in selector.select(min(left, 0.2)):
                    chunk = os.read(event.fileobj.fileno(), 8192)
                    if not chunk:
                        selector.unregister(event.fileobj)
                        continue
                    data[event.fileobj].extend(chunk)
                    if sum(map(len, data.values())) > MAX_OUTPUT:
                        raise ObservationError('RUNTIME_ERROR')
            process.wait(timeout=max(0.01, deadline-time.monotonic()))
            if process.returncode:
                stderr = bytes(data[process.stderr]).lower()
                raise ObservationError('AUTHORIZATION_FAILED' if b'access denied' in stderr or b'permission denied' in stderr else 'RUNTIME_ERROR')
            return bytes(data[process.stdout]).decode('utf-8')
        except (OSError, subprocess.TimeoutExpired, UnicodeError) as exc:
            raise ObservationError('RUNTIME_ERROR') from exc
        finally:
            if process.poll() is None:
                process.kill()
            process.wait()
            selector.close()
            process.stdout.close(); process.stderr.close()


def stamp(value):
    match = re.fullmatch(r'[A-Za-z]{3} (\d{4}-\d{2}-\d{2}) (\d{2}:\d{2}:\d{2}) UTC', value or '')
    if not match:
        return None
    try:
        return datetime.fromisoformat(match[1]+'T'+match[2]+'+00:00').isoformat()
    except ValueError:
        return None


def properties(unit, names, runner):
    output = runner(['systemctl', 'show', unit, '--no-pager', '--property='+','.join(names)])
    return dict(line.split('=', 1) for line in output.splitlines() if '=' in line)


def log_codes(message):
    # Only codes leave this function: never forward raw messages/customer data.
    lower = message.lower()
    codes = []
    for pattern, code in [('reviewed code changed', 'REVIEWED_CODE_CHANGED'),
                          ('total lifetime budget exhausted', 'LIFETIME_BUDGET_EXHAUSTED'),
                          ('daily_budget_exhausted', 'DAILY_BUDGET_EXHAUSTED'),
                          ('pilot_stop', 'PILOT_STOP'), ('unauthorized', 'AUTHORIZATION_FAILED'),
                          ('connection refused', 'CALLTRACK_UNAVAILABLE'),
                          ('timed out', 'CALLTRACK_UNAVAILABLE')]:
        if pattern in lower and code not in codes:
            codes.append(code)
    if re.search(r'\b(?:http(?:error)?|status)[ :=]*(?:401|403)\b|\baccess denied\b', lower):
        codes.append('AUTHORIZATION_FAILED')
    for code in ('ALGORITHM_POLICY_NOT_APPROVED', 'POLICY_NOT_APPROVED', 'IDENTITY_NOT_VERIFIED',
                 'SOURCE_CHANGED_OR_PENDING', 'RUN_NOT_SUCCEEDED', 'RUN_NOT_CURRENT_PUBLICATION',
                 'UPSTREAM_SIGNATURE_INVALID'):
        if code in message:
            codes.append(code)
    return list(dict.fromkeys(codes))


def host_observation(runner=command):
    systemd = dict(timer_enabled=None, timer_active=None, service_state='unknown', service_result='unknown',
                   last_attempt=None, last_success=None, next_run=None, schedule=None)
    codes, history = [], []
    try:
        timer = properties(TIMER, ['LoadState', 'ActiveState', 'UnitFileState', 'NextElapseUSecRealtime', 'TimersCalendar'], runner)
        service = properties(SERVICE, ['LoadState', 'ActiveState', 'Result', 'ExecMainStartTimestamp', 'ExecMainExitTimestamp', 'InvocationID'], runner)
        if timer.get('LoadState') != 'loaded' or service.get('LoadState') != 'loaded':
            raise ObservationError('SCHEDULE_UNCONFIRMED')
        systemd.update(timer_enabled=timer.get('UnitFileState') in {'enabled', 'enabled-runtime'},
            timer_active=timer.get('ActiveState') == 'active',
            service_state=service.get('ActiveState') if service.get('ActiveState') in {'active', 'inactive', 'failed', 'activating', 'deactivating'} else 'unknown',
            service_result='success' if service.get('Result') == 'success' else 'failed' if service.get('Result') else 'unknown',
            last_attempt=stamp(service.get('ExecMainStartTimestamp')),
            next_run=stamp(timer.get('NextElapseUSecRealtime')),
            schedule='09:00 Europe/Moscow' if re.search(r'OnCalendar=\*-\*-\* 09:00(?::00)? Europe/Moscow(?:\s|;|})', timer.get('TimersCalendar', '')) and timer.get('TimersCalendar', '').count('OnCalendar=') == 1 else None)
        output = runner(['journalctl', '--unit='+SERVICE, '--no-pager', '--output=json',
                         '--output-fields=MESSAGE,__REALTIME_TIMESTAMP,_SYSTEMD_INVOCATION_ID', '-n', '100'])
        groups = {}
        for line in output.splitlines():
            row = json.loads(line)
            identity = row.get('_SYSTEMD_INVOCATION_ID')
            if not isinstance(identity, str) or not re.fullmatch(r'[a-fA-F0-9]{32}', identity):
                continue
            moment = datetime.fromtimestamp(int(row['__REALTIME_TIMESTAMP'])/1000000, timezone.utc).isoformat()
            item = groups.setdefault(identity, dict(id=identity, observed_at=moment, result='unknown', codes=[]))
            item['observed_at'] = min(item['observed_at'], moment)
            item['codes'] = list(dict.fromkeys(item['codes'] + log_codes(str(row.get('MESSAGE', '')))))[:20]
            if item['codes']:
                item['result'] = 'blocked' if 'LIFETIME_BUDGET_EXHAUSTED' in item['codes'] or 'DAILY_BUDGET_EXHAUSTED' in item['codes'] else 'failed'
        invocation = service.get('InvocationID')
        if not invocation and systemd['last_attempt']:
            recent = sorted(groups.values(), key=lambda r: r['observed_at'], reverse=True)
            if recent and recent[0]['observed_at'] >= systemd['last_attempt']:
                invocation = recent[0]['id']
        latest_codes = groups.get(invocation, {}).get('codes', [])
        codes += latest_codes
        if invocation and re.fullmatch(r'[a-fA-F0-9]{32}', invocation) and systemd['last_attempt']:
            current = groups.setdefault(invocation, dict(id=invocation, observed_at=systemd['last_attempt'], result='unknown', codes=[]))
            current['observed_at'] = systemd['last_attempt']
            if not current['codes']:
                current['result'] = 'success' if systemd['service_result'] == 'success' and systemd['service_state'] == 'inactive' else 'failed' if systemd['service_result'] == 'failed' else 'unknown'
            if current['result'] == 'success':
                systemd['last_success'] = stamp(service.get('ExecMainExitTimestamp'))
        history = sorted(groups.values(), key=lambda r: r['observed_at'], reverse=True)[:20]
    except (OSError, ValueError, KeyError, TypeError, OverflowError, ObservationError):
        codes.append('RUNTIME_OBSERVATION_FAILED')
    return systemd, list(dict.fromkeys(codes)), history


def private_json(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022 or info.st_size > MAX_OUTPUT:
            raise ValueError('Untrusted file')
        with os.fdopen(fd, 'rb', closefd=False) as source:
            data = source.read(MAX_OUTPUT + 1)
        if len(data) > MAX_OUTPUT:
            raise ValueError('Input too large')
        return json.loads(data)
    finally:
        os.close(fd)


def integrity(manifest, root):
    """Only supported explicit file->SHA256 maps; unknown schemas stay unknown."""
    try:
        entries = private_json(manifest)
        if isinstance(entries, dict) and set(entries) == {'files'}:
            entries = entries['files']
        if not isinstance(entries, dict) or not 1 <= len(entries) <= 100:
            return 'unknown'
        total = 0
        for filename, expected in entries.items():
            if not isinstance(expected, str) or not re.fullmatch('[a-f0-9]{64}', expected):
                return 'unknown'
            path = Path(filename)
            if not path.is_absolute():
                if '..' in path.parts or path.parts[0] not in {'api', 'scripts', 'analizmop'}:
                    return 'unknown'
                path = root / path
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            try:
                info = os.fstat(fd)
                if not stat.S_ISREG(info.st_mode) or info.st_size > 2*1024*1024:
                    return 'unknown'
                total += info.st_size
                if total > 16*1024*1024:
                    return 'unknown'
                hashed = hashlib.sha256()
                read_bytes = 0
                with os.fdopen(fd, 'rb', closefd=False) as source:
                    for chunk in iter(lambda: source.read(65536), b''):
                        read_bytes += len(chunk)
                        if read_bytes > 2*1024*1024:
                            return 'unknown'
                        hashed.update(chunk)
                if hashed.hexdigest() != expected:
                    return 'mismatch'
            finally:
                os.close(fd)
        return 'match'
    except (OSError, ValueError, TypeError, IndexError):
        return 'unknown'


def receiver_sql(day):
    # Fixed, aggregate-only queries. No sales scans, row locks, DDL or DML.
    return f"""SET TRANSACTION READ ONLY;
START TRANSACTION;
SELECT JSON_OBJECT('kind','policy','approved',p.approved,'min_confidence',p.min_confidence,'churn_enabled',t.enabled,'priority',t.priority,'due_days',t.due_days) FROM analytics_task_policy p LEFT JOIN analytics_task_type_policy t ON t.signal_type='CHURN_RISK' WHERE p.id=1;
SELECT JSON_OBJECT('kind','lifetime','approved',approved,'limit',max_tasks,'used',used_tasks) FROM analytics_task_budget WHERE id=1;
SELECT JSON_OBJECT('kind','daily','approved',(p.policy_version='aviator-churn-pilot/1' AND p.timezone='Europe/Moscow' AND p.max_tasks=5),'policy_version',p.policy_version,'business_date','{day}','limit',p.max_tasks,'used',COALESCE(b.used_tasks,0),'ledger_used',(SELECT COUNT(*) FROM analytics_daily_budget_reservations WHERE policy_id=1 AND business_date='{day}')) FROM analytics_daily_budget_policy p LEFT JOIN analytics_daily_budget b ON b.policy_id=p.id AND b.business_date='{day}' WHERE p.id=1;
SELECT JSON_OBJECT('kind','summary','accepted_signals',COALESCE(SUM(decision IN ('TASK_CREATED','TASK_EXISTS')),0),'blocked_signals',COALESCE(SUM(decision IN ('BLOCKED','REVIEW')),0),'tasks_created',(SELECT COUNT(*) FROM manager_tasks WHERE source_signal_key IS NOT NULL AND family='INACTIVITY')) FROM analytics_inbox;
SELECT JSON_OBJECT('kind','manager','manager_id',m.id,'limit',5,'approved',1,'used',COUNT(DISTINCT e.task_id)) FROM (SELECT 3 AS id UNION ALL SELECT 6 AS id) m LEFT JOIN manager_task_events e ON e.event_type='CREATED' AND e.actor_id IS NULL AND JSON_UNQUOTE(JSON_EXTRACT(e.data_json,'$.source'))='ANALYTICS' AND CAST(JSON_UNQUOTE(JSON_EXTRACT(e.data_json,'$.assignee_id')) AS UNSIGNED)=m.id GROUP BY m.id;
SELECT JSON_OBJECT('kind','receipt','release_id',JSON_UNQUOTE(JSON_EXTRACT(payload_json,'$.run.id')),'accepted',SUM(decision IN ('TASK_CREATED','TASK_EXISTS')),'blocked',SUM(decision IN ('BLOCKED','REVIEW')),'tasks',COUNT(DISTINCT CASE WHEN decision IN ('TASK_CREATED','TASK_EXISTS') THEN task_id END)) FROM analytics_inbox GROUP BY JSON_UNQUOTE(JSON_EXTRACT(payload_json,'$.run.id')) ORDER BY MAX(processed_at) DESC LIMIT 30;
ROLLBACK;
"""


def receiver_observation(defaults, policy_path, *, now, runner=command):
    if defaults is None:
        return {'status': 'unknown'}
    try:
        info = defaults.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o077:
            raise ObservationError('AUTHORIZATION_FAILED')
        output = runner(['mariadb', '--defaults-file='+str(defaults), '--batch', '--raw',
                         '--skip-column-names', '--connect-timeout=3'],
                        input_text=receiver_sql(now.astimezone(ZoneInfo('Europe/Moscow')).date().isoformat()))
        result = dict(status='available', receipts=[], manager_budgets=[])
        for line in output.splitlines():
            row = json.loads(line)
            kind = row.pop('kind')
            if kind == 'policy':
                result.update(policy_approved=bool(row['approved']), min_confidence=float(row['min_confidence']) if row['min_confidence'] is not None else None,
                              churn_enabled=bool(row.get('churn_enabled')) if row.get('churn_enabled') is not None else None,
                              priority=row.get('priority'), due_days=row.get('due_days'))
            elif kind in {'lifetime', 'daily'}:
                row['approved'] = bool(row['approved'])
                result[kind] = row
            elif kind == 'manager':
                row['approved'] = bool(row['approved'])
                result['manager_budgets'].append(row)
            elif kind == 'summary':
                result.update(row)
            elif kind == 'receipt' and isinstance(row.get('release_id'), str) and re.fullmatch(r'[a-zA-Z0-9-]{1,36}', row['release_id']):
                result['receipts'].append(row)
        if 'accepted_signals' not in result or 'tasks_created' not in result:
            raise ValueError('Missing receiver aggregate response')
        if policy_path:
            try:
                policy = private_json(policy_path)
                generators = policy.get('approved_generators')
                if isinstance(generators, list) and len(generators) <= 10 and all(isinstance(g, str) and re.fullmatch(r'inactivity/[a-z0-9/-]{1,64}', g) for g in generators):
                    result['approved_generators'] = generators
            except (OSError, ValueError, TypeError, AttributeError):
                pass  # DB facts remain valid; missing policy evidence is a separate blocker.
        return result
    except ObservationError as exc:
        return {'status': 'unauthorized' if exc.code == 'AUTHORIZATION_FAILED' else 'unavailable'}
    except (OSError, ValueError, KeyError, TypeError):
        return {'status': 'unavailable'}


def write_snapshot(path, snapshot):
    encoded = (json.dumps(snapshot, ensure_ascii=False)+'\n').encode()
    if len(encoded) > MAX_OUTPUT:
        raise ObservationError('RUNTIME_ERROR')
    # Parent is explicitly provisioned by the operator; never create broad paths.
    directory = path.parent.lstat()
    if not stat.S_ISDIR(directory.st_mode) or directory.st_uid != 0 or directory.st_mode & 0o022:
        raise ObservationError('RUNTIME_ERROR')
    fd, temporary = tempfile.mkstemp(prefix='.status-', dir=path.parent)
    try:
        os.fchmod(fd, 0o640)
        if os.geteuid() == 0:
            os.fchown(fd, 0, directory.st_gid)
        with os.fdopen(fd, 'wb') as target:
            target.write(encoded); target.flush(); os.fsync(target.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--calltrack-root', type=Path, required=True)
    parser.add_argument('--receiver-defaults', type=Path, help='Root-only MariaDB SELECT-only credentials file; never printed')
    parser.add_argument('--receiver-policy', type=Path, help='Existing protected consumer policy; allowlisted keys only')
    args = parser.parse_args()
    if args.output != Path('/run/sales-churn-monitor/status.json'):
        parser.exit(1, 'Only the dedicated monitoring output path is allowed\n')
    if os.geteuid() != 0:
        parser.exit(1, 'Status observer requires an operator-provisioned root execution context\n')
    now = datetime.now(timezone.utc)
    host, codes, history = host_observation()
    snapshot = dict(schema_version=1, collected_at=now.isoformat(), systemd=host,
        integrity=integrity(args.manifest, args.calltrack_root), codes=codes, history=history,
        receiver=receiver_observation(args.receiver_defaults, args.receiver_policy, now=now))
    try:
        write_snapshot(args.output, snapshot)
    except (OSError, ValueError, ObservationError):
        parser.exit(1, 'Status observation could not be saved; no automation was executed\n')
    print('Read-only status observation saved; no automation was executed')


if __name__ == '__main__':
    main()
