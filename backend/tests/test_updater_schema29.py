"""Prepared-only real Bash transactions with fake Docker; no production connections."""

import hashlib, json, os, re, subprocess, sys
from pathlib import Path
import pytest
import test_update_sales_external as external
from test_updater_schema27 import fake27
from test_update_sales_external import updater
from test_import_control import pg
from test_sales_control import sales_pg

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
import prepare_schema29_updater as gen
import prepare_schema29_recovery as recovery
import prepare_schema28_updater as previous
import prepare_schema28_recovery as previous_recovery
from test_schema27_recovery import IDS


def source26():
    value = os.environ.get("SALES_SCHEMA29_SOURCE")
    if not value:
        pytest.skip("Reviewed external updater26 required")
    return Path(value).read_bytes()


def fake29(immutable=True):
    code = gen.literal_map(
        fake27(),
        {
            "20261109_26": gen.SOURCE,
            "20261111_27": gen.TARGET,
            "sales-schema27-migrate-": "sales-schema29-migrate-",
        },
    )
    code = code.replace(
        "   if 'SCHEMA_SUPPORTED' in line:",
        "   if 'SCHEMA_SOURCE' in line:\n    print('SCHEMA_SOURCE' if read()['revision']=='20261112_28' else 'OTHER',flush=True);continue\n   if 'SCHEMA_TARGET' in line:\n    print('SCHEMA_TARGET' if read()['revision']=='20261114_29' else 'OTHER',flush=True);continue\n   if 'SCHEMA_SUPPORTED' in line:",
    )
    code = code.replace(
        "if args[0]=='exec':",
        "if args[0]=='exec' and args[1]=='sales-journal-backend':\n event('normal-process')\n if failure=='normal-process':sys.exit(1)\n sys.exit(0)\nif args[0]=='exec':",
    )
    code = code.replace(
        "runtime.get('command')",
        "runtime.get('command') or ['python','-B','start_backend.py']",
    )
    if immutable:
        for old, new in IDS.items():
            code = code.replace(repr(old), repr(new))
    return code


@pytest.fixture
def artifact29(tmp_path, monkeypatch):
    path = tmp_path / "update29.sh"
    path.write_bytes(gen.adapt(source26()))
    monkeypatch.setattr(external, "SCRIPT", str(path))
    return path


@pytest.fixture
def release29(artifact29, updater):
    root, project, active, original, _ = updater
    for f in (root / "bin").iterdir():
        f.write_text(fake29())

    def convert(v):
        if isinstance(v, dict):
            return {k: convert(x) for k, x in v.items()}
        if isinstance(v, list):
            return [convert(x) for x in v]
        return IDS.get(v, v) if isinstance(v, str) else v

    state = convert(json.loads((root / "state.json").read_text()))
    state["target"] = gen.TARGET
    state["images"].update({v: v for v in IDS.values()})
    (root / "state.json").write_text(json.dumps(state))
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

    def run(
        failure="", args=("--migrate-28-to-29",), revision=gen.SOURCE, extra_env=None
    ):
        state = json.loads((root / "state.json").read_text())
        state["revision"] = revision
        (root / "state.json").write_text(json.dumps(state))
        r = subprocess.run(
            ["bash", str(artifact29), *args],
            env=dict(env, FAILURE=failure, **(extra_env or {})),
            text=True,
            capture_output=True,
            timeout=35,
        )
        assert "SECRET_SENTINEL" not in r.stdout + r.stderr
        assert (root / "working-update_sales.sh").read_text() == "DO NOT MODIFY\n"
        events = (
            [json.loads(x) for x in (root / "events").read_text().splitlines()]
            if (root / "events").exists()
            else []
        )
        return r, json.loads((root / "state.json").read_text()), events

    return root, project, active, original, run


