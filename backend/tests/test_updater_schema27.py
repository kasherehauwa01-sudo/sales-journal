"""Offline generator, fake Docker release state machine, native local PG and Bash FD tests."""

import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest
import test_update_sales_external as external
from test_update_sales_external import updater
from test_import_control import pg

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location(
    "prepare27", ROOT / "tools/prepare_schema27_updater.py"
)
gen = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gen)


def source26():
    name = os.environ.get("SALES_UPDATE_SCHEMA26_SOURCE")
    if not name:
        pytest.skip("Audited external SALES_UPDATE_SCHEMA26_SOURCE required")
    return Path(name)


@pytest.fixture
def artifact(tmp_path, monkeypatch):
    source = source26().read_bytes()
    output = tmp_path / "update_sales.schema27.sh"
    output.write_bytes(gen.adapt(source))
    monkeypatch.setattr(external, "SCRIPT", str(output))
    return output


def fake27():
    s = external.FAKE
    s = s.replace(
        "import yaml\nroot=",
        "import yaml\nyaml.SafeLoader.add_constructor('!reset',lambda loader,node:None)\nroot=",
    )
    s = s.replace("len(files) in (5,6)", "len(files) in (5,6,7)")
    s = s.replace(
        "if verb=='run':\n",
        "if verb=='run':\n assert not merged['services']['backend'].get('build')\n",
    )
    s = s.replace(
        "[734190221,734190220,734190117,734190271,734190272]",
        "[734190221,734190220,734190117,734190271,734190272,734190270]",
    )
    s = s.replace("'=5' in line", "'=6' in line").replace(
        "len(held)==5", "len(held)==6"
    )
    s = s.replace("734190272:'MAIL_OK'", "734190272:'MAIL_OK',734190270:'CONTROL_OK'")
    s = s.replace(
        "   if 'GUARD_OK' in line:",
        "   if 'SCHEMA_SUPPORTED' in line:\n    print('SCHEMA_SUPPORTED' if read()['revision'] in ('20261109_26','20261111_27') else 'UNSUPPORTED',flush=True);continue\n   if 'GUARD_OK' in line:",
    )
    s = s.replace(
        "(failure=='lock-busy' and key==734190117)",
        "(failure=='lock-busy' and key==734190117) or (failure=='control-busy' and key==734190270)",
    )
    s = s.replace(
        "'sales-schema26-migrate-'",
        "'sales-schema26-migrate-','sales-schema27-migrate-'",
    )
    old = "data=(root/'project/backend/start_backend.py').read_text().replace(\"DEFAULT_REVISION = '20261109_26'\", \"DEFAULT_REVISION = '\"+state.get('target','20261030_25')+\"'\")"
    assert old in s
    s = s.replace(
        old,
        "data=(root/'project/backend/start_backend.py').read_text()\n if failure=='launcher-drift':data+='\\n# changed candidate\\n'",
    )
    s = s.replace(
        "code.startswith('import app.main;')", "code.startswith('import hashlib,sys')"
    )
    s = s.replace(
        "if failure=='image-new-locks':fail()",
        "if failure in ('image-new-locks','migration-hash','migration-graph'):fail()",
    )
    s = s.replace(
        " if action=='status':print(state['revision']);sys.exit(0)",
        """ if action=='status':print(state['revision']);sys.exit(0)
 if action in ('safety','initial-safety'):
  if failure=='history-running' or (action=='initial-safety' and failure=='history-created'):fail()
  digest='a'*64 if not (failure=='settings-drift' and action=='initial-safety') else 'b'*64
  print('Compose warning '+action,file=sys.stderr)
  record=dict(protocol=1,guard=args[args.index('-c')+4],action=action,revision=state['revision'],sha256=digest)
  if failure=='activated-drift' and state['containers']['backend']=='new-backend':record['sha256']='c'*64
  if failure=='protected-empty':sys.exit(0)
  print('SALES_PROTECTED_V1 '+json.dumps(record))
  if failure=='protected-failed':sys.exit(7)
  if failure=='protected-duplicate':print('SALES_PROTECTED_V1 '+json.dumps(record))
  sys.exit(0)""",
    )
    s = s.replace(
        "assert state['revision']==('20261022_24' if expected=='20261030_25' else '20261030_25')",
        "assert expected=='20261111_27' and state['revision']=='20261109_26' and state.get('stopped')",
    )
    s = s.replace(
        "elif args[0]=='pull' and failure=='pull':fail()",
        "elif args[0]=='pull' and failure=='pull':fail()\n elif args[0]=='merge-base' and failure=='git-release':fail()",
    )
    s = s.replace(
        "    if failure in ('pg-lost','pg-rollback-busy')",
        "    if state.get('guard_fail_next'):sys.exit(2)\n    if failure in ('pg-lost','pg-rollback-busy')",
    )
    s = s.replace(
        " if action in ('safety','initial-safety'):\n",
        " if action in ('safety','initial-safety'):\n  if failure=='guard-before-ddl':state['guard_fail_next']=True;save(state)\n",
    )
    s = s.replace(
        " if failure=='backend-up' and image=='new-backend':fail()",
        " if service=='frontend' and image=='new-frontend' and failure in ('frontend-term','frontend-int'):os.kill(os.getppid(),signal.SIGTERM if failure=='frontend-term' else signal.SIGINT)\n if failure=='backend-up' and image=='new-backend':fail()",
    )
    return s


