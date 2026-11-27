"""Real sender transport -> pinned PHP receiver; disposable MariaDB only."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
import run_churn_automation as host

root = Path(os.environ['CALLTRACK_COMPAT_ROOT'])
assert subprocess.check_output(['git', '-C', str(root), 'rev-parse', 'HEAD'], text=True).strip() == '00a8c9c85c7c03835187e152f5b7fd9a60714971'
assert os.environ['ANALYTICS_TASK_TEST_DSN'] == 'mysql:host=analytics-test-mariadb;dbname=analytics_tasks_test'
with tempfile.TemporaryDirectory() as directory:
    bootstrap = Path(directory)/'bootstrap.php'
    bootstrap.write_text("<?php define('DB_PASS','');")
    php = ['php', '-d', 'auto_prepend_file='+str(bootstrap)]
    fixture = subprocess.run(php+[str(root/'tests/churn_permanent_cli_fixture.php')],
                             capture_output=True, text=True, check=True)
    data = json.loads(fixture.stdout.strip().splitlines()[-1])
    environment = dict(os.environ, **data['environment'], CALLTRACK_DB_HOST='analytics-test-mariadb',
                       CALLTRACK_DB_NAME='analytics_tasks_test', CALLTRACK_DB_USER='root',
                       CALLTRACK_ANALYTICS_MODE='enabled')
    page = data['page']
    event = json.loads(page['events'][0]['event_json'])
    assert event['permanent_policy'] == dict(version='churn-permanent/1', revision=2,
                                            producer_revision=2, daily_limit=2, cooldown_days=30)
    def count():
        code = "define('DB_PASS','');require $argv[1].'/api/config.php';echo getPdo()->query('SELECT COUNT(*) FROM manager_tasks')->fetchColumn();"
        return int(subprocess.check_output(['php', '-r', code, str(root)], env=environment))
    before = count()
    assert before == 12  # 5+5 exhausted historical pilot tasks, two fixture permanent episodes
    acknowledgements = []
    deliveries = []
    lose_ack = True
    def runner(argv, **kwargs):
        global lose_ack
        if 'app.churn_automation' in argv:
            action = argv[argv.index('app.churn_automation')+1]
            if action == 'page': return json.dumps(page)
            if action == 'ack':
                response = json.loads(kwargs['input_text'])
                if lose_ack:
                    lose_ack = False
                    raise host.ObservationError('RUNTIME_ERROR')
                acknowledgements.append(response)
                return '{"status":"SUCCEEDED"}'
            raise AssertionError(action)
        assert argv[0] == '/usr/bin/php'
        result = subprocess.run(php+argv[1:], input=kwargs['input_text'],
                                capture_output=True, text=True, env=environment, timeout=30)
        if result.returncode:
            raise host.ObservationError('RUNTIME_ERROR')
        if argv[-1] == 'deliver':
            deliveries.append(json.loads(result.stdout))
        return result.stdout
    args = ({'id': page['job_id'], 'status': 'RELEASED'}, {'calltrack_root': str(root)}, environment)
    try:
        host.process_job(*args, runner=runner, deadline=host.time.monotonic()+30, verify=lambda c: None)
        raise AssertionError('Lost ACK must fail')
    except host.ObservationError:
        pass
    assert not acknowledgements and count() == before+1
    host.process_job(*args, runner=runner, deadline=host.time.monotonic()+30, verify=lambda c: None)
    a, b = (response['receipts'][0] for response in deliveries)
    assert a['decision'] == 'TASK_CREATED' and b['decision'] == 'TASK_EXISTS'
    assert a['task_id'] == b['task_id'] and a['created_for_release'] and b['created_for_release']
    assert count() == before+1 and len(acknowledgements) == 1
    environment['CALLTRACK_ANALYTICS_MODE'] = 'disabled'
    try:
        host.process_job(*args, runner=runner, deadline=host.time.monotonic()+30, verify=lambda c: None)
        raise AssertionError('Disabled receiver must reject')
    except host.ObservationError:
        pass
    assert count() == before+1 and len(acknowledgements) == 1
print('sender_receiver_518: real signed preflight/delivery, 5+5/10 exhausted pilot, lost ACK, replay and disabled receiver OK')
