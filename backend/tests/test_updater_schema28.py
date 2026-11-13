"""Prepared-only schema27→28 release state machine; no real service starts."""

import hashlib, importlib.util, json, os, re, subprocess, sys
from pathlib import Path
import pytest
import test_update_sales_external as external
from test_update_sales_external import updater
from test_updater_schema27 import fake27
from test_import_control import pg

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import prepare_schema28_updater as gen
import prepare_schema28_recovery as recovery


def source26():
    value = os.environ.get("SALES_SCHEMA28_SOURCE")
    if not value:
        pytest.skip("Reviewed external updater26 required")
    return Path(value).read_bytes()


@pytest.fixture
def artifact28(tmp_path, monkeypatch):
    path = tmp_path / "update28.sh"
    path.write_bytes(gen.adapt(source26()))
    monkeypatch.setattr(external, "SCRIPT", str(path))
    return path


@pytest.fixture
def release28(artifact28, updater):
    root, project, active, original, run = updater
    code = fake27()
    mapping = {
        "20261109_26": gen.SOURCE,
        "20261111_27": gen.TARGET,
        "sales-schema27-migrate-": "sales-schema28-migrate-",
    }
    code = re.sub(
        "|".join(re.escape(k) for k in sorted(mapping, key=len, reverse=True)),
        lambda m: mapping[m[0]],
        code,
    )
    for f in (root / "bin").iterdir():
        f.write_text(code)
    state = json.loads((root / "state.json").read_text())
    state["target"] = gen.TARGET
    (root / "state.json").write_text(json.dumps(state))

    def launch(failure="", args=("--migrate-27-to-28",), revision=gen.SOURCE):
        return run(failure, args=args, revision=revision, prepared=True)

    return root, project, active, original, launch


def test_explicit_success_keeps_chain_and_no_build(release28):
    root, project, active, original, run = release28
    result, state, events = run()
    assert result.returncode == 0, result.stdout + result.stderr
    assert state["revision"] == gen.TARGET
    assert state["containers"] == {"backend": "new-backend", "frontend": "new-frontend"}
    assert not any(e["event"] == "build" for e in events)
    assert state["runtime"]["backend"]["environment"]["CHURN"] == "SHADOW"
    assert not (project / ".sales-update-recovery-required").exists()


@pytest.mark.parametrize(
    "revision,args",
    [
        (gen.SOURCE, ()),
        (gen.TARGET, ("--migrate-27-to-28",)),
        ("20261109_26", ("--migrate-27-to-28",)),
        (gen.SOURCE, ("--migrate-26-to-27",)),
    ],
)
def test_rejects_wrong_transition_no_blind_repeat(release28, revision, args):
    root, project, active, original, run = release28
    result, state, events = run(revision=revision, args=args)
    assert result.returncode != 0 and state["revision"] == revision
    assert not any(e.get("action") == "migrate" for e in events)
    assert active.read_bytes() == original


@pytest.mark.parametrize(
    "failure",
    [
        "image-new-locks",
        "history-running",
        "migration-24",
        "migration-25",
        "migration-term",
        "backend-up",
        "backend-health",
        "backend-int",
        "pg-lost",
        "settings-drift",
        "activated-drift",
    ],
)
def test_failures_preserve_checkpoints_and_restore_appropriate_image(
    release28, failure
):
    root, project, active, original, run = release28
    result, state, events = run(failure)
    assert result.returncode != 0
    assert state["containers"]["backend"] == "old-backend"
    assert active.read_bytes() == original
    if state["revision"] == gen.TARGET:
        assert state["runtime"]["backend"]["command"] == [
            "--rollback",
            "--expected-revision",
            gen.TARGET,
        ]
        assert (project / ".sales-update-recovery-required").exists()
    assert not any(e["event"] == "build" for e in events)


