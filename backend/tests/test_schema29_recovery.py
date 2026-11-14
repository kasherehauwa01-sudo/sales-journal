"""Reuse the reviewed recovery transaction tests with strict schema29 artifacts."""

import pytest
import test_schema27_recovery as shared
from test_updater_schema29 import (
    gen,
    recovery,
    source26,
    fake29,
    release29,
    artifact29,
    updater,
)


@pytest.fixture
def recovery29(release29, tmp_path, monkeypatch):
    class Source:
        def read_bytes(self):
            return source26()

    monkeypatch.setattr(shared, "gen", gen)
    monkeypatch.setattr(shared, "recovery", recovery)
    monkeypatch.setattr(shared, "source26", lambda: Source())
    monkeypatch.setattr(shared, "fake27", lambda: fake29(immutable=False))
    value = shared.recovery27.__wrapped__(release29, tmp_path)
    for f in (value[0] / "bin").iterdir():
        code = (
            f.read_text()
            .replace("SCHEMA27", "SCHEMA29")
            .replace(
                "read()['revision']=='20261111_27'", "read()['revision']=='20261114_29'"
            )
        )
        f.write_text(code)
    return value


def test_success_and_marker_last_no_ddl(recovery29):
    root, project, cp, marker, before, original, run = recovery29
    r, state, events = run()
    assert r.returncode == 0, r.stdout + r.stderr
    assert not marker.exists() and state["containers"] == {
        "backend": shared.IDS["new-backend"],
        "frontend": shared.IDS["new-frontend"],
    }
    assert list(
        project.glob("docker-compose.schema25-recovery.override.yml.exit29-*.saved")
    )


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
def test_failure_restores_bridge29_and_keeps_marker(recovery29, failure):
    shared.test_backend_failure_restores_original_bridge_and_marker(recovery29, failure)


@pytest.mark.parametrize(
    "failure", ["frontend-health", "frontend-term", "frontend-int", "frontend-up"]
)
def test_frontend_failure_resume_never_restarts_committed_backend(recovery29, failure):
    shared.test_frontend_failure_preserves_backend_and_resume(recovery29, failure)


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
def test_preflight_refuses_without_mutation(recovery29, failure):
    shared.test_preflight_refusal_keeps_running_recovery_unchanged(recovery29, failure)


def test_wrong_schema_refused_without_ddl(recovery29):
    shared.test_recovery_refuses_schema26_without_ddl(recovery29)


def test_marker_tamper_refused(recovery29):
    shared.test_marker_tampering_refused_before_operations(recovery29)


def test_config_drift_preserves_backend_and_marker(recovery29):
    shared.test_resume_refuses_server_only_config_drift(recovery29)


def test_archive_failure_safe_resume(recovery29):
    shared.test_archive_failure_retains_marker_and_normal_backend(recovery29)


def test_interrupted_ambiguous_switch_no_blind_resume(recovery29):
    root, project, cp, marker, before, original, run = recovery29
    r, state, events = run("backend-health")
    assert r.returncode != 0
    path = cp / "exit29-state.json"
    import json

    state = json.loads(path.read_text())
    state["phase"] = "switching"
    path.write_text(json.dumps(state))
    count = len(events)
    r, state, events = run()
    assert r.returncode != 0 and len(events) == count and marker.read_bytes() == before
