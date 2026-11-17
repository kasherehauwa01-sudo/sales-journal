#!/usr/bin/env bash
# ONLY new isolated Sales29Lab; no production Compose, existing DB or image builds.
set -euo pipefail
set +x
umask 077
CANDIDATE=33ed69cb0b445d7daf580c6192c05e433eff2d7d
: "${LAB_BASE:?Set a large writable Sales29Lab disk directory}"
PG_IMAGE=${PG_IMAGE:-postgres:16-alpine}
PY_IMAGE=${PY_IMAGE:-sales-updater29-test-deps:local}
ROOT=$(git rev-parse --show-toplevel)
cd "$ROOT"
test -f backend/benchmarks/benchmark_snapshot_fill.py
# This test must NEVER silently exercise different production code.
git diff --quiet "$CANDIDATE" -- backend/app backend/alembic backend/start_backend.py
for image in "$PG_IMAGE" "$PY_IMAGE"; do docker image inspect "$image" --format '{{.Id}} {{.Os}}/{{.Architecture}}'; done
test "$(docker info --format '{{.CgroupVersion}}')" = 2 || { echo 'STOP: cgroup v2 is required for this measurement'; exit 1; }
DOCKER_ROOT=$(docker info --format '{{.DockerRootDir}}')
test -d "$DOCKER_ROOT" || { echo 'STOP: Docker VM storage must be checked in the daemon VM'; exit 1; }
for path in "$DOCKER_ROOT" "$LAB_BASE"; do
    df -Pk "$path"
    test "$(df -Pk "$path" | awk 'NR==2 {print $4}')" -ge 16777216 || { echo 'STOP: lab admission requires 16GiB free; nothing will be deleted'; exit 1; }