def test_source_pins_static_determinism_and_unchanged28(tmp_path):
    source = source26()
    assert (
        hashlib.sha256(previous.adapt(source)).hexdigest()
        == gen.BASELINE28_UPDATE_SHA256
    )
    assert (
        hashlib.sha256(previous_recovery.prepare(source)).hexdigest()
        == "5ea6a71f65af29324fb53a1a110a5d4bc7e25745339364fce8973632c4383692"
    )
    assert (
        hashlib.sha256((ROOT / "backend/start_backend.py").read_bytes()).hexdigest()
        == gen.LAUNCHER_SHA256
    )
    assert (
        hashlib.sha256(
            (
                ROOT
                / "backend/alembic/versions/20261114_29_category_season_snapshots.py"
            ).read_bytes()
        ).hexdigest()
        == gen.MIGRATION_SHA256
    )
    for name, create in [("up29.sh", gen.adapt), ("recovery29.sh", recovery.prepare)]:
        data = create(source)
        assert data == create(source)
        path = tmp_path / name
        path.write_bytes(data)
        subprocess.run(["bash", "-n", str(path)], check=True)
        subprocess.run(["shellcheck", str(path)], check=True)
        assert b"fill --confirm" not in data and b"--migrate-27-to-28" not in data
    assert "elif action=='migrate':" not in recovery.prepare(source).decode()
    with pytest.raises(ValueError):
        gen.adapt(source + b"\n")


def test_migrate_success_and_normal_update_never_build(release29):
    root, project, active, original, run = release29
    r, state, events = run()
    assert r.returncode == 0, r.stdout + r.stderr
    assert state["revision"] == gen.TARGET and state["containers"] == {
        "backend": IDS["new-backend"],
        "frontend": IDS["new-frontend"],
    }
    assert not any(e["event"] == "build" for e in events)
    assert any(e["event"] == "normal-process" for e in events)
    assert state["runtime"]["backend"]["environment"]["CHURN"] == "SHADOW"
    assert not (project / ".sales-update-recovery-required").exists()


def test_existing29_ordinary_update_has_no_ddl(release29):
    *_, run = release29
    r, state, events = run(args=(), revision=gen.TARGET)
    assert r.returncode == 0, r.stdout + r.stderr
    assert not any(e.get("action") == "migrate" for e in events)


@pytest.mark.parametrize(
    "revision,args",
    [
        (gen.SOURCE, ()),
        (gen.TARGET, ("--migrate-28-to-29",)),
        ("20261111_27", ("--migrate-28-to-29",)),
        (gen.SOURCE, ("--migrate-27-to-28",)),
        (gen.SOURCE, ("--frontend-only",)),
        (gen.SOURCE, ("--frontend-only", "--migrate-28-to-29")),
    ],
)
def test_wrong_transition_refused_before_switch(release29, revision, args):
    root, project, active, original, run = release29
    r, state, events = run(revision=revision, args=args)
    assert (
        r.returncode != 0
        and state["revision"] == revision
        and active.read_bytes() == original
    )
    assert not any(
        e["event"] in ("up", "tag") or e.get("action") == "migrate" for e in events
    )


@pytest.mark.parametrize(
    "failure",
    [
        "image-new-locks",
        "migration-hash",
        "migration-graph",
        "launcher-drift",
        "history-running",
        "migration-24",
        "migration-25",
        "migration-term",
        "migration-int",
        "backend-up",
        "backend-health",
        "backend-int",
        "normal-process",
        "pg-lost",
        "settings-drift",
        "activated-drift",
        "guard-before-ddl",
        "lock-busy",
        "control-busy",
        "applied-config",
        "dirty",
        "disk",
    ],
)
def test_failure_preserves_checkpoint_and_uses_actual_schema(release29, failure):
    root, project, active, original, run = release29
    r, state, events = run(failure)
    assert r.returncode != 0 and state["containers"]["backend"] == IDS["old-backend"], (
        r.stdout + r.stderr
    )
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
def test_frontend_failure_keeps_verified_backend29(release29, failure):
    *_, run = release29
    r, state, events = run(failure)
    assert r.returncode != 0 and state["containers"] == {
        "backend": IDS["new-backend"],
        "frontend": IDS["old-frontend"],
    }, (
        r.stdout + r.stderr
    )
    assert not any(
        e["event"] == "up" and e.get("image") == IDS["old-backend"] for e in events
    )


@pytest.mark.parametrize("reference", ["prepared", "sha256:123", "sha256:" + "a" * 63])
def test_tags_and_invalid_ids_refused_before_operations(release29, reference):
    *_, run = release29
    r, state, events = run(extra_env={"SALES_UPDATE_BACKEND_IMAGE": reference})
    assert r.returncode != 0 and not events