@pytest.fixture
def updater27(artifact, updater):
    root, project, active, original, run = updater
    for file in (root / "bin").iterdir():
        file.write_text(fake27())
    state = json.loads((root / "state.json").read_text())
    state["target"] = gen.TARGET
    (root / "state.json").write_text(json.dumps(state))

    def launch(
        failure="", args=("--migrate-26-to-27",), revision=gen.SOURCE, prepared=True
    ):
        return run(failure, args=args, revision=revision, prepared=prepared)

    return root, project, active, original, launch


def test_wrong_input_never_adapted():
    with pytest.raises(ValueError, match="SHA256"):
        gen.adapt(b"#!/bin/bash\n")


def test_generator_deterministic_no_overwrite_and_26_unchanged(tmp_path):
    src = source26()
    before = src.read_bytes()
    dest = tmp_path / "update_sales.schema27.sh"
    command = [
        sys.executable,
        str(ROOT / "tools/prepare_schema27_updater.py"),
        str(src),
        str(dest),
    ]
    assert subprocess.run(command, capture_output=True).returncode == 0
    assert dest.read_bytes() == gen.adapt(before) == gen.adapt(before)
    assert (
        dest.with_suffix(".sh.sha256").read_text().split()[0]
        == hashlib.sha256(dest.read_bytes()).hexdigest()
    )
    assert subprocess.run(command, capture_output=True).returncode != 0
    assert (
        subprocess.run(command[:-1] + [str(src)], capture_output=True).returncode != 0
    )
    assert (
        src.read_bytes() == before
        and hashlib.sha256(before).hexdigest() == gen.INPUT_SHA256
    )
    subprocess.run(["bash", "-n", str(dest)], check=True)
    assert (
        "__LAUNCHER_SHA__" not in dest.read_text()
        and "__MIGRATION_SHA__" not in dest.read_text()
    )
    assert (
        "--allow-build" not in dest.read_text()
        and "compose build" not in dest.read_text()
    )


@pytest.mark.parametrize(
    "failure",
    [
        "dirty",
        "disk",
        "pull",
        "lock-busy",
        "control-busy",
        "image-entrypoint",
        "image-alembic",
        "command-alembic",
        "command-schema25",
        "old-entrypoint",
        "old-preflight",
        "launcher-extract",
        "unknown-launcher",
        "launcher-drift",
        "image-new-locks",
        "migration-hash",
        "migration-graph",
        "git-release",
        "history-running",
        "guard-before-ddl",
        "protected-failed",
        "protected-empty",
        "protected-duplicate",
    ],
)
def test_preflight_refusal_never_stops_backend_or_changes_yaml(updater27, failure):
    root, project, active, original, run = updater27
    result, state, events = run(failure)
    assert result.returncode != 0
    assert state["revision"] == gen.SOURCE and active.read_bytes() == original
    assert not any(
        e["event"] in ("build", "backend-stop", "up") or e.get("action") == "migrate"
        for e in events
    )


