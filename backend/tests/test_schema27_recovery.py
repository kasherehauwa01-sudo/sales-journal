"""No production: real Bash transactions, fake Docker, and native local PG payloads."""

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest
import yaml
from test_updater_schema27 import gen, source26, updater27, fake27, artifact, updater

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import prepare_schema27_recovery as recovery

IDS = {
    name: "sha256:" + char * 64
    for name, char in [
        ("old-backend", "1"),
        ("old-frontend", "2"),
        ("new-backend", "3"),
        ("new-frontend", "4"),
    ]
}


def test_protocol_context_and_noise(tmp_path):
    lib = gen.PROTECTED_SHELL
    record = dict(
        protocol=1,
        guard="fixture-nonce",
        action="safety",
        revision=gen.TARGET,
        sha256="a" * 64,
    )
    log = tmp_path / "protected.log"

    def run(data):
        log.write_text(data)
        return subprocess.run(
            [
                "bash",
                "-c",
                lib
                + '\nGUARD_APPNAME=fixture-nonce\nprotected_result "$1" safety 20261111_27',
                "_",
                str(log),
            ],
            text=True,
            capture_output=True,
        )

    line = "SALES_PROTECTED_V1 " + json.dumps(record)
    for noise in (
        "Container fixture Created\nContainer fixture Started\nCompose warning\n",
        "different stderr\n",
    ):
        result = run(noise + line + "\n")
        assert result.returncode == 0 and result.stdout == "a" * 64 + "\n"
    bad = [
        "",
        "a" * 64,
        line + "\n" + line,
        "prefix " + line,
        line.replace("fixture-nonce", "another"),
        line.replace("safety", "initial-safety"),
        line.replace(gen.TARGET, gen.SOURCE),
        line.replace('"protocol": 1', '"protocol": true'),
        line.replace('"protocol": 1', '"protocol": 1,"protocol": 1'),
        line.replace("a" * 64, "A" * 64),
        line + " trailing",
        "SALES_PROTECTED_V2 {}",
        line + "\nSALES_PROTECTED_BROKEN",
    ]
    for data in bad:
        result = run(data)
        assert result.returncode != 0, data


def test_valid_record_from_failed_process_is_not_accepted(tmp_path):
    # A valid stdout line must not mask a failed compose/job exit.
    command = (
        gen.PROTECTED_SHELL
        + r"""
BACKUP_DIR=$1
GUARD_APPNAME=fixture
logged() { printf '%s\n' 'SALES_PROTECTED_V1 {"protocol":1,"guard":"fixture","action":"safety","revision":"20261111_27","sha256":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"}' >"$BACKUP_DIR/$1.log"; return 7; }
ROLLBACK_LAUNCHER_SHA=fixture
protected_snapshot protected-before unused safety 20261111_27
"""
    )
    result = subprocess.run(
        ["bash", "-c", command, "_", str(tmp_path)], capture_output=True
    )
    assert (
        result.returncode != 0 and not (tmp_path / "protected-before.digest").exists()
    )