def test_native_schema29_payload_preserves_notifications_and_never_fills(sales_pg):
    import sqlalchemy as sa
    from uuid import uuid4

    nonce = "updater29-" + uuid4().hex
    sync, _, _, _ = sales_pg
    keys = [734190221, 734190220, 734190117, 734190271, 734190272, 734190270]

    def job(action, expected="", guard=None):
        return subprocess.run(
            [
                sys.executable,
                "-B",
                "-c",
                gen.payload(),
                action,
                expected,
                guard or nonce,
                str(ROOT / "backend/start_backend.py"),
                gen.LAUNCHER_SHA256,
                gen.TARGET,
            ],
            env=dict(os.environ),
            capture_output=True,
            text=True,
            timeout=30,
        )

    with sync.begin() as db:
        db.execute(sa.text("UPDATE sales_control_settings SET enabled=true"))
    with sync.connect() as guard:
        guard.execute(
            sa.text("SELECT set_config('application_name',:n,false)"), {"n": nonce}
        )
        for key in keys:
            assert guard.scalar(sa.text("SELECT pg_try_advisory_lock(:k)"), {"k": key})
        guard.commit()
        try:
            assert job("check", gen.SOURCE).returncode == 0
            before = job("safety")
            assert before.returncode == 0, before.stderr
            assert job("safety", guard="wrong-nonce").returncode != 0
            guard.execute(sa.text("SELECT pg_advisory_unlock(:k)"), {"k": keys[-1]})
            guard.commit()
            assert job("check", gen.SOURCE).returncode != 0
            guard.execute(sa.text("SELECT pg_advisory_lock(:k)"), {"k": keys[-1]})
            guard.commit()
            migrated = job("migrate", gen.TARGET)
            assert migrated.returncode == 0, migrated.stdout + migrated.stderr
            after = job("initial-safety")
            assert after.returncode == 0, after.stderr
            parse = lambda p: json.loads(
                next(
                    x
                    for x in p.stdout.splitlines()
                    if x.startswith("SALES_PROTECTED_V1 ")
                ).split(" ", 1)[1]
            )["sha256"]
            assert parse(before) == parse(after)
            assert job("migrate", gen.TARGET).returncode != 0
            assert job("check", gen.TARGET).returncode == 0
            with sync.begin() as db:
                assert (
                    db.scalar(sa.text("SELECT enabled FROM sales_control_settings"))
                    is True
                )
                assert (
                    db.scalar(
                        sa.text(
                            "SELECT active_id FROM category_season_state WHERE id=1"
                        )
                    )
                    is None
                )
                assert (
                    db.scalar(
                        sa.text("SELECT next_due FROM category_season_state WHERE id=1")
                    )
                    is None
                )
                assert (
                    db.scalar(sa.text("SELECT count(*) FROM category_season_snapshots"))
                    == 0
                )
                db.execute(
                    sa.text("UPDATE sales_control_settings SET send_time='10:00'")
                )
            changed = job("safety")
            assert changed.returncode == 0 and parse(changed) != parse(before)
        finally:
            for key in reversed(keys):
                guard.execute(sa.text("SELECT pg_advisory_unlock(:k)"), {"k": key})
            guard.commit()


