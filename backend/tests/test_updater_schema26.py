"""External artifact tests: fake Docker/Git/HTTP only, plus opt-in isolated PG."""

import hashlib, json, os, subprocess, sys
from pathlib import Path
import pytest
from test_update_sales_external import updater, SCRIPT, external_prefix, schema_payload
from test_import_control import pg

pytestmark = pytest.mark.skipif(
    not SCRIPT, reason="SALES_UPDATE_TEST_SCRIPT is required"
)


@pytest.fixture
def updater26(updater):
    root, project, active, original, run = updater
    state = json.loads((root / "state.json").read_text())
    state["target"] = "20261109_26"
    (root / "state.json").write_text(json.dumps(state))

    def launch(
        failure="",
        args=("--backend-only", "--migrate-25-to-26"),
        revision="20261030_25",
    ):
        return run(failure, args=args, revision=revision, prepared=True)

    return root, project, active, original, launch


@pytest.mark.parametrize(
    "failure",
    [
        "old-preflight",
        "dirty",
        "disk",
        "pull",
        "lock-busy",
        "image-entrypoint",
        "image-alembic",
        "command-alembic",
        "old-entrypoint",
        "launcher-extract",
        "unknown-launcher",
        "command-schema25",
        "image-new-locks",
    ],
)
def test_stage_failures_do_not_stop_or_replace_working25(updater26, failure):
    root, project, active, original, run = updater26
    result, state, events = run(failure)
    assert result.returncode != 0
    assert (
        state["revision"] == "20261030_25"
        and state["containers"]["backend"] == "old-backend"
    )
    assert not any(
        e["event"] in ("backend-stop", "up") or e.get("action") == "migrate"
        for e in events
    )
    assert active.read_bytes() == original


@pytest.mark.parametrize(
    "failure",
    [
        "migration-25",
        "migration-term",
        "migration-int",
        "backend-up",
        "backend-health",
        "backend-term",
        "backend-int",
        "applied-config",
        "candidate-check",
        "pg-lost",
        "frontend-image-drift",
    ],
)
def test_committed26_recovers_old_image_with26_bridge(updater26, failure):
    root, project, active, original, run = updater26
    result, state, events = run(failure)
    assert result.returncode != 0, result.stdout + result.stderr
    assert (
        state["revision"] == "20261109_26"
        and state["containers"]["backend"] == "old-backend"
    )
    runtime = state["runtime"]["backend"]
    assert runtime["command"] == ["--rollback", "--expected-revision", "20261109_26"]
    assert runtime["entrypoint"] == [
        "python",
        "-B",
        "/run/sales-schema25-recovery/start_backend.py",
    ]
    assert runtime["environment"]["ANALYTICS_AUTOMATION_ENABLED"] == "false"
    assert "MAINTENANCE" in result.stdout and "КРИТИЧЕСКАЯ" not in result.stdout
    assert (project / ".sales-update-recovery-required").is_file()
    assert active.read_bytes() == original
    assert any(e["event"] == "backend-stop" for e in events)


@pytest.mark.parametrize("failure", ["migration-24", "backend-stop"])
def test_precommit_failure_restarts25_only_after_quiesce_started(updater26, failure):
    root, project, active, original, run = updater26
    result, state, events = run(failure)
    assert result.returncode != 0
    assert (
        state["revision"] == "20261030_25"
        and state["containers"]["backend"] == "old-backend"
        and not state.get("stopped")
    )
    assert any(e["event"] == "backend-stop" for e in events)
    assert any(e["event"] == "up" and e["image"] == "old-backend" for e in events)
    assert not (project / "docker-compose.schema25-recovery.override.yml").exists()
    assert not (project / ".sales-update-recovery-required").exists()
    assert active.read_bytes() == original


@pytest.mark.parametrize(
    "failure",
    [
        "rollback-health",
        "rollback-up-before",
        "rollback-wrong-runtime",
        "recovery-settings-drift",
        "pg-rollback-busy",
    ],
)
def test_bad26_recovery_is_critical_and_preserves_every_checkpoint(updater26, failure):
    root, project, active, original, run = updater26
    result, state, events = run(failure)
    assert result.returncode != 0 and "КРИТИЧЕСКАЯ" in result.stdout
    assert (project / ".sales-update-recovery-required").exists()
    assert list((root / "backup").glob("update.*/rollback-start_backend.py"))
    assert list((root / "backup").glob("update.*/checkpoint.yml"))