@pytest.fixture
def recovery27(updater27, tmp_path):
    root, project, active, original, run = updater27
    compose = project / "docker-compose.yml"
    data = yaml.safe_load(compose.read_text())
    data["services"]["backend"]["command"] = ["python", "-B", "start_backend.py"]
    compose.write_text(yaml.safe_dump(data))
    # Reproduce a committed migration with healthy native26+bridge27 recovery.
    result, state, events = run("migration-25")
    assert result.returncode != 0 and state["revision"] == gen.TARGET
    checkpoint = next((root / "backup").glob("update.*"))
    marker = project / ".sales-update-recovery-required"
    before = marker.read_bytes()
    original_recovery = (
        project / "docker-compose.schema25-recovery.override.yml"
    ).read_bytes()
    # Legacy production evidence is deliberately NOT parsed as a trusted result.
    (checkpoint / "protected-before.log").write_text(
        "compose old\nnoise\nwarning\n" + "a" * 64 + "\n"
    )
    script = root / "recover27.sh"
    script.write_bytes(recovery.prepare(source26().read_bytes()))

    def convert(value):
        if isinstance(value, dict):
            return {k: convert(v) for k, v in value.items()}
        if isinstance(value, list):
            return [convert(v) for v in value]
        return IDS.get(value, value) if isinstance(value, str) else value

    state = convert(state)
    state["images"].update({v: v for v in IDS.values()})
    (root / "state.json").write_text(json.dumps(state))
    fake = fake27()
    fake = fake.replace(
        "if args[0]=='exec':",
        """if args[0]=='exec' and args[1]=='sales-journal-backend':
 event('normal-process')
 if failure=='normal-process':sys.exit(1)
 sys.exit(0)
if args[0]=='exec':""",
    )
    fake = fake.replace(
        "   if 'GUARD_OK' in line:",
        "   if 'SCHEMA27' in line:\n    print('SCHEMA27' if read()['revision']=='20261111_27' else 'WRONG',flush=True);continue\n   if 'GUARD_OK' in line:",
    )
    fake = fake.replace(
        "  digest='a'*64", "  digest='b'*64 if failure=='protected-drift' else 'a'*64"
    )
    fake = fake.replace(
        " if failure=='frontend-image-drift'", " if failure=='frontend-image-drift'"
    )
    fake = fake.replace(
        "if kind=='mv':",
        """if kind=='mv':
 if failure=='archive-failed' and args[-1].endswith('.saved'):sys.exit(1)
 if failure=='neutral-term' and args[-1].endswith('docker-compose.schema25-recovery.override.yml') and not read().get('signalled'):
  result=subprocess.run(['/usr/bin/mv',*args]);s=read();s['signalled']=True;save(s);os.kill(os.getppid(),signal.SIGTERM);sys.exit(result.returncode)
""",
    )
    fake = fake.replace(
        " service=args[-1];image=state['images'][merged['services'][service]['image']];event('up',service=service,image=image)",
        " service=args[-1];image=state['images'][merged['services'][service]['image']];event('up',service=service,image=image)\n if failure=='backend-noop' and service=='backend' and image=='new-backend':sys.exit(0)\n if failure=='frontend-up' and service=='frontend' and image=='new-frontend':fail()",
    )
    for name, value in IDS.items():
        fake = fake.replace(repr(name), repr(value))
    for f in (root / "bin").iterdir():
        f.write_text(fake)
    offset = len(events)
    env = dict(
        os.environ,
        PATH=str(root / "bin") + os.pathsep + os.environ["PATH"],
        FAKE_ROOT=str(root),
        SALES_PROJECT_DIR=str(project),
        SALES_CALLTRACK_COMPOSE=str(root / "bindings.json"),
        SALES_UPDATE_LOCK_FILE=str(root / "lock"),
        SALES_UPDATE_BACKUP_ROOT=str(root / "backup"),
        SALES_UPDATE_MEMINFO_FILE=str(root / "meminfo"),
        SALES_UPDATE_HEALTH_ATTEMPTS="2",
        SALES_UPDATE_HEALTH_DELAY="0",
        SALES_UPDATE_GUARD_TIMEOUT="2",
        SALES_UPDATE_BACKEND_IMAGE=IDS["new-backend"],
        SALES_UPDATE_FRONTEND_IMAGE=IDS["new-frontend"],
    )
    args = [
        "--checkpoint",
        str(checkpoint),
        "--protected-sha256",
        "a" * 64,
        "--old-backend-id",
        IDS["old-backend"],
        "--old-frontend-id",
        IDS["old-frontend"],
    ]

    def launch(failure="", extra_env=None):
        result = subprocess.run(
            ["bash", str(script), *args],
            env=dict(env, FAILURE=failure, **(extra_env or {})),
            text=True,
            capture_output=True,
            timeout=40,
        )
        state = json.loads((root / "state.json").read_text())
        events = [json.loads(x) for x in (root / "events").read_text().splitlines()][
            offset:
        ]
        assert "SECRET_SENTINEL" not in result.stdout + result.stderr
        assert (root / "working-update_sales.sh").read_text() == "DO NOT MODIFY\n"
        assert state["revision"] in (
            gen.TARGET,
            gen.SOURCE,
        )  # test deliberately changes source in one refusal case
        assert not any(
            e.get("action") == "migrate" or e["event"] == "build" for e in events
        )
        return result, state, events

    return root, project, checkpoint, marker, before, original_recovery, launch


def test_recovery_success_schema27_no_ddl(recovery27):
    root, project, cp, marker, before, original, run = recovery27
    result, state, events = run()
    assert result.returncode == 0, result.stdout + result.stderr
    assert state["containers"] == {
        "backend": IDS["new-backend"],
        "frontend": IDS["new-frontend"],
    }
    assert not marker.exists()
    assert not (project / "docker-compose.schema25-recovery.override.yml").exists()
    assert list(
        project.glob("docker-compose.schema25-recovery.override.yml.exit27-*.saved")
    )
    assert state["runtime"]["backend"].get("entrypoint") in (None, [])
    assert (
        state["runtime"]["backend"]["environment"]["CALLTRACK_SECRET"]
        == "SECRET_SENTINEL"
    )
    assert any(e["event"] == "normal-process" for e in events)