def test_native_previous_code_with_readonly_bridge29_starts_maintenance(
    sales_pg, tmp_path
):
    """Real previous CODE and PG16, not a claim about a production image."""
    import socket
    import time
    import urllib.request
    import sqlalchemy as sa
    from alembic import command
    from alembic.config import Config

    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 8000))
    cfg = Config()
    cfg.set_main_option("script_location", str(ROOT / "backend/alembic"))
    command.upgrade(cfg, gen.TARGET)
    archive = subprocess.check_output(
        ["git", "archive", "f8b00e6", "backend"], cwd=ROOT
    )
    subprocess.run(["tar", "-x", "-C", str(tmp_path)], input=archive, check=True)
    old = tmp_path / "backend"
    bridge = tmp_path / "bridge29.py"
    bridge.write_bytes((ROOT / "backend/start_backend.py").read_bytes())
    bridge.chmod(0o444)
    assert hashlib.sha256(bridge.read_bytes()).hexdigest() == gen.LAUNCHER_SHA256
    env = dict(
        os.environ,
        PYTHONPATH=str(old),
        ANALYTICS_SIGNALS_ENABLED="false",
        ANALYTICS_SIGNALS_MODE="SHADOW",
        ANALYTICS_AUTOMATION_ENABLED="false",
        PATH=str(Path(sys.executable).parent) + os.pathsep + os.environ["PATH"],
    )
    imported = subprocess.check_output(
        [sys.executable, "-c", "import app.main;print(app.main.__file__)"],
        cwd=old,
        env=env,
        text=True,
    )
    assert str(old / "app/main.py") in imported
    output = tmp_path / "bridge-private.log"
    with output.open("w") as log:
        process = subprocess.Popen(
            [
                sys.executable,
                "-B",
                str(bridge),
                "--rollback",
                "--expected-revision",
                gen.TARGET,
            ],
            cwd=old,
            env=env,
            stdout=log,
            stderr=log,
        )
        try:
            healthy = False
            for _ in range(100):
                if process.poll() is not None:
                    break
                try:
                    with urllib.request.urlopen(
                        "http://127.0.0.1:8000/api/health", timeout=0.2
                    ) as response:
                        healthy = response.status == 200
                    if healthy:
                        break
                except OSError:
                    time.sleep(0.1)
            assert healthy, output.read_text()
            args = Path(f"/proc/{process.pid}/cmdline").read_bytes().split(b"\0")
            assert b"app.main:app" in args and b"--lifespan" in args
            assert args[args.index(b"--lifespan") + 1] == b"off"
            with sales_pg[0].connect() as db:
                assert (
                    db.scalar(sa.text("SELECT count(*) FROM category_season_snapshots"))
                    == 0
                )
                assert tuple(
                    db.execute(
                        sa.text("SELECT active_id,next_due FROM category_season_state")
                    ).one()
                ) == (None, None)
        finally:
            process.terminate()
            process.wait(timeout=25)


def test_protocol29_rejects_malformed_context_and_accepts_noise(tmp_path):
    script = gen.adapt(source26()).decode()
    lib = script[
        script.index("protected_result() {") : script.index("\nschema_job() {")
    ]
    log = tmp_path / "log"
    record = dict(
        protocol=1, guard="nonce", action="safety", revision=gen.TARGET, sha256="a" * 64
    )

    def parse(data):
        log.write_text(data)
        return subprocess.run(
            [
                "bash",
                "-c",
                lib + '\nGUARD_APPNAME=nonce\nprotected_result "$1" safety 20261114_29',
                "_",
                str(log),
            ],
            capture_output=True,
            text=True,
        )

    line = "SALES_PROTECTED_V1 " + json.dumps(record)
    for noise in ["created\nstarted\nwarning\n", "different diagnostic chatter\n"]:
        r = parse(noise + line + "\n")
        assert r.returncode == 0 and r.stdout == "a" * 64 + "\n"
    for wrong in [
        "",
        "a" * 64,
        line + "\n" + line,
        "noise " + line,
        line.replace("nonce", "wrong"),
        line.replace("safety", "initial-safety"),
        line.replace(gen.TARGET, gen.SOURCE),
        line.replace('"protocol": 1', '"protocol": true'),
        line.replace('"protocol": 1', '"protocol": 1,"protocol": 1'),
        line + " trailing",
    ]:
        assert parse(wrong).returncode != 0


def test_normal29_failure_has_durable_marker_and_bridge(release29):
    root, project, active, original, run = release29
    r, state, events = run("backend-health", args=(), revision=gen.TARGET)
    assert r.returncode != 0 and (project / ".sales-update-recovery-required").exists()
    assert state["runtime"]["backend"]["command"] == [
        "--rollback",
        "--expected-revision",
        gen.TARGET,
    ]


def test_unknown_image_id_and_resource_floor_refused(release29):
    *_, run = release29
    for extra in [
        {"SALES_UPDATE_BACKEND_IMAGE": "sha256:" + "f" * 64},
        {"SALES_UPDATE_MIN_FREE_KB": "1024"},
        {"SALES_UPDATE_MIN_AVAILABLE_KB": "100000"},
    ]:
        r, state, events = run(extra_env=extra)
        assert r.returncode != 0 and not any(e["event"] == "up" for e in events)