@pytest.mark.parametrize(
    "args,revision",
    [
        (("--backend-only",), "20261030_25"),
        (("--backend-only", "--migrate-24-to-25"), "20261030_25"),
        (("--backend-only", "--migrate-25-to-26"), "20261022_24"),
        (("--frontend-only", "--migrate-25-to-26"), "20261030_25"),
        (("--migrate-24-to-25", "--migrate-25-to-26"), "20261030_25"),
    ],
)
def test_exact_migration_consent_and_no_shortcuts(updater26, args, revision):
    root, project, active, original, run = updater26
    result, state, events = run(args=args, revision=revision)
    assert result.returncode != 0 and state["revision"] == revision
    assert not any(
        e.get("action") == "migrate" or e["event"] in ("backend-stop", "up")
        for e in events
    )


@pytest.mark.parametrize(
    "revision,args",
    [
        ("20261030_25", ("--backend-only", "--migrate-25-to-26")),
        ("20261109_26", ("--backend-only",)),
        ("20261109_26", ("--backend-only", "--migrate-25-to-26")),
    ],
)
def test_prepared_success_and_matching_schema_never_build_or_remigrate(
    updater26, revision, args
):
    root, project, active, original, run = updater26
    result, state, events = run(args=args, revision=revision)
    assert result.returncode == 0, result.stdout + result.stderr
    assert (
        state["revision"] == "20261109_26"
        and state["containers"]["backend"] == "new-backend"
    )
    assert sum(e.get("action") == "migrate" for e in events) == int(
        revision == "20261030_25"
    )
    assert not any(e["event"] == "build" for e in events)
    assert not (project / ".sales-update-recovery-required").exists()
    assert not (project / "docker-compose.schema25-recovery.override.yml").exists()


def test_frontend_error_keeps_committed26_backend(updater26):
    root, project, active, original, run = updater26
    result, state, events = run("frontend-health", args=("--migrate-25-to-26",))
    assert result.returncode != 0
    assert state["revision"] == "20261109_26" and state["containers"] == {
        "backend": "new-backend",
        "frontend": "old-frontend",
    }
    assert not any(e["event"] == "up" and e["image"] == "old-backend" for e in events)
    assert not (project / "docker-compose.schema25-recovery.override.yml").exists()


def test_old_frontend_can_be_installed_alone_on26(updater26):
    root, project, active, original, run = updater26
    result, state, events = run(args=("--migrate-25-to-26",))
    assert result.returncode == 0, result.stdout + result.stderr
    offset = len(events)
    state["images"]["prepared-frontend"] = "old-frontend"
    (root / "state.json").write_text(json.dumps(state))
    result, state, events = run(args=("--frontend-only",), revision="20261109_26")
    assert result.returncode == 0, result.stdout + result.stderr
    assert state["containers"] == {"backend": "new-backend", "frontend": "old-frontend"}
    assert not any(
        e.get("service") == "backend" or e.get("action") == "migrate"
        for e in events[offset:]
    )


@pytest.mark.parametrize("committed", [False, True])
def test_named_job_is_stopped_before_26_outcome_classification(updater26, committed):
    root, project, active, original, run = updater26
    result, state, events = run(
        "migration-running-25" if committed else "migration-running"
    )
    assert result.returncode != 0 and not state.get("job")
    assert any(e["event"] == "migration-stop" for e in events)
    assert state["revision"] == ("20261109_26" if committed else "20261030_25")
    assert "КРИТИЧЕСКАЯ" not in result.stdout


def test_power_loss_retains_marker_and_blocks_blind_retry(updater26):
    root, project, active, original, run = updater26
    result, state, events = run("migration-kill")
    assert result.returncode != 0 and state["revision"] == "20261109_26"
    assert (project / ".sales-update-recovery-required").is_file()
    result, state, again = run(args=("--backend-only",), revision="20261109_26")
    assert result.returncode != 0 and len(again) == len(events)