@pytest.mark.parametrize(
    "failure",
    [
        "backend-up",
        "backend-health",
        "backend-term",
        "backend-int",
        "normal-process",
        "pg-lost",
        "neutral-term",
        "backend-noop",
    ],
)
def test_backend_failure_restores_original_bridge_and_marker(recovery27, failure):
    root, project, cp, marker, before, original, run = recovery27
    result, state, events = run(failure)
    assert result.returncode != 0
    assert state["containers"]["backend"] == IDS["old-backend"], (
        result.stdout + result.stderr
    )
    assert state["runtime"]["backend"]["command"] == [
        "--rollback",
        "--expected-revision",
        gen.TARGET,
    ]
    assert marker.read_bytes() == before
    assert (
        project / "docker-compose.schema25-recovery.override.yml"
    ).read_bytes() == original


@pytest.mark.parametrize(
    "failure", ["frontend-health", "frontend-term", "frontend-int", "frontend-up"]
)
def test_frontend_failure_preserves_backend_and_resume(recovery27, failure):
    root, project, cp, marker, before, original, run = recovery27
    result, state, events = run(failure)
    assert result.returncode != 0
    assert state["containers"] == {
        "backend": IDS["new-backend"],
        "frontend": IDS["old-frontend"],
    }, (
        result.stdout + result.stderr
    )
    assert marker.read_bytes() == before
    assert not any(
        e["event"] == "up" and e.get("image") == IDS["old-backend"] for e in events
    )
    count = len(events)
    result, state, events = run()
    assert result.returncode == 0, result.stdout + result.stderr
    assert not any(
        e["event"] == "up" and e.get("service") == "backend" for e in events[count:]
    )
    assert not marker.exists()


@pytest.mark.parametrize(
    "failure",
    [
        "control-busy",
        "history-running",
        "protected-drift",
        "image-new-locks",
        "launcher-drift",
        "disk",
    ],
)
def test_preflight_refusal_keeps_running_recovery_unchanged(recovery27, failure):
    root, project, cp, marker, before, original, run = recovery27
    result, state, events = run(failure)
    assert result.returncode != 0
    assert not any(e["event"] == "up" for e in events)
    assert marker.read_bytes() == before
    assert (
        project / "docker-compose.schema25-recovery.override.yml"
    ).read_bytes() == original


def test_recovery_refuses_schema26_without_ddl(recovery27):
    root, project, cp, marker, before, original, run = recovery27
    state = json.loads((root / "state.json").read_text())
    state["revision"] = gen.SOURCE
    (root / "state.json").write_text(json.dumps(state))
    result, state, events = run()
    assert result.returncode != 0 and not any(e["event"] == "up" for e in events)
    assert marker.read_bytes() == before


def test_generator_no_overwrite_and_static_checks(tmp_path):
    dest = tmp_path / "exit27.sh"
    cmd = [
        sys.executable,
        str(ROOT / "tools/prepare_schema27_recovery.py"),
        str(source26()),
        str(dest),
    ]
    assert subprocess.run(cmd, capture_output=True).returncode == 0
    assert dest.read_bytes() == recovery.prepare(source26().read_bytes())
    assert subprocess.run(cmd, capture_output=True).returncode != 0
    subprocess.run(["bash", "-n", str(dest)], check=True)
    subprocess.run(["shellcheck", str(dest)], check=True)


def test_archive_failure_retains_marker_and_normal_backend(recovery27):
    root, project, cp, marker, before, original, run = recovery27
    result, state, events = run("archive-failed")
    assert result.returncode != 0 and marker.read_bytes() == before
    assert state["containers"]["backend"] == IDS["new-backend"]
    assert (project / "docker-compose.schema25-recovery.override.yml").exists()
    result, state, events = run()
    assert result.returncode == 0, result.stdout + result.stderr
    assert not marker.exists()


def test_interrupted_switching_refuses_blind_retry(recovery27):
    root, project, cp, marker, before, original, run = recovery27
    result, state, events = run("backend-health")
    assert result.returncode != 0
    path = cp / "exit27-state.json"
    data = json.loads(path.read_text())
    data["phase"] = "switching"
    path.write_text(json.dumps(data))
    count = len(events)
    result, state, events = run()
    assert result.returncode != 0 and len(events) == count
    assert marker.read_bytes() == before


def test_marker_tampering_refused_before_operations(recovery27):
    root, project, cp, marker, before, original, run = recovery27
    marker.write_text("run_id=untrusted\nbackup=/wrong/checkpoint\n")
    result, state, events = run()
    assert result.returncode != 0 and not events
    assert marker.exists()


def test_resume_refuses_server_only_config_drift(recovery27):
    root, project, cp, marker, before, original, run = recovery27
    result, state, events = run("frontend-health")
    assert (
        result.returncode != 0 and state["containers"]["backend"] == IDS["new-backend"]
    )
    count = len(events)
    path = project / "docker-compose.churn-v3u.override.yml"
    path.write_text("services: {backend: {environment: {CHURN: unexpected}}}\n")
    result, state, events = run()
    assert result.returncode != 0
    assert not any(e["event"] == "up" for e in events[count:])
    assert marker.read_bytes() == before
    assert state["containers"]["backend"] == IDS["new-backend"]