@pytest.mark.parametrize(
    "revision,args",
    [
        ("20261030_25", ("--migrate-26-to-27",)),
        ("20261022_24", ("--migrate-26-to-27",)),
        ("20261112_28", ("--migrate-26-to-27",)),
        (gen.SOURCE, ("--backend-only",)),
        (gen.TARGET, ("--migrate-26-to-27",)),
        (gen.SOURCE, ("--migrate-25-to-26",)),
        (gen.SOURCE, ("--allow-build",)),
        (gen.SOURCE, ("--frontend-only", "--migrate-26-to-27")),
        (gen.SOURCE, ("--migrate-26-to-27", "--migrate-26-to-27")),
    ],
)
def test_exact_transition_consent_no_old_flags_or_build(updater27, revision, args):
    root, project, active, original, run = updater27
    result, state, events = run(args=args, revision=revision)
    assert result.returncode != 0 and state["revision"] == revision
    assert not any(
        e.get("action") == "migrate" or e["event"] in ("build", "backend-stop", "up")
        for e in events
    )
    assert active.read_bytes() == original


def test_success_quiesces_before_ddl_and_preserves_chain_and_configuration(updater27):
    root, project, active, original, run = updater27
    result, state, events = run()
    assert result.returncode == 0, result.stdout + result.stderr
    assert state["revision"] == gen.TARGET and state["containers"] == {
        "backend": "new-backend",
        "frontend": "new-frontend",
    }
    assert next(i for i, e in enumerate(events) if e["event"] == "backend-stop") < next(
        i for i, e in enumerate(events) if e.get("action") == "migrate"
    )
    assert not any(e["event"] == "build" for e in events)
    assert state["runtime"]["backend"]["environment"]["CHURN"] == "SHADOW"
    assert (
        state["runtime"]["backend"]["environment"]["CALLTRACK_SECRET"]
        == "SECRET_SENTINEL"
    )
    assert state["runtime"]["backend"]["volumes"][0]["target"] == "/run/mtls"
    assert not (project / ".sales-update-recovery-required").exists()
    assert list((root / "backup").glob("update.*/original.yml"))
    backup = next((root / "backup").glob("update.*"))
    assert (backup / "protected-before.log").read_bytes() != (
        backup / "protected-after.log"
    ).read_bytes()
    assert (
        (backup / "protected-before.digest").read_bytes()
        == (backup / "protected-after.digest").read_bytes()
        == (backup / "protected-activated.digest").read_bytes()
    )


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
        "settings-drift",
        "activated-drift",
        "history-created",
    ],
)
def test_actual27_uses_old_pinned_image_with_bridge_not_native26(updater27, failure):
    root, project, active, original, run = updater27
    result, state, events = run(failure)
    assert result.returncode != 0 and state["revision"] == gen.TARGET, (
        result.stdout + result.stderr
    )
    assert state["containers"]["backend"] == "old-backend"
    assert state["runtime"]["backend"]["command"] == [
        "--rollback",
        "--expected-revision",
        gen.TARGET,
    ]
    assert state["runtime"]["backend"]["entrypoint"] == [
        "python",
        "-B",
        "/run/sales-schema25-recovery/start_backend.py",
    ]
    assert (
        state["runtime"]["backend"]["environment"]["ANALYTICS_AUTOMATION_ENABLED"]
        == "false"
    )
    assert "MAINTENANCE" in result.stdout and "КРИТИЧЕСКАЯ" not in result.stdout
    assert (
        active.read_bytes() == original
        and (project / ".sales-update-recovery-required").exists()
    )
    assert list((root / "backup").glob("update.*/protected-before.log"))


@pytest.mark.parametrize(
    "failure",
    [
        "migration-24",
        "backend-stop",
        "backend-stop-before",
        "backend-stop-lying",
        "backend-stop-delayed",
    ],
)
def test_failed_stop_or_uncommitted_ddl_restores_source26(updater27, failure):
    root, project, active, original, run = updater27
    result, state, events = run(failure)
    assert result.returncode != 0 and state["revision"] == gen.SOURCE
    assert (
        state["containers"]["backend"] == "old-backend"
        and not state.get("stopped")
        and not state.get("stop_pending")
    )
    assert active.read_bytes() == original
    assert not (project / ".sales-update-recovery-required").exists()


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
def test_unverified_recovery_keeps_marker_and_all_backups(updater27, failure):
    root, project, active, original, run = updater27
    result, state, events = run(failure)
    assert result.returncode != 0 and "КРИТИЧЕСКАЯ" in result.stdout
    assert (project / ".sales-update-recovery-required").exists()
    assert list((root / "backup").glob("update.*/rollback-start_backend.py"))
    assert list((root / "backup").glob("update.*/checkpoint.yml"))