@pytest.mark.parametrize("pg", ["20261030_25"], indirect=True)
@pytest.mark.parametrize(
    "matching_guard,keys",
    [
        (False, (734190221, 734190220, 734190117, 734190271, 734190272)),
        (True, (734190221, 734190220, 734190117)),
        (True, (734190221, 734190220, 734190117, 734190271)),
        (True, (734190221, 734190220, 734190117, 734190271, 734190272)),
    ],
)
def test_native_25_to26_guard_binding_and_old_models_bridge(pg, matching_guard, keys):
    import sqlalchemy as sa
    import start_backend as launcher
    from uuid import uuid4

    source = Path(__file__).parents[1] / "start_backend.py"
    payload = schema_payload()
    checksum = hashlib.sha256(source.read_bytes()).hexdigest()
    nonce = "updater26-" + uuid4().hex

    def job(action, expected, old_models=False):
        prefix = ""
        if old_models:
            prefix = (
                "import sqlalchemy as sa\nfrom app.models import Base\nold=sa.MetaData()\nfor table in Base.metadata.sorted_tables:\n if table.name not in "
                + repr(set(launcher.IMPORT_CONTROL_CONTRACT))
                + ":table.to_metadata(old)\nBase.metadata=old\n"
            )
        return subprocess.run(
            [
                sys.executable,
                "-B",
                "-c",
                prefix + payload,
                action,
                expected,
                nonce if matching_guard else nonce + "-wrong",
                str(source),
                checksum,
                "20261109_26",
            ],
            capture_output=True,
            text=True,
            env=dict(os.environ),
            timeout=30,
        )

    with pg[0].connect() as guard:
        guard.execute(
            sa.text("SELECT set_config('application_name',:nonce,false)"),
            {"nonce": nonce},
        )
        for key in keys:
            assert guard.scalar(
                sa.text("SELECT pg_try_advisory_lock(:key)"), {"key": key}
            )
        guard.commit()
        try:
            status = job("status", "")
            if not matching_guard or len(keys) != 5:
                assert (
                    status.returncode != 0
                    and "DEPLOY_SCHEMA_JOB_FAILED" in status.stderr
                )
                return
            assert status.returncode == 0 and status.stdout.strip() == "20261030_25"
            assert job("check", "20261030_25", True).returncode == 0
            migrated = job("migrate", "20261109_26")
            assert migrated.returncode == 0, migrated.stderr
            assert job("check", "20261109_26", True).returncode == 0
            assert job("check", "20261109_26").returncode == 0
            assert job("migrate", "20261030_25").returncode != 0
        finally:
            for key in reversed(keys):
                guard.execute(sa.text("SELECT pg_advisory_unlock(:key)"), {"key": key})
            guard.commit()


@pytest.mark.parametrize(
    "failure", ["backend-stop-before", "backend-stop-lying", "backend-stop-delayed"]
)
def test_interrupted_quiesce_is_settled_by_a_known_old_container(updater26, failure):
    root, project, active, original, run = updater26
    result, state, events = run(failure)
    assert result.returncode != 0 and state["revision"] == "20261030_25"
    assert not state.get("stopped") and not state.get("stop_pending")
    assert (
        sum(e["event"] == "up" and e.get("image") == "old-backend" for e in events) == 1
    )
    assert not any(e.get("action") == "migrate" for e in events)
    assert active.read_bytes() == original


def test_legacy25_image_is_not_a_downgrade_path_on26(updater):
    root, project, active, original, run = updater
    result, state, events = run(
        args=("--backend-only",), revision="20261109_26", prepared=True
    )
    assert result.returncode != 0 and state["revision"] == "20261109_26"
    assert not any(
        e.get("action") == "migrate" or e["event"] in ("backend-stop", "up")
        for e in events
    )
    assert active.read_bytes() == original


@pytest.mark.parametrize("target", ["20261030_25", "20261109_26"])
def test_actual_import_preflight_checks_target_specific_locks(target):
    import shlex

    source = Path(SCRIPT).read_text()
    line = next(
        line
        for line in source.splitlines()
        if line.strip().startswith("'import app.main;")
    )
    args = shlex.split(line.strip().removesuffix("\\").strip())
    assert len(args) == 2 and args[1] == "$TARGET_REVISION"
    env = dict(
        os.environ,
        DATABASE_URL="postgresql+asyncpg://fixture:synthetic-updater26@127.0.0.1:18529/import_control_test",
    )
    result = subprocess.run(
        [sys.executable, "-B", "-c", args[0], target],
        env=env,
        capture_output=True,
        text=True,
        timeout=20,
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("failure", ["rollback-identity", "rollback-empty-id"])
def test_transient_old_http200_is_not_proof_of_finished_recovery(updater26, failure):
    root, project, active, original, run = updater26
    result, state, events = run(failure)
    assert result.returncode != 0 and "КРИТИЧЕСКАЯ" in result.stdout
    assert state["revision"] == "20261030_25"
    assert (project / ".sales-update-recovery-required").exists()