done
awk '/MemAvailable:/{print}' /proc/meminfo
# These are LAB caps, not a change to FirstVDS; inspect actual available host RAM.
PG_MEMORY=${PG_MEMORY:-1024m}
BACKEND_MEMORY=${BACKEND_MEMORY:-768m}
LAB_ROOT=$(mktemp -d "$LAB_BASE/sales29-full-fill.XXXXXXXX")
NAME="category-fill-lab-${LAB_ROOT##*.}"
PG_SEED="$NAME-seed"
PG="$NAME-measure"
JOB="$NAME-backend"
mkdir -p "$LAB_ROOT/pgdata" "$LAB_ROOT/out" "$LAB_ROOT/control" "$LAB_ROOT/code"
printf 'stop\n' > "$LAB_ROOT/control/state"
# Immutable runtime code, no checkout .env, no server-only mounts or Docker socket.
git archive "$CANDIDATE" backend | tar -x -C "$LAB_ROOT/code"
cp backend/benchmarks/benchmark_snapshot_fill.py "$LAB_ROOT/worker.py"
export LAB_ROOT PG_IMAGE PY_IMAGE PG_MEMORY BACKEND_MEMORY
python3 - <<'PY'
import hashlib,json,os,secrets,subprocess
from pathlib import Path
r=Path(os.environ['LAB_ROOT']);admin=secrets.token_hex(24);app=secrets.token_hex(24);db='category_fill_lab_'+secrets.token_hex(6)
(r/'postgres.private.env').write_text('POSTGRES_USER=lab_admin\nPOSTGRES_PASSWORD='+admin+'\nPOSTGRES_INITDB_ARGS=--encoding=UTF8\n')
(r/'app.private.env').write_text('DATABASE_URL=postgresql+asyncpg://sales_fill_app:'+app+'@127.0.0.1:5432/'+db+'\nPYTHONDONTWRITEBYTECODE=1\nPYTHONPATH=/app\n')
(r/'admin.private.env').write_text('LAB_ADMIN_URL=postgresql+psycopg://lab_admin:'+admin+'@127.0.0.1:5432/postgres\n')
images={name:subprocess.check_output(['docker','image','inspect','--format','{{.Id}} {{.Os}}/{{.Architecture}}',os.environ[name]],text=True).strip() for name in ('PG_IMAGE','PY_IMAGE')}
(r/'out'/'manifest.json').write_text(json.dumps(dict(images=images,pg_memory=os.environ['PG_MEMORY'],backend_memory=os.environ['BACKEND_MEMORY'],swap_over_ram_disabled=True,candidate='33ed69cb0b445d7daf580c6192c05e433eff2d7d',harness_commit=subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),worker_sha256=hashlib.sha256((r/'worker.py').read_bytes()).hexdigest()),indent=2))
PY
monitor_pid=''
cleanup() {
    printf 'stop\n' > "$LAB_ROOT/control/state"
    if [ -n "$monitor_pid" ]; then wait "$monitor_pid" || true; fi
    # Only this invocation's uniquely named services. Keep data, images and outputs.
    for own in "$JOB" "$PG" "$PG_SEED"; do docker stop -t 15 "$own" >/dev/null 2>&1 || true; done
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
start_pg() {
    docker run -d --pull never --name "$1" --network none --memory "$PG_MEMORY" --memory-swap "$PG_MEMORY" \
        --env-file "$LAB_ROOT/postgres.private.env" \
        -v "$LAB_ROOT/pgdata:/var/lib/postgresql/data" \
        -v "$LAB_ROOT/control:/lab-control:ro" "$PG_IMAGE"
    for attempt in $(seq 1 60); do
        if docker exec "$1" pg_isready -U lab_admin -d postgres >/dev/null 2>&1; then break; fi
        sleep 1
    done
    docker exec "$1" pg_isready -U lab_admin -d postgres
    test "$(docker exec "$1" psql -U lab_admin -d postgres -Atc 'SHOW server_version_num')" = 160016 || { echo 'STOP: this experiment requires PostgreSQL16.16'; exit 1; }
    docker exec "$1" psql -U lab_admin -d postgres -Atc 'SELECT datctype,datcollate FROM pg_database WHERE datname=current_database()'
}
run_job() {
    phase=$1; output=$2; shift 2
    docker run --rm --pull never --name "$JOB" --network "container:$ACTIVE_PG" \
        --memory "$BACKEND_MEMORY" --memory-swap "$BACKEND_MEMORY" --user 0:0 --read-only --tmpfs /tmp:rw,size=128m \
        --env HOME=/tmp --env LAB_RESULT_UID="$(id -u)" --env LAB_RESULT_GID="$(id -g)" \
        --env-file "$LAB_ROOT/app.private.env" "$@" \
        -v "$LAB_ROOT/code/backend:/app:ro" -v "$LAB_ROOT/worker.py:/lab/worker.py:ro" \
        -v "$LAB_ROOT/pgdata:/pgdata:ro" -v "$LAB_ROOT/out:/out:rw" -w /app \
        "$PY_IMAGE" python /lab/worker.py "$phase" --confirm-synthetic-lab --output "/out/$output"
}
start_pg "$PG_SEED"
ACTIVE_PG=$PG_SEED
run_job bootstrap bootstrap.json --env-file "$LAB_ROOT/admin.private.env" > "$LAB_ROOT/out/bootstrap.private.log" 2>&1
run_job seed seed.json > "$LAB_ROOT/out/seed.private.log" 2>&1
# New cgroup removes seeding RAM high-water from PG's measured lifetime.
docker stop -t 30 "$PG_SEED" >/dev/null
start_pg "$PG"
ACTIVE_PG=$PG
printf 'running\n' > "$LAB_ROOT/control/state"
# One persistent, low-cost observer in this PG's cgroup. No grants/sockets in backend.
docker exec "$PG" sh -c 'set -e; while [ "$(cat /lab-control/state)" = running ]; do read up rest < /proc/uptime; read used < /sys/fs/cgroup/memory.current; read peak < /sys/fs/cgroup/memory.peak; printf "%s %s %s\n" "$up" "$used" "$peak"; sleep 0.25; done' > "$LAB_ROOT/out/pg-memory.tsv" &
monitor_pid=$!
set +e
run_job fill first-fill.json > "$LAB_ROOT/out/first-fill.private.log" 2>&1
first_rc=$?
set -e
printf 'First fill exit=%s; outputs=%s\n' "$first_rc" "$LAB_ROOT/out"
if [ "$first_rc" != 0 ]; then
    printf 'stop\n' > "$LAB_ROOT/control/state"
    wait "$monitor_pid" || true
    monitor_pid=''
    python3 tools/summarize_snapshot_fill_lab.py "$LAB_ROOT/out" || true
    exit "$first_rc"
fi
# Required to measure the same build while retaining an actual full active snapshot.
set +e
run_job fill retained-fill.json > "$LAB_ROOT/out/retained-fill.private.log" 2>&1
second_rc=$?
set -e
printf 'stop\n' > "$LAB_ROOT/control/state"
wait "$monitor_pid"
monitor_pid=''
printf 'Retained fill exit=%s; outputs=%s\n' "$second_rc" "$LAB_ROOT/out"
python3 tools/summarize_snapshot_fill_lab.py "$LAB_ROOT/out"
exit "$second_rc"