@pytest.mark.parametrize(
    "failure", ["frontend-health", "frontend-term", "frontend-int"]
)
def test_frontend_error_does_not_rollback_committed_backend(updater27, failure):
    root, project, active, original, run = updater27
    result, state, events = run(failure)
    assert result.returncode != 0 and state["containers"] == {
        "backend": "new-backend",
        "frontend": "old-frontend",
    }
    assert state["revision"] == gen.TARGET
    assert not any(
        e["event"] == "up" and e.get("image") == "old-backend" for e in events
    )


def test_matching27_update_no_ddl_and_frontend_can_rollback_separately(updater27):
    root, project, active, original, run = updater27
    result, state, events = run(args=(), revision=gen.TARGET)
    assert result.returncode == 0, result.stdout + result.stderr
    assert not any(
        e.get("action") == "migrate" or e["event"] == "backend-stop" for e in events
    )
    offset = len(events)
    state["images"]["prepared-frontend"] = "old-frontend"
    (root / "state.json").write_text(json.dumps(state))
    result, state, events = run(args=("--frontend-only",), revision=gen.TARGET)
    assert result.returncode == 0, result.stdout + result.stderr
    assert state["containers"] == {"backend": "new-backend", "frontend": "old-frontend"}
    assert not any(
        e.get("service") == "backend" or e.get("action") == "migrate"
        for e in events[offset:]
    )


@pytest.mark.parametrize(
    "failure", ["migration-kill", "migration-term", "migration-running-25"]
)
def test_partial_commit_blocks_blind_restart(updater27, failure):
    root, project, active, original, run = updater27
    result, state, events = run(failure)
    assert result.returncode != 0 and state["revision"] == gen.TARGET
    assert (project / ".sales-update-recovery-required").exists()
    result, state, again = run(args=(), revision=gen.TARGET)
    assert result.returncode != 0 and len(again) == len(events)


@pytest.mark.parametrize("failure", ["migration-running", "migration-running-25"])
def test_orphan_job_stops_before_actual_revision_classification(updater27, failure):
    root, project, active, original, run = updater27
    result, state, events = run(failure)
    assert result.returncode != 0 and not state.get("job")
    assert any(e["event"] == "migration-stop" for e in events)
    assert "КРИТИЧЕСКАЯ" not in result.stdout


def payload(artifact):
    source = artifact.read_text().rsplit('\nmain "$@"', 1)[0]
    result = subprocess.run(
        ["bash", "-c", source + "\nschema_python\n"],
        capture_output=True,
        text=True,
        check=True,
    )
    compile(result.stdout, "schema27-payload", "exec")
    return result.stdout


