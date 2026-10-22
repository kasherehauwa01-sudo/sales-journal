"""Opt-in real-container SIGTERM regression, restricted to an isolated fixture DB."""
import json
import os
import subprocess
import time
from uuid import uuid4

import pytest


@pytest.mark.skipif(os.environ.get('BACKEND_SHUTDOWN_TEST') != '1', reason='Explicit local Docker fixture required')
def test_backend_sigterm_reaches_uvicorn_and_finishes_shutdown():
    image = os.environ['BACKEND_SHUTDOWN_TEST_IMAGE']
    assert image in {'churn-0e33c2c:pristine', 'churn-sigterm:fixed'}
    database = 'postgresql+asyncpg://fixture:synthetic-only@churn-sigterm-test-pg:5432/shutdown_test'
    name = 'churn-shutdown-' + uuid4().hex
    env = os.environ.copy()
    for key in ('DOCKER_HOST', 'DOCKER_CONTEXT', 'DOCKER_TLS', 'DOCKER_TLS_VERIFY', 'DOCKER_CERT_PATH'):
        env.pop(key, None)

    def docker(*args, check=True):
        return subprocess.run(['docker', '--host=unix:///var/run/docker.sock', *args],
                              env=env, text=True, capture_output=True, check=check, timeout=60)

    docker('run', '-d', '--name', name, '--network', 'rollback-churn-test',
           '-e', 'DATABASE_URL=' + database, '-e', 'ANALYTICS_SIGNALS_ENABLED=false',
           '-e', 'CLIENTS_VR_API_URL=', '-e', 'VRCATALOG_API_URL=', image)
    try:
        for _ in range(100):
            result = docker('exec', name, 'python', '-B', '-c',
                            "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/health',timeout=1)", check=False)
            if result.returncode == 0:
                break
            time.sleep(.1)
        else:
            pytest.fail('Isolated backend did not become healthy')
        started = time.monotonic()
        docker('stop', '--timeout', '5', name)
        elapsed = time.monotonic() - started
        state = json.loads(docker('inspect', '--format', '{{json .State}}', name).stdout)
        logs = docker('logs', name)
        assert state['ExitCode'] == 0, (state['ExitCode'], logs.stdout, logs.stderr)
        assert elapsed < 5, elapsed
        assert 'Application shutdown complete' in logs.stdout + logs.stderr
    finally:
        # Preserve this owned test container/logs for audit; never remove external objects.
        docker('stop', '--timeout', '2', name, check=False)
