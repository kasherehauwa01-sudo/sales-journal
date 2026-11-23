#!/usr/bin/env python3
"""Check by default. Explicit --apply copies host code only; never starts/enables anything."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tempfile

PACKAGE = Path(__file__).resolve().parents[2]
sys.dont_write_bytecode = True
sys.path.insert(0, str(PACKAGE/'backend/scripts'))
from run_churn_automation import load_config, manifest_entries, validate_runtime
from collect_churn_status import private_json

ROOT = Path('/var/www/html/vr/calltrack-v3-20261026')
STORAGE = ROOT/'storage'
UNITS = ('sales-churn-permanent.service', 'sales-churn-permanent.timer')
MAPPING = {
 'backend/scripts/run_churn_automation.py': '/usr/local/libexec/run_churn_automation.py',
 'backend/scripts/collect_churn_status.py': '/usr/local/libexec/collect_churn_status.py',
 'backend/scripts/churn_trusted_ftp.py': '/usr/local/libexec/churn_trusted_ftp.py',
 **{'deploy/churn-permanent/systemd/'+name: '/etc/systemd/system/'+name for name in UNITS},
}


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def secure(path, *, private=False):
    info = path.lstat()
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != 0
            or info.st_mode & (0o077 if private else 0o022) or info.st_size > 2*1024*1024):
        raise ValueError('UNTRUSTED_FILE')
    return info


def unit_stopped(name):
    result = subprocess.run(['/usr/bin/systemctl', 'show', name,
        '--property=LoadState,ActiveState,UnitFileState'], capture_output=True, text=True, timeout=5)
    values = dict(line.split('=', 1) for line in result.stdout.splitlines() if '=' in line)
    if result.returncode not in (0, 4):
        raise ValueError('UNIT_STATE_UNKNOWN')
    if (values.get('ActiveState') not in ('inactive', 'failed')
            or values.get('UnitFileState', '') not in ('', 'disabled', 'static', 'not-found')):
        raise ValueError('CONSUMER_MUST_BE_STOPPED_AND_DISABLED')


def check_host():
    release = dict(line.split('=', 1) for line in Path('/etc/os-release').read_text().splitlines() if '=' in line)
    if release.get('ID', '').strip('"') != 'ubuntu' or release.get('VERSION_ID', '').strip('"') != '24.04':
        raise ValueError('REVIEWED_UBUNTU_24_04_REQUIRED')
    for name in (*UNITS, 'calltrack-churn-pilot.service', 'calltrack-churn-pilot.timer'):
        unit_stopped(name)
    for directory in ('/etc/systemd/system', '/run/systemd/system', '/usr/lib/systemd/system'):
        for name in (*UNITS, 'service', 'timer'):
            if any((Path(directory)/(name+'.d')).glob('*.conf')):
                raise ValueError('UNIT_DROPINS_REQUIRE_SEPARATE_REVIEW')
    running = subprocess.run(['/usr/bin/docker', 'inspect', '--format={{.State.Running}}',
        'sales-journal-backend'], capture_output=True, text=True, check=True, timeout=10).stdout.strip()
    if running != 'true':
        raise ValueError('BACKEND_CONTAINER_NOT_RUNNING')
    # Does not load PHP application code, connect to MariaDB or run migrations.
    subprocess.run(['/usr/bin/php', '-r',
        'if(PHP_VERSION!=="8.5.12"||!extension_loaded("pdo_mysql")||!extension_loaded("sodium"))exit(1);'],
        check=True, timeout=5, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def plan(config_path, source_mode, provider=None):
    package_hashes = json.loads((PACKAGE/'deploy/churn-permanent/host-package.sha256.json').read_text())
    expected = set(MAPPING) | {'deploy/churn-permanent/install_host.py'}
    if set(package_hashes) != expected:
        raise ValueError('PACKAGE_COVERAGE_INCOMPLETE')
    for relative, expected_hash in package_hashes.items():
        if digest(PACKAGE/relative) != expected_hash:
            raise ValueError('PACKAGE_CHANGED')
    config, environment = load_config(config_path, require_enabled=False)
    if Path(config['calltrack_root']) != ROOT or environment.get('CALLTRACK_ANALYTICS_MODE') != 'disabled':
        raise ValueError('EXACT_ROOT_AND_DISABLED_RECEIVER_REQUIRED')
    if environment.get('CALLTRACK_STORAGE_DIR', str(STORAGE)) != str(STORAGE):
        raise ValueError('STORAGE_SANDBOX_REVIEW_REQUIRED')
    if ('trusted_import' in config) != (source_mode == 'TRUSTED_IMPORT'):
        raise ValueError('SOURCE_MODE_MISMATCH')
    if source_mode == 'INDEPENDENT':
        if provider is None or not provider.is_absolute():
            raise ValueError('REVIEWED_INDEPENDENT_PROVIDER_REQUIRED')
        secure(provider)
    elif provider is not None:
        raise ValueError('UNEXPECTED_PROVIDER')
    validate_runtime(config, environment, require_enabled=False,
                     evidence_provider=provider or Path('/usr/local/libexec/sales-churn-evidence'))
    planned = {Path(target): PACKAGE/source for source, target in MAPPING.items()
               if source_mode == 'TRUSTED_IMPORT' or not source.endswith('/churn_trusted_ftp.py')}
    if provider is not None:
        planned[Path('/usr/local/libexec/sales-churn-evidence')] = provider
    entries = manifest_entries(config)
    for target, source in planned.items():
        if digest(source) != entries.get(str(target)):
            raise ValueError('PACKAGE_NOT_APPROVED_BY_RUNTIME_MANIFEST')
        if target.exists() or target.is_symlink():
            secure(target)
        parent = target.parent
        while parent != Path('/'):
            if parent.exists() or parent.is_symlink():
                info = parent.lstat()
                if not stat.S_ISDIR(info.st_mode) or info.st_uid != 0 or info.st_mode & 0o022:
                    raise ValueError('UNTRUSTED_TARGET_DIRECTORY')
            parent = parent.parent
    for name, expected_hash in entries.items():
        path = Path(name)
        if path not in planned:
            secure(path)
            if digest(path) != expected_hash:
                raise ValueError('REVIEWED_RUNTIME_FILE_CHANGED')
    for directory in (STORAGE, Path('/run/sales-churn-monitor')):
        info = directory.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_mode & 0o002:
            raise ValueError('PROVISIONED_STORAGE_REQUIRED')
    frozen = {}
    for target, source in planned.items():
        data = source.read_bytes()
        if hashlib.sha256(data).hexdigest() != entries[str(target)]:
            raise ValueError('SOURCE_CHANGED_DURING_CHECK')
        frozen[target] = data
    return frozen


def atomic_copy(source, target, mode):
    fd, temporary = tempfile.mkstemp(prefix='.churn-install-', dir=target.parent)
    try:
        with os.fdopen(fd, 'wb') as output:
            output.write(source if isinstance(source, bytes) else source.read_bytes())
            output.flush()
            os.fsync(output.fileno())
            os.fchmod(output.fileno(), mode)
        os.replace(temporary, target)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def apply(planned):
    """Caller has completed checks. Preserve originals and rollback partial copies."""
    backup = Path(tempfile.mkdtemp(prefix='sales-churn-host-', dir='/var/backups'))
    backup.chmod(0o700)
    previous = {}
    for number, target in enumerate(planned):
        if target.exists():
            original = backup/str(number)
            shutil.copy2(target, original)
            previous[target] = (original, stat.S_IMODE(target.stat().st_mode))
        else:
            previous[target] = None
    (backup/'restore.json').write_text(json.dumps({str(k): {'file': v[0].name, 'mode': v[1]} if v else None
                                                  for k, v in previous.items()}, indent=2)+'\n')
    changed = []
    try:
        for target, source in planned.items():
            target.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
            changed.append(target)
            atomic_copy(source, target, 0o755 if target.parent == Path('/usr/local/libexec') else 0o644)
            if digest(target) != hashlib.sha256(source).hexdigest():
                raise ValueError('INSTALLED_HASH_MISMATCH')
    except BaseException:
        for target in reversed(changed):
            old = previous[target]
            if old:
                atomic_copy(old[0], target, old[1])
            else:
                target.unlink(missing_ok=True)
        raise
    return backup


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group()
    group.add_argument('--check', action='store_true', help='Default: checks only')
    group.add_argument('--apply', action='store_true', help='Explicit copy after all checks')
    parser.add_argument('--source-mode', required=True, choices=['INDEPENDENT', 'TRUSTED_IMPORT'])
    parser.add_argument('--config', type=Path, default=Path('/etc/sales/churn-permanent/host.json'))
    parser.add_argument('--evidence-provider', type=Path)
    args = parser.parse_args()
    try:
        if os.geteuid() != 0:
            raise ValueError('ROOT_REVIEW_CONTEXT_REQUIRED')
        check_host()
        planned = plan(args.config, args.source_mode, args.evidence_provider)
        for target, source in planned.items():
            print(str(target)+' sha256='+hashlib.sha256(source).hexdigest())
        if args.apply:
            # Recheck stops immediately before copies. Never stop an active unit ourselves.
            check_host()
            planned = plan(args.config, args.source_mode, args.evidence_provider)
            backup = apply(planned)
            print('Installed code only. Backup: '+str(backup))
        else:
            print('CHECK ONLY: no files, services, policies or databases changed')
        print('No daemon-reload, enable, start, timer activation or migration performed')
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
        parser.exit(1, 'HOST_INSTALL_BLOCKED: review package/config/manifest, inactive units and host prerequisites\n')


if __name__ == '__main__':
    main()