@pytest.mark.parametrize("pg", [gen.SOURCE], indirect=True)
@pytest.mark.parametrize("complete_guard", [False, True])
def test_native26_to27_guard_settings_idle_history_and_old_models_bridge(
    pg, artifact, complete_guard
):
    import sqlalchemy as sa
    import start_backend as launcher
    from uuid import uuid4

    nonce = "updater27-" + uuid4().hex
    source = ROOT / "backend/start_backend.py"
    keys = [734190221, 734190220, 734190117, 734190271, 734190272] + (
        [734190270] if complete_guard else []
    )
    code = payload(artifact)

    def job(action, expected="", old=False):
        prefix = ""
        if old:
            prefix = (
                "import sqlalchemy as sa\nfrom app.models import Base\nold=sa.MetaData()\nfor t in Base.metadata.sorted_tables:\n if t.name not in "
                + repr(set(launcher.IMPORT_HISTORY_CONTRACT))
                + ":t.to_metadata(old)\nBase.metadata=old\n"
            )
        return subprocess.run(
            [
                sys.executable,
                "-B",
                "-c",
                prefix + code,
                action,
                expected,
                nonce,
                str(source),
                gen.LAUNCHER_SHA256,
                gen.TARGET,
            ],
            env=dict(os.environ),
            capture_output=True,
            text=True,
            timeout=30,
        )

    with pg[0].begin() as db:
        db.execute(
            sa.text(
                "INSERT INTO ftp_config(id,protocol,host,port,username,password,directory,retries,retry_delay,enabled) VALUES(1,'FTP','fixture.invalid',21,'fixture','SECRET_SENTINEL','/',2,3,true)"
            )
        )
        db.execute(
            sa.text(
                "INSERT INTO import_control_config(id,enabled,automatic,email_enabled,start_date,recipients) VALUES(1,true,true,true,'2026-11-11','fixture@example.invalid')"
            )
        )
    with pg[0].connect() as guard:
        guard.execute(
            sa.text("SELECT set_config('application_name',:nonce,false)"),
            {"nonce": nonce},
        )
        for key in keys:
            assert guard.scalar(sa.text("SELECT pg_try_advisory_lock(:k)"), {"k": key})
        guard.commit()
        try:
            status = job("status")
            if not complete_guard:
                assert (
                    status.returncode != 0
                    and "DEPLOY_SCHEMA_JOB_FAILED" in status.stderr
                )
                return
            assert status.returncode == 0 and status.stdout.strip() == gen.SOURCE
            assert job("check", gen.SOURCE, True).returncode == 0
            before = job("safety")
            assert before.returncode == 0
            migration = job("migrate", gen.TARGET)
            assert migration.returncode == 0, migration.stderr
            after = job("initial-safety")
            assert after.returncode == 0
            assert (
                json.loads(after.stdout.split(" ", 1)[1])["sha256"]
                == json.loads(before.stdout.split(" ", 1)[1])["sha256"]
            )
            assert (
                "SECRET_SENTINEL"
                not in before.stdout + before.stderr + after.stdout + after.stderr
            )
            with pg[0].connect() as db:
                assert db.execute(
                    sa.text(
                        "SELECT enabled,automatic,email_enabled,start_date::text,recipients FROM import_control_config"
                    )
                ).one() == (True, True, True, "2026-11-11", "fixture@example.invalid")
                assert (
                    db.scalar(sa.text("SELECT password FROM ftp_config"))
                    == "SECRET_SENTINEL"
                )
            assert job("check", gen.TARGET, True).returncode == 0
            assert job("check", gen.TARGET).returncode == 0
            assert job("migrate", gen.TARGET).returncode != 0
            with pg[0].begin() as db:
                assert (
                    db.scalar(sa.text("SELECT count(*) FROM import_history_job")) == 0
                )
                db.execute(
                    sa.text(
                        "INSERT INTO import_history_job(id,status,plan,rules_hash,total,started_at,updated_at) VALUES(1,'paused','[]',:h,0,now(),now())"
                    ),
                    {"h": "a" * 64},
                )
            assert (
                job("safety").returncode == 0 and job("initial-safety").returncode != 0
            )
            with pg[0].begin() as db:
                db.execute(sa.text("UPDATE import_history_job SET status='running'"))
            assert job("safety").returncode != 0
            with pg[0].begin() as db:
                db.execute(sa.text("UPDATE import_history_job SET status='paused'"))
                db.execute(sa.text("UPDATE ftp_config SET password='CHANGED_SENTINEL'"))
            changed = job("safety")
            assert changed.returncode == 0
            assert (
                json.loads(changed.stdout.split(" ", 1)[1])["sha256"]
                != json.loads(after.stdout.split(" ", 1)[1])["sha256"]
            )
            assert "CHANGED_SENTINEL" not in changed.stdout + changed.stderr
            # A version label alone must never bypass frozen FK validation.
            with pg[0].begin() as db:
                fk = db.scalar(
                    sa.text(
                        "SELECT conname FROM pg_constraint WHERE conrelid='import_history_results'::regclass AND contype='f' LIMIT 1"
                    )
                )
                assert fk
                db.execute(
                    sa.text(
                        'ALTER TABLE import_history_results DROP CONSTRAINT "'
                        + fk
                        + '"'
                    )
                )
            assert job("check", gen.TARGET).returncode != 0
        finally:
            for key in reversed(keys):
                guard.execute(sa.text("SELECT pg_advisory_unlock(:k)"), {"k": key})
            guard.commit()