@pytest.mark.parametrize(
    "mode,selected,untouched",
    [
        ("--frontend-only", "frontend", "backend"),
        ("--backend-only", "backend", "frontend"),
    ],
)
def test_single_service_update_on29_preserves_other_service(
    release29, mode, selected, untouched
):
    *_, run = release29
    result, state, events = run(args=(mode,), revision=gen.TARGET)
    assert result.returncode == 0, result.stdout + result.stderr
    assert state["containers"][selected] == IDS["new-" + selected]
    assert state["containers"][untouched] == IDS["old-" + untouched]
    assert not any(e["event"] == "up" and e.get("service") == untouched for e in events)
    assert state["revision"] == gen.TARGET


@pytest.mark.parametrize("generator", [gen, recovery])
def test_generator_cli_never_overwrites_input_output_or_sidecar(tmp_path, generator):
    source = tmp_path / "reviewed26.sh"
    source.write_bytes(source26())
    destination = tmp_path / "release29.sh"
    args = [sys.executable, generator.__file__, str(source), str(destination)]
    first = subprocess.run(args, capture_output=True)
    assert first.returncode == 0, first.stderr
    actual = destination.read_bytes()
    checksum = destination.with_name(destination.name + ".sha256")
    assert checksum.read_text().split()[0] == hashlib.sha256(actual).hexdigest()
    assert subprocess.run(args, capture_output=True).returncode != 0
    assert destination.read_bytes() == actual and source.read_bytes() == source26()
    destination.unlink()  # Our temporary output only; orphan sidecar must block.
    assert subprocess.run(args, capture_output=True).returncode != 0
    assert not destination.exists()
    source.write_bytes(source26() + b"\n")
    other = tmp_path / "untrusted.sh"
    assert subprocess.run(args[:-1] + [str(other)], capture_output=True).returncode != 0
    assert not other.exists()


def test_protected_valid_record_cannot_mask_failed_job(tmp_path):
    script = gen.adapt(source26()).decode()
    lib = script[
        script.index("protected_result() {") : script.index("\nschema_job() {")
    ]
    command = (
        lib
        + r"""
BACKUP_DIR=$1
GUARD_APPNAME=fixture
ROLLBACK_LAUNCHER_SHA=fixture
logged() { printf '%s\n' 'SALES_PROTECTED_V1 {"protocol":1,"guard":"fixture","action":"safety","revision":"20261114_29","sha256":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"}' >"$BACKUP_DIR/$1.log"; return 7; }
protected_snapshot protected-before unused safety 20261114_29
"""
    )
    result = subprocess.run(
        ["bash", "-c", command, "_", str(tmp_path)], capture_output=True
    )
    assert result.returncode != 0
    assert not (tmp_path / "protected-before.digest").exists()


@pytest.mark.parametrize("eof", [False, True])
def test_six_lock_guard_real_coproc_descriptors(tmp_path, eof):
    import inspect

    # Exercise the actual generated functions with the reviewed Bash child
    # protocol harness, extended to the sixth lock introduced after that test.
    source = inspect.getsource(
        external.test_real_coproc_duplicate_fds_survive_original_close
    )
    source = source.replace("@pytest.mark.parametrize('eof',[False,True])\n", "")
    source = source.replace(
        "*pg_try_advisory_lock\\(734190272\\)*) response=MAIL_OK ;;",
        "*pg_try_advisory_lock\\(734190272\\)*) response=MAIL_OK ;;\n    *pg_try_advisory_lock\\(734190270\\)*) response=CONTROL_OK ;;",
    )
    source = source.replace(
        "events.count('answered')==18", "events.count('answered')==19"
    )
    namespace = {
        "Path": Path,
        "SCRIPT": str(tmp_path / "update29.sh"),
        "subprocess": subprocess,
    }
    Path(namespace["SCRIPT"]).write_bytes(gen.adapt(source26()))
    exec(compile(source, "six-lock-coproc-harness", "exec"), namespace)
    namespace[external.test_real_coproc_duplicate_fds_survive_original_close.__name__](
        tmp_path, eof
    )
