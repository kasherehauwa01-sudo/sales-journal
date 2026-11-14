"""Opt-in real local Docker/PG16 transaction, old CODE build, never production images.
Uses stand-in dependency layer sales-updater29-test-deps:local; no browser/PDF check.
Only creates new named containers when the reviewed production names are absent.
No volumes/images are deleted; stops this fixture's own services on exit.
"""

import hashlib, json, os, secrets, socket, subprocess, sys, time, uuid
from pathlib import Path
import pytest
import yaml
import tempfile
import shutil
from test_updater_schema29 import gen, recovery, source26, ROOT

pytestmark = pytest.mark.skipif(
    os.environ.get("SALES_SCHEMA29_COMPOSE") != "1",
    reason="Explicit isolated local Compose only",
)
DOCKER = ["docker", "--host=unix:///var/run/docker.sock"]


def test_real_transition_bridge_recovery_and_frontend_independence(tmp_path):
    assert str(ROOT).startswith("/workspace/")
    for name in ["sales-journal-backend", "sales-journal-frontend", "sales-journal-db"]:
        assert (
            subprocess.run([*DOCKER, "inspect", name], capture_output=True).returncode
            != 0
        ), "Refuse existing container"
    for port in [8010, 8011]:
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", port))
    subprocess.run(
        [*DOCKER, "image", "inspect", "sales-updater29-test-deps:local"],
        stdout=subprocess.DEVNULL,
        check=True,
    )
    # Never put PostgreSQL/checkpoints under pytest's automatic temp cleanup.
    work = Path(tempfile.mkdtemp(prefix="sales-updater29-compose-"))

    def command(args, *, cwd=None, env=None, timeout=90):
        r = subprocess.run(
            args, cwd=cwd, env=env, capture_output=True, text=True, timeout=timeout
        )
        if r.returncode:
            log = work / f"command-failure-{uuid.uuid4().hex}.log"
            log.write_text(r.stdout + r.stderr)
            log.chmod(0o600)
            pytest.fail("Isolated command failed; private log: " + str(log))
        return r.stdout.strip()

    image_ids = {}
    for kind, ref in [
        ("old", "f8b00e6"),
        ("new", "607b1de9ffe120130655f4584b68672aa6ceef27"),
    ]:
        context = work / kind
        context.mkdir()
        archive = subprocess.check_output(["git", "archive", ref, "backend"], cwd=ROOT)
        subprocess.run(["tar", "-x", "-C", str(context)], input=archive, check=True)
        (context / "Dockerfile").write_text(
            'FROM sales-updater29-test-deps:local\nWORKDIR /app\nCOPY backend/ /app/\nCMD ["python","-B","start_backend.py"]\n'
        )
        tag = "sales-updater29-local-" + kind + ":" + uuid.uuid4().hex[:10]
        command([*DOCKER, "build", "-t", tag, str(context)], timeout=180)
        image_ids[kind] = command([*DOCKER, "image", "inspect", "-f", "{{.Id}}", tag])
    fronts = {}
    for kind in ["old", "new"]:
        context = work / ("front-" + kind)
        context.mkdir()
        (context / "Dockerfile").write_text(
            'FROM sales-updater29-test-deps:local\nRUN mkdir -p /srv/vr/sales && printf "synthetic '
            + kind
            + ' frontend" > /srv/vr/sales/index.html\nCMD ["python","-m","http.server","8000","--directory","/srv"]\n'
        )
        tag = "sales-updater29-front-" + kind + ":" + uuid.uuid4().hex[:10]
        command([*DOCKER, "build", "-t", tag, str(context)], timeout=120)
        fronts[kind] = command([*DOCKER, "image", "inspect", "-f", "{{.Id}}", tag])
    project = work / "project"
    command(["git", "clone", "--shared", "--no-checkout", str(ROOT), str(project)])
    command(
        ["git", "switch", "-c", "main", "607b1de9ffe120130655f4584b68672aa6ceef27"],
        cwd=project,
    )
    password = secrets.token_hex(18)
    envfile = project / ".env"
    envfile.write_text(
        "POSTGRES_PASSWORD="
        + password
        + "\nDATABASE_URL=postgresql+asyncpg://sales:"
        + password
        + "@db:5432/sales\n"
    )
    envfile.chmod(0o600)
    stub = work / "mount.fixture"
    stub.write_text("SYNTHETIC MOUNT\n")
    base = {
        "name": "updater29-" + uuid.uuid4().hex[:8],
        "services": {
            "db": {
                "image": "postgres:16-alpine",
                "container_name": "sales-journal-db",
                "environment": {
                    "POSTGRES_USER": "sales",
                    "POSTGRES_DB": "sales",
                    "POSTGRES_PASSWORD": "${POSTGRES_PASSWORD}",
                },
                "volumes": [
                    {
                        "type": "bind",
                        "source": str(work / "pgdata"),
                        "target": "/var/lib/postgresql/data",
                    }
                ],
            },
            "backend": {
                "image": image_ids["old"],
                "container_name": "sales-journal-backend",
                "environment": {
                    "DATABASE_URL": "${DATABASE_URL}",
                    "CLIENTS_VR_API_URL": "http://127.0.0.1:9",
                    "VRCATALOG_API_URL": "http://127.0.0.1:9",
                },
                "ports": ["127.0.0.1:8010:8000"],
                "volumes": [
                    {
                        "type": "bind",
                        "source": str(stub),
                        "target": "/run/mtls.fixture",
                        "read_only": True,
                    }
                ],
            },
            "frontend": {
                "image": fronts["old"],
                "container_name": "sales-journal-frontend",
                "ports": ["127.0.0.1:8011:8000"],
            },
        },
    }
    (project / "docker-compose.yml").write_text(yaml.safe_dump(base))
    (project / "docker-compose.override.yml").write_text(
        "services: {backend: {init: false}}\n"
    )
    (project / "docker-compose.churn-v3u.override.yml").write_text(
        'services: {backend: {environment: {ANALYTICS_SIGNALS_ENABLED: "false", ANALYTICS_SIGNALS_MODE: SHADOW, ANALYTICS_AUTOMATION_ENABLED: "false", CHURN: SHADOW}}}\n'
    )
    bindings = work / "bindings.json"
    bindings.write_text(
        json.dumps(
            {
                "services": {
                    "backend": {
                        "environment": {
                            "CALLTRACK_TEST_SECRET": "SYNTHETIC_SECRET_DO_NOT_PRINT"
                        },
                        "volumes": [
                            {
                                "type": "bind",
                                "source": str(stub),
                                "target": "/run/calltrack.fixture",
                                "read_only": True,
                            }
                        ],
                    }
                }
            }
        )
    )
    bindings.chmod(0o600)
    active = project / "docker-compose.report-fix.override.yml"
    active.write_text("services: {}\n")
    # Local fixture commit keeps main clean and keeps the required release ancestor.
    with (project / ".git/info/exclude").open("a") as f:
        f.write(
            "\ndocker-compose.report-fix.override.yml\ndocker-compose.schema25-recovery.override.yml\n.sales-update-recovery-required\n"
        )
    command(
        [
            "git",
            "add",
            "-f",
            "docker-compose.yml",
            "docker-compose.override.yml",
            "docker-compose.churn-v3u.override.yml",
        ],
        cwd=project,
    )
    command(
        [
            "git",
            "-c",
            "user.name=Fixture",
            "-c",
            "user.email=fixture@example.invalid",
            "commit",
            "-m",
            "isolated Compose configuration",
        ],
        cwd=project,
    )
    remote = work / "remote.git"
    command(["git", "clone", "--bare", str(project), str(remote)])
    command(["git", "remote", "set-url", "origin", str(remote)], cwd=project)
    chain = [
        *DOCKER,
        "compose",
        "--env-file",
        str(envfile),
        "-f",
        str(project / "docker-compose.yml"),
        "-f",
        str(project / "docker-compose.override.yml"),
        "-f",
        str(project / "docker-compose.churn-v3u.override.yml"),
        "-f",
        str(bindings),
        "-f",
        str(active),
    ]
    update = work / "update29.sh"
    update.write_bytes(gen.adapt(source26()))
    recover = work / "recover29.sh"
    recover.write_bytes(recovery.prepare(source26()))
    binpath = work / "bin"
    binpath.mkdir()
    (binpath / "docker").write_text(
        """#!/usr/bin/env python3
import os,subprocess,sys
from pathlib import Path
args=sys.argv[1:];mode=os.environ.get('FIXTURE_INJECT','');once=Path(os.environ['FIXTURE_ONCE'])
rc=subprocess.call([os.environ['FIXTURE_DOCKER'],'--host=unix:///var/run/docker.sock',*args])
match=mode=='committed-migration' and '-c' in args and args[args.index('-c')+2:args.index('-c')+3]==['migrate']
match=match or (mode=='frontend-up' and 'compose' in args and 'up' in args and args[-1]=='frontend')
if rc==0 and match and not once.exists():once.write_text('one transport failure');sys.exit(73)
sys.exit(rc)
"""
    )
    (binpath / "docker").chmod(0o755)
    env = dict(
        os.environ,
        FIXTURE_DOCKER=shutil.which("docker"),
        PATH=str(binpath) + os.pathsep + os.environ["PATH"],
        SALES_PROJECT_DIR=str(project),
        SALES_CALLTRACK_COMPOSE=str(bindings),
        SALES_UPDATE_LOCK_FILE=str(work / "flock"),
        SALES_UPDATE_BACKUP_ROOT=str(work / "backups"),
        SALES_UPDATE_BACKEND_IMAGE=image_ids["new"],
        SALES_UPDATE_FRONTEND_IMAGE=fronts["new"],
        SALES_UPDATE_HEALTH_DELAY="1",
        SALES_UPDATE_HEALTH_ATTEMPTS="30",
    )

    def run(script, args=(), inject=""):
        r = subprocess.run(
            ["bash", str(script), *args],
            env=dict(
                env,
                FIXTURE_INJECT=inject,
                FIXTURE_ONCE=str(work / ("inject-" + uuid.uuid4().hex)),
            ),
            capture_output=True,
            text=True,
            timeout=180,
        )
        log = work / ("run-" + uuid.uuid4().hex + ".log")
        log.write_text(r.stdout + r.stderr)
        log.chmod(0o600)
        assert "SYNTHETIC_SECRET_DO_NOT_PRINT" not in r.stdout + r.stderr
        return r, log

    def sql(query):
        return command(
            [
                *DOCKER,
                "exec",
                "sales-journal-db",
                "psql",
                "-X",
                "-A",
                "-t",
                "-U",
                "sales",
                "-d",
                "sales",
                "-c",
                query,
            ]
        )

    def runtime(service):
        return json.loads(command([*DOCKER, "inspect", "sales-journal-" + service]))[0]

    try:
        command([*chain, "up", "-d", "--no-deps", "db"])
        for _ in range(60):
            if (
                subprocess.run(
                    [
                        *DOCKER,
                        "exec",
                        "sales-journal-db",
                        "pg_isready",
                        "-U",
                        "sales",
                        "-d",
                        "sales",
                    ],
                    capture_output=True,
                ).returncode
                == 0
            ):
                break
            time.sleep(0.2)
        # Prepare only the empty local database through28. Never use operator DB.
        prepare = "from alembic.config import Config;from alembic import command;import sqlalchemy as sa;from app.config import settings;c=Config('alembic.ini');command.upgrade(c,'20261111_27');e=sa.create_engine(settings.database_url.replace('+asyncpg','+psycopg'));x=e.connect();x.execute(sa.text('CREATE INDEX ix_sales_department_date_control ON sales(department,sale_date)'));x.commit();x.close();command.upgrade(c,'20261112_28')"
        temp = work / "new.yml"
        temp.write_text(
            yaml.safe_dump({"services": {"backend": {"image": image_ids["new"]}}})
        )
        command(
            [
                *chain,
                "-f",
                str(temp),
                "run",
                "--rm",
                "-T",
                "--no-deps",
                "--pull",
                "never",
                "--entrypoint",
                "python",
                "backend",
                "-B",
                "-c",
                prepare,
            ]
        )
        command([*chain, "up", "-d", "--no-deps", "backend", "frontend"])
        import urllib.request

        for _ in range(60):
            try:
                with urllib.request.urlopen(
                    "http://127.0.0.1:8010/api/health", timeout=1
                ) as response:
                    assert response.status == 200
                break
            except Exception:
                time.sleep(0.2)
        # Commit29 but simulate lost CLI status: must prove old CODE + bridge29.
        r, log = run(update, ["--migrate-28-to-29"], "committed-migration")
        assert r.returncode != 0, str(log)
        assert sql("SELECT version_num FROM alembic_version") == gen.TARGET
        back = runtime("backend")
        assert back["Image"] == image_ids["old"]
        assert back["Config"]["Cmd"] == [
            "--rollback",
            "--expected-revision",
            gen.TARGET,
        ]
        mount = next(
            m
            for m in back["Mounts"]
            if m["Destination"] == "/run/sales-schema25-recovery/start_backend.py"
        )
        assert mount["RW"] is False
        pid = command(
            [
                *DOCKER,
                "exec",
                "sales-journal-backend",
                "python",
                "-c",
                "from pathlib import Path;print(Path('/proc/1/cmdline').read_bytes().replace(b'\\0',b' ').decode())",
            ]
        )
        assert "--lifespan off" in pid
        marker = project / ".sales-update-recovery-required"
        checkpoint = Path(marker.read_text().splitlines()[1][7:])
        digest = (checkpoint / "protected-before.digest").read_text().strip()
        args = [
            "--checkpoint",
            str(checkpoint),
            "--protected-sha256",
            digest,
            "--old-backend-id",
            image_ids["old"],
            "--old-frontend-id",
            fronts["old"],
        ]
        # Recovery frontend failure commits normal backend, then retry only frontend.
        r, log = run(recover, args, "frontend-up")
        assert r.returncode != 0, str(log)
        assert (
            runtime("backend")["Image"] == image_ids["new"]
            and runtime("frontend")["Image"] == fronts["old"]
            and marker.exists()
        )
        backend_container = runtime("backend")["Id"]
        r, log = run(recover, args)
        assert r.returncode == 0, str(log)
        assert runtime("backend")["Id"] == backend_container and not marker.exists()
        assert runtime("frontend")["Image"] == fronts["new"]
        # An ordinary already29 update performs no DDL and keeps snapshots empty.
        r, log = run(update)
        assert r.returncode == 0, str(log)
        command([*chain, "restart", "backend"])
        time.sleep(3)
        assert sql("SELECT count(*) FROM category_season_snapshots") == "0"
        assert (
            sql(
                "SELECT active_id IS NULL AND next_due IS NULL FROM category_season_state WHERE id=1"
            )
            == "t"
        )
        proof = dict(
            previous_code="f8b00e6",
            candidate_code="607b1de9ffe120130655f4584b68672aa6ceef27",
            previous_image=image_ids["old"],
            candidate_image=image_ids["new"],
            actual_production_image_available=False,
            revision=sql("SELECT version_num FROM alembic_version"),
            bridge_readonly=True,
            bridge_lifespan_off=True,
            frontend_rollback_independent=True,
            recovery_resumed_without_backend_restart=True,
            published_snapshots=0,
        )
        (ROOT / "docs/benchmarks/updater29_compose.json").write_text(
            json.dumps(proof, indent=2)
        )
    finally:
        # Preserve containers, images, bind data/checkpoints for inspection.
        subprocess.run([*chain, "stop"], capture_output=True, timeout=60)