@pytest.mark.parametrize("eof", [False, True])
def test_real_coproc_all_six_locks_and_repeated_fd_io(artifact, tmp_path, eof):
    source = artifact.read_text()
    functions = source[
        source.index("guard_send() (") : source.index("ensure_rollback_guard() {")
    ]
    harness = r"""
set -Eeuo pipefail
BACKUP_DIR=$1 GUARD_TIMEOUT=2 GUARD_PID='' GUARD_WRITE='' GUARD_READ='' GUARD_APPNAME=fixture
trap 'release_guard' EXIT
docker() {
 bash --noprofile --norc -c '
  while IFS= read -r query; do
   case "$query" in
    *pg_try_advisory_lock\(734190221\)*) answer=WORKER_OK ;;
    *pg_try_advisory_lock\(734190220\)*) answer=SOURCE_OK ;;
    *pg_try_advisory_lock\(734190117\)*) answer=CATALOG_OK ;;
    *pg_try_advisory_lock\(734190271\)*) answer=FTP_OK ;;
    *pg_try_advisory_lock\(734190272\)*) answer=MAIL_OK ;;
    *pg_try_advisory_lock\(734190270\)*) answer=CONTROL_OK ;;
    *count\(DISTINCT\ objid\)*) answer=GUARD_OK ;;
    "\q") echo quit >> "$1";exit 0 ;;
    *) exit 42 ;;
   esac
   printf "%s\n" "$answer";echo answered >> "$1"
  done
  echo eof >> "$1"
 ' fixture "$BACKUP_DIR/child-events"
}
export -f docker
mkdir "$BACKUP_DIR/bin"
printf '#!/bin/bash\ndocker "$@"\n' > "$BACKUP_DIR/bin/docker"
chmod +x "$BACKUP_DIR/bin/docker"
export BACKUP_DIR PATH="$BACKUP_DIR/bin:$PATH"
start_seasonality_guard
saved_pid=$GUARD_PID
[[ ! -e /proc/$$/fd/${SEASON_GUARD[1]} && ! -e /proc/$$/fd/${SEASON_GUARD[0]} ]]
[[ -e /proc/$$/fd/$GUARD_WRITE && -e /proc/$$/fd/$GUARD_READ ]]
for ((i=0;i<12;i++));do assert_guard;done
"""
    if eof:
        harness += 'exec {GUARD_WRITE}>&-\nGUARD_WRITE=\'\'\nif IFS= read -r -t 2 unexpected <&"$GUARD_READ";then exit 43;fi\nwait "$saved_pid"\n'
    harness += 'release_guard\n! kill -0 "$saved_pid" 2>/dev/null\n[[ -z "$GUARD_PID$GUARD_WRITE$GUARD_READ" ]]\ntrap - EXIT\n'
    result = subprocess.run(
        [
            "bash",
            "--noprofile",
            "--norc",
            "-c",
            functions + harness,
            "fixture",
            str(tmp_path),
        ],
        capture_output=True,
        text=True,
        timeout=12,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    events = (tmp_path / "child-events").read_text().splitlines()
    assert events.count("answered") == 19 and events[-1] == ("eof" if eof else "quit")


def test_real_imports_and_pinned_migration_graph_preflight():
    # Keep Path semantics while resolving the container's absolute path locally.
    code = gen.IMPORT_PREFLIGHT.replace(
        "'/app/alembic/versions/20261111_27_import_history.py'",
        repr(str(ROOT / "backend/alembic/versions/20261111_27_import_history.py")),
    )
    code = code.replace("'/app/alembic'", repr(str(ROOT / "backend/alembic")))
    result = subprocess.run(
        [sys.executable, "-B", "-c", code, gen.TARGET],
        capture_output=True,
        text=True,
        timeout=20,
    )
    if hashlib.sha256((ROOT/'backend/start_backend.py').read_bytes()).hexdigest()!=gen.LAUNCHER_SHA256:
        assert result.returncode!=0  # updater27 MUST reject schema28 images/head.
        return
    assert result.returncode == 0, result.stderr
    assert (
        hashlib.sha256((ROOT / "backend/start_backend.py").read_bytes()).hexdigest()
        == gen.LAUNCHER_SHA256
    )


@pytest.mark.parametrize("failure", ["rollback-identity", "rollback-empty-id"])
def test_noop_recovery_rpc_and_empty_container_id_are_not_success(updater27, failure):
    root, project, active, original, run = updater27
    result, state, events = run(failure)
    assert result.returncode != 0 and "КРИТИЧЕСКАЯ" in result.stdout
    assert state["revision"] == gen.SOURCE
    assert (project / ".sales-update-recovery-required").exists()


def test_existing_checksum_is_not_overwritten_and_no_partial_output(tmp_path):
    src = source26()
    dest = tmp_path / "new.sh"
    checksum = tmp_path / "new.sh.sha256"
    checksum.write_text("Keep this backup")
    result = subprocess.run(
        [
            sys.executable,
            str(ROOT / "tools/prepare_schema27_updater.py"),
            str(src),
            str(dest),
        ],
        capture_output=True,
    )
    assert (
        result.returncode != 0
        and checksum.read_text() == "Keep this backup"
        and not dest.exists()
    )


@pytest.mark.skipif(
    os.environ.get("SCHEMA27_COMPOSE_TESTS") != "1", reason="Local Compose config only"
)
def test_real_compose_build_reset_and_recovery_delta_preserve_overrides(
    updater27, artifact
):
    root, project, active, original, run = updater27
    result, state, events = run("migration-25")
    assert result.returncode != 0 and "MAINTENANCE" in result.stdout
    env = dict(os.environ)
    for name in (
        "DOCKER_HOST",
        "DOCKER_CONTEXT",
        "DOCKER_TLS",
        "DOCKER_TLS_VERIFY",
        "DOCKER_CERT_PATH",
        "COMPOSE_PROFILES",
    ):
        env.pop(name, None)
    command = [
        "docker",
        "--host=unix:///var/run/docker.sock",
        "compose",
        "--env-file",
        "/dev/null",
    ]
    for file in [
        project / "docker-compose.yml",
        project / "docker-compose.override.yml",
        project / "docker-compose.churn-v3u.override.yml",
        root / "bindings.json",
        active,
    ]:
        command += ["-f", str(file)]
    config = json.loads(
        subprocess.run(
            command + ["config", "--format", "json"],
            env=env,
            capture_output=True,
            text=True,
            check=True,
        ).stdout
    )
    backups = next((root / "backup").glob("update.*"))
    runtime = command + ["-f", str(backups / "run-prebuilt-only.yml")]
    no_build = json.loads(
        subprocess.run(
            runtime + ["config", "--format", "json"],
            env=env,
            capture_output=True,
            text=True,
            check=True,
        ).stdout
    )
    assert config["services"]["backend"].get("build")
    assert not no_build["services"]["backend"].get("build")
    for service in config["services"]:
        before, after = dict(config["services"][service]), dict(
            no_build["services"][service]
        )
        if service == "backend":
            before.pop("build", None)
            after.pop("build", None)
        assert before == after
    # Real Compose normalization, not only the fake YAML merge.
    prefix = artifact.read_text().rsplit('\nmain "$@"', 1)[0]
    metadata = subprocess.run(
        ["bash", "-c", prefix + "\nconfig_metadata_python\n"],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    recovery = json.loads(
        subprocess.run(
            command
            + [
                "-f",
                str(project / "docker-compose.schema25-recovery.override.yml"),
                "config",
                "--format",
                "json",
            ],
            env=env,
            capture_output=True,
            text=True,
            check=True,
        ).stdout
    )

    def summary(value):
        return json.loads(
            subprocess.run(
                [sys.executable, "-c", metadata, gen.TARGET],
                input=json.dumps(value),
                capture_output=True,
                text=True,
                check=True,
            ).stdout
        )

    before, after = summary(config), summary(recovery)
    assert before["recovery_invariants_sha256"] == after["recovery_invariants_sha256"]
    assert all(after["recovery_contract"].values())