@pytest.mark.parametrize(
    "failure", ["frontend-health", "frontend-int", "frontend-term"]
)
def test_frontend_failure_never_reverts_healthy_backend28(release28, failure):
    root, project, active, original, run = release28
    result, state, events = run(failure)
    assert result.returncode != 0 and state["revision"] == gen.TARGET
    assert state["containers"] == {"backend": "new-backend", "frontend": "old-frontend"}
    assert not any(
        e["event"] == "up" and e.get("image") == "old-backend" for e in events
    )


def test_generators_no_overwrite_and_static(tmp_path):
    source = source26()
    for name, create in [
        ("update28.sh", gen.adapt),
        ("recover28.sh", recovery.prepare),
    ]:
        path = tmp_path / name
        data = create(source)
        assert data == create(source)
        path.write_bytes(data)
        subprocess.run(["bash", "-n", str(path)], check=True)
        subprocess.run(["shellcheck", str(path)], check=True)
    emitted = (
        gen.adapt(source)
        .decode()
        .split("cat <<'PY_SCHEMA'\n", 1)[1]
        .split("\nPY_SCHEMA", 1)[0]
    )
    assert emitted == gen.payload().strip()
    assert "elif action=='migrate':" not in recovery.prepare(source).decode()
    assert "--migrate-26-to-27" not in gen.adapt(source).decode()
    assert (
        hashlib.sha256((ROOT / "backend/start_backend.py").read_bytes()).hexdigest()
        == gen.LAUNCHER_SHA256
    )
    assert (
        hashlib.sha256(
            (
                ROOT / "backend/alembic/versions/20261112_28_sales_control.py"
            ).read_bytes()
        ).hexdigest()
        == gen.MIGRATION_SHA256
    )


def test_native_migration28_protected_settings_defaults_and_old_models(pg):
    from uuid import uuid4

    nonce = "sales28-" + uuid4().hex
    with pg[0].begin() as db:
        db.execute(
            __import__("sqlalchemy").text(
                "CREATE INDEX ix_sales_department_date_control ON sales(department,sale_date)"
            )
        )
    import sqlalchemy as sa

    keys = [734190221, 734190220, 734190117, 734190271, 734190272, 734190270]

    def job(action, expected=""):
        return subprocess.run(
            [
                sys.executable,
                "-B",
                "-c",
                gen.payload(),
                action,
                expected,
                nonce,
                str(ROOT / "backend/start_backend.py"),
                gen.LAUNCHER_SHA256,
                gen.TARGET,
            ],
            env=dict(os.environ),
            capture_output=True,
            text=True,
            timeout=30,
        )

    with pg[0].connect() as guard:
        guard.execute(
            sa.text("SELECT set_config('application_name',:nonce,false)"),
            {"nonce": nonce},
        )
        for k in keys:
            assert guard.scalar(sa.text("SELECT pg_try_advisory_lock(:k)"), {"k": k})
        guard.commit()
        try:
            before = job("safety")
            assert before.returncode == 0, before.stderr
            assert job("check", gen.SOURCE).returncode == 0
            migrated = job("migrate", gen.TARGET)
            assert migrated.returncode == 0, migrated.stderr + migrated.stdout
            after = job("initial-safety")
            assert after.returncode == 0, after.stderr
            assert (
                json.loads(before.stdout.split(" ", 1)[1])["sha256"]
                == json.loads(after.stdout.split(" ", 1)[1])["sha256"]
            )
            assert job("migrate", gen.TARGET).returncode != 0
            assert job("check", gen.TARGET).returncode == 0
            with pg[0].connect() as db:
                assert (
                    db.scalar(sa.text("SELECT enabled FROM sales_control_settings"))
                    is False
                )
                assert (
                    db.scalar(sa.text("SELECT count(*) FROM sales_control_deliveries"))
                    == 0
                )
        finally:
            for k in reversed(keys):
                guard.execute(sa.text("SELECT pg_advisory_unlock(:k)"), {"k": k})
            guard.commit()
