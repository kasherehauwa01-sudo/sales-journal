# Dedicated schema27 recovery entrypoint. Never source, never call normal main().
# Uses only read-only schema jobs. No migrate/downgrade/stamp path is called.
RECOVERY_CHECKPOINT=''
EXPECTED_PROTECTED=''
EXPECTED_OLD_BACKEND=''
EXPECTED_OLD_FRONTEND=''
RECOVERY_ORIGINAL_RUN=''
RECOVERY_PHASE=preflight
RECOVERY_TOUCHED=0
RECOVERY_STATE=''

atomic_copy() {
    local temporary
    temporary=$(mktemp "${2}.pending.XXXXXXXX") || return 1
    cp -p -- "$1" "$temporary" || return 1
    python3 - "$temporary" <<'PY_SYNC' || return 1
import os,sys
with open(sys.argv[1],'rb') as f:os.fsync(f.fileno())
PY_SYNC
    mv -f -- "$temporary" "$2" || return 1
    python3 - "$2" <<'PY_SYNC_DIR'
import os,sys
from pathlib import Path
d=os.open(Path(sys.argv[1]).parent,os.O_RDONLY|os.O_DIRECTORY)
try:os.fsync(d)
finally:os.close(d)
PY_SYNC_DIR
}

recovery_state_write() {
    python3 - "$RECOVERY_STATE" "$BACKUP_DIR" "$1" "$EXPECTED_PROTECTED" \
        "$EXPECTED_OLD_BACKEND" "$EXPECTED_OLD_FRONTEND" "$PREPARED_BACKEND_ID" "$PREPARED_FRONTEND_ID" <<'PY_STATE'
import hashlib,json,os,sys,tempfile
from pathlib import Path
path,backup,phase,digest,ob,of,nb,nf=sys.argv[1:]
backup=Path(backup)
files=['recovery.original.yml','normal.original.yml','marker.original','bridge-launcher.py','bridge-initial.json']
if (backup/'neutral-recovery.yml').exists():files.append('neutral-recovery.yml')
for name in files:
    with (backup/name).open('rb') as source:os.fsync(source.fileno())
d=os.open(backup,os.O_RDONLY|os.O_DIRECTORY)
try:os.fsync(d)
finally:os.close(d)
state=dict(backup=str(backup),phase=phase,protected=digest,old_backend=ob,old_frontend=of,new_backend=nb,new_frontend=nf,
           files={n:hashlib.sha256((backup/n).read_bytes()).hexdigest() for n in files})
fd,tmp=tempfile.mkstemp(prefix='.exit27-state.',dir=Path(path).parent)
with os.fdopen(fd,'w') as out:
    json.dump(state,out);out.flush();os.fsync(out.fileno())
os.replace(tmp,path)
d=os.open(Path(path).parent,os.O_RDONLY|os.O_DIRECTORY)
try:os.fsync(d)
finally:os.close(d)
PY_STATE
}

normal_runtime() {
    # Persist only the launch contract and three non-secret automation flags.
    compose config --format json 2>"$BACKUP_DIR/normal-config.log" |
        python3 -c 'import json,sys; b=json.load(sys.stdin)["services"]["backend"]; e=b.get("environment",{}); print(json.dumps({"command":b.get("command") or ["python","-B","start_backend.py"],"flags":{k:e.get(k) for k in ("ANALYTICS_SIGNALS_ENABLED","ANALYTICS_SIGNALS_MODE","ANALYTICS_AUTOMATION_ENABLED")}}))' \
        >"$BACKUP_DIR/normal-launch.json" || return 1
    docker inspect sales-journal-backend 2>"$BACKUP_DIR/normal-inspect.log" |
        python3 -c 'import json,sys; d=json.load(sys.stdin)[0]; e=json.load(open(sys.argv[1])); c=d["Config"]; env=dict(v.split("=",1) for v in c.get("Env",[]) if "=" in v); ok=c.get("Entrypoint") in (None,[]) and c.get("Cmd")==e["command"] and all(v is None or env.get(k)==v for k,v in e["flags"].items()) and not any(m.get("Destination")=="/run/sales-schema25-recovery/start_backend.py" for m in d.get("Mounts",[])); sys.exit(0 if ok else 1)' \
        "$BACKUP_DIR/normal-launch.json" 2>"$BACKUP_DIR/normal-runtime.log" || return 1
    # Reviewed launcher execs uvicorn as PID1. A healthy API alone could be bridge.
    logged normal-process docker exec sales-journal-backend python -B -c \
        'from pathlib import Path; import sys; a=Path("/proc/1/cmdline").read_bytes().split(b"\0"); ok=b"app.main:app" in a and any(b"uvicorn" in v for v in a) and b"--lifespan" not in a and b"--rollback" not in a; sys.exit(0 if ok else 1)'
}

recovery_protected() {
    protected_snapshot "$1" "$BACKUP_DIR/backend.candidate.yml" safety "$TARGET_REVISION" || return 1
    [[ "$(cat "$BACKUP_DIR/$1.digest")" == "$EXPECTED_PROTECTED" ]]
}

recovery_restore_bridge() {
    ensure_rollback_guard || return 1
    atomic_copy "$BACKUP_DIR/normal.original.yml" "$OVERRIDE" || return 1
    atomic_copy "$BACKUP_DIR/recovery.original.yml" "$RECOVERY_FILE" || return 1
    ACTIVE_RECOVERY=1
    validate_config "$OVERRIDE" "$BACKUP_DIR/bridge-restored.json" || return 1
    cmp -s "$BACKUP_DIR/bridge-initial.json" "$BACKUP_DIR/bridge-restored.json" || return 1
    assert_guard || return 1
    logged recovery-bridge-up compose up -d --no-deps --no-build --pull never --force-recreate backend || return 1
    healthcheck backend "$EXPECTED_OLD_BACKEND" || return 1
    verify_alignment backend "$EXPECTED_OLD_BACKEND" || return 1
    verify_recovery_runtime || return 1
    assert_guard || return 1
    recovery_state_write bridge || return 1
    RECOVERY_PHASE=bridge
    say 'Восстановлен прежний bridge27, lifespan OFF. Marker и резервные данные сохранены.'
}

recovery_finish() {
    local status=$1 ok=1
    trap - ERR EXIT
    trap '' INT TERM PIPE
    set +e
    if ((status!=0)); then
        say "Выход из recovery остановлен (код $status)."
        if [[ "$RECOVERY_PHASE" == backend_committed ]]; then
            if ((YAML_DIRTY || SWITCH_STARTED)); then rollback_current || ok=0; fi
            # Never return a committed normal backend to bridge after frontend error.
            healthcheck backend "$PREPARED_BACKEND_ID" && verify_alignment backend "$PREPARED_BACKEND_ID" && normal_runtime || ok=0
            say 'Backend27 уже подтверждён; откат backend не выполняется.'
        elif ((RECOVERY_TOUCHED)); then
            recovery_restore_bridge || ok=0
        fi
        if ((!ok)); then say 'КРИТИЧЕСКАЯ: восстановление не подтверждено. Не удаляйте marker/overrides. Требуется аудит фактического runtime.'; fi
        say "Marker не удаляется. Закрытые контрольные точки: ${BACKUP_DIR:-не созданы}"
    fi
    release_guard
    exit "$status"
}

recovery_main() {
    [[ $# == 8 && "$1" == --checkpoint && "$3" == --protected-sha256 && "$5" == --old-backend-id && "$7" == --old-frontend-id ]] \
        || fail 'Нужны --checkpoint DIR --protected-sha256 SHA --old-backend-id sha256:ID --old-frontend-id sha256:ID. Новые immutable IDs задаются SALES_UPDATE_*_IMAGE.'
    RECOVERY_CHECKPOINT=$2 EXPECTED_PROTECTED=$4 EXPECTED_OLD_BACKEND=$6 EXPECTED_OLD_FRONTEND=$8
    [[ "$EXPECTED_PROTECTED" =~ ^[0-9a-f]{64}$ ]] || fail 'Неверный подтверждённый digest настроек.'
    local ref resolved tool file
    for ref in "$EXPECTED_OLD_BACKEND" "$EXPECTED_OLD_FRONTEND" "$PREBUILT_BACKEND" "$PREBUILT_FRONTEND"; do
        [[ "$ref" =~ ^sha256:[0-9a-f]{64}$ ]] || fail 'Требуются полные immutable image IDs, не теги.'
    done
    for tool in docker python3 flock curl df awk cp mv mktemp sha256sum cmp cat; do
        command -v "$tool" >/dev/null || fail "Отсутствует $tool"
    done
    if ! { [[ "$MIN_AVAILABLE_KB" =~ ^[1-9][0-9]*$ && "$MIN_FREE_KB" =~ ^[1-9][0-9]*$ && "$HEALTH_ATTEMPTS" =~ ^[1-9][0-9]*$ && "$HEALTH_DELAY" =~ ^[0-9]+$ && "$GUARD_TIMEOUT" =~ ^[1-9][0-9]*$ ]]; }; then fail 'Некорректные лимиты.'; fi
    if ! { ((MIN_AVAILABLE_KB>=262144 && MIN_FREE_KB>=2097152)); }; then fail 'Пороги RAM/диска нельзя снижать.'; fi
    exec {STATUS_FD}>&1
    exec 9>"$LOCK_FILE"
    flock -n 9 || fail 'Другое обновление уже выполняется.'
    trap 'recovery_finish "$?"' EXIT
    trap 'exit "$?"' ERR
    trap 'exit 130' INT
    trap 'exit 143' TERM
    trap 'exit 141' PIPE
    cd -- "$PROJECT_DIR"
    PROJECT_DIR=$(pwd -P)
    OVERRIDE="$PROJECT_DIR/docker-compose.report-fix.override.yml"
    RECOVERY_FILE="$PROJECT_DIR/docker-compose.schema25-recovery.override.yml"
    RECOVERY_BLOCK_FILE="$PROJECT_DIR/.sales-update-recovery-required"
    RECOVERY_STATE="$RECOVERY_CHECKPOINT/exit27-state.json"
    TARGET_REVISION=20261111_27
    # Validate trusted local artifacts before parsing them; never source marker/state.
    python3 - "$RECOVERY_CHECKPOINT" "$RECOVERY_BLOCK_FILE" "$OVERRIDE" "$CALLTRACK_FILE" "$PROJECT_DIR" <<'PY_TRUST' || fail 'Недоверенные checkpoint/marker/config или несовпадение marker backup.'
import os,stat,sys
from pathlib import Path
checkpoint,marker,override,bindings,project=map(Path,sys.argv[1:])
def trusted(path,directory=False):
    st=path.lstat()
    if stat.S_ISLNK(st.st_mode) or st.st_uid!=os.geteuid() or st.st_mode&0o022:raise ValueError('unsafe owner/mode')
    if not (stat.S_ISDIR(st.st_mode) if directory else stat.S_ISREG(st.st_mode)):raise ValueError('unsafe type')
    if not directory and st.st_size>2097152:raise ValueError('oversized artifact')
for d in (checkpoint,project):trusted(d,True)
recovery=project/'docker-compose.schema25-recovery.override.yml'
if recovery.exists() or recovery.is_symlink():trusted(recovery)
for f in (marker,override,bindings,checkpoint/'schema25-recovery.yml',checkpoint/'rollback-start_backend.py',checkpoint/'backend.checkpoint.json',checkpoint/'protected-before.log',project/'.env',project/'docker-compose.yml',project/'docker-compose.override.yml',project/'docker-compose.churn-v3u.override.yml'):trusted(f)
lines=marker.read_text().splitlines()
if len(lines)!=2 or not lines[0].startswith('run_id=') or not lines[1].startswith('backup='):raise ValueError('marker shape')
if Path(lines[1][7:]).resolve()!=checkpoint.resolve() or not lines[0][7:]:raise ValueError('marker checkpoint')
PY_TRUST
    # Existing images are never removed, pulled, or rebuilt.
    for ref in "$EXPECTED_OLD_BACKEND" "$EXPECTED_OLD_FRONTEND" "$PREBUILT_BACKEND" "$PREBUILT_FRONTEND"; do
        resolved=$(docker image inspect -f '{{.Id}}' "$ref" 2>/dev/null) || fail 'Закреплённый образ отсутствует локально.'
        [[ "$resolved" == "$ref" ]] || fail 'Immutable image ID не подтверждён.'
    done
    PREPARED_BACKEND_ID=$PREBUILT_BACKEND PREPARED_FRONTEND_ID=$PREBUILT_FRONTEND
    BASE_COMPOSE=(docker compose --env-file "$PROJECT_DIR/.env"
        -f "$PROJECT_DIR/docker-compose.yml" -f "$PROJECT_DIR/docker-compose.override.yml"
        -f "$PROJECT_DIR/docker-compose.churn-v3u.override.yml" -f "$CALLTRACK_FILE")
    if [[ -e "$RECOVERY_STATE" || -L "$RECOVERY_STATE" ]]; then
        BACKUP_DIR=$(python3 - "$RECOVERY_STATE" "$EXPECTED_PROTECTED" "$EXPECTED_OLD_BACKEND" "$EXPECTED_OLD_FRONTEND" "$PREPARED_BACKEND_ID" "$PREPARED_FRONTEND_ID" <<'PY_RESUME'
import hashlib,json,os,stat,sys
from pathlib import Path
p=Path(sys.argv[1]);st=p.lstat()
if not stat.S_ISREG(st.st_mode) or st.st_uid!=os.geteuid() or st.st_mode&0o077 or st.st_size>16384:raise SystemExit(1)
s=json.loads(p.read_text())
if [s[k] for k in ('protected','old_backend','old_frontend','new_backend','new_frontend')]!=sys.argv[2:]:raise SystemExit(1)
if s['phase'] not in ('bridge','backend_committed'):raise SystemExit('Interrupted switching requires manual runtime audit; no force retry')
b=Path(s['backup']);st=b.lstat()
if not stat.S_ISDIR(st.st_mode) or st.st_uid!=os.geteuid() or st.st_mode&0o077:raise SystemExit(1)
for n,h in s['files'].items():
    p=b/n;st=p.lstat()
    if not stat.S_ISREG(st.st_mode) or st.st_uid!=os.geteuid() or hashlib.sha256(p.read_bytes()).hexdigest()!=h:raise SystemExit(1)
print(b)
PY_RESUME
        ) || fail 'Нельзя безопасно продолжить recovery: state/артефакты требуют проверки.'
        RECOVERY_PHASE=$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1]))["phase"])' "$RECOVERY_STATE")
        cmp -s "$RECOVERY_BLOCK_FILE" "$BACKUP_DIR/marker.original" || fail 'Marker изменён.'
        # Each attempt gets fresh exclusive staging paths and release tags. Keep
        # every prior attempt intact, including failed frontend candidates/logs.
        local previous_backup=$BACKUP_DIR retry_backup
        retry_backup=$(mktemp -d "$BACKUP_ROOT/exit27.XXXXXXXX")
        for file in recovery.original.yml normal.original.yml marker.original bridge-launcher.py bridge-initial.json neutral-recovery.yml; do
            if [[ -f "$previous_backup/$file" ]]; then cp -p "$previous_backup/$file" "$retry_backup/$file"; fi
        done
        BACKUP_DIR=$retry_backup
    else
        if ! { [[ -f "$RECOVERY_FILE" && ! -L "$RECOVERY_FILE" ]]; }; then fail 'Нет ожидаемого recovery override.'; fi
        mkdir -p -- "$BACKUP_ROOT"
        BACKUP_DIR=$(mktemp -d "$BACKUP_ROOT/exit27.XXXXXXXX")
        cp -p "$OVERRIDE" "$BACKUP_DIR/normal.original.yml"
        cp -p "$RECOVERY_FILE" "$BACKUP_DIR/recovery.original.yml"
        cp -p "$RECOVERY_BLOCK_FILE" "$BACKUP_DIR/marker.original"
        cp -p "$RECOVERY_CHECKPOINT/rollback-start_backend.py" "$BACKUP_DIR/bridge-launcher.py"
        RECOVERY_PHASE=bridge
    fi
    RUN_ID="exit27-${BACKUP_DIR##*.}"
    ROLLBACK_LAUNCHER="$RECOVERY_CHECKPOINT/rollback-start_backend.py"
    ROLLBACK_LAUNCHER_SHA=$(sha256sum "$ROLLBACK_LAUNCHER"); ROLLBACK_LAUNCHER_SHA=${ROLLBACK_LAUNCHER_SHA%% *}
    [[ "$ROLLBACK_LAUNCHER_SHA" == 62934e47d2377a91238f073cc663d7dc2ea14ec9b404e14993c202548cf411bc ]] || fail 'Bridge launcher27 отличается.'
    printf 'services:\n  backend:\n    build: !reset null\n' >"$BACKUP_DIR/run-prebuilt-only.yml"
    check_disk; check_memory
    GUARD_APPNAME="sales-journal-exit27-$(python3 -c 'import secrets;print(secrets.token_hex(12))')"
    start_seasonality_guard || fail 'Не получены все шесть блокировок.'
    guard_query "SELECT CASE WHEN (SELECT array_agg(version_num::text ORDER BY version_num) FROM alembic_version)=ARRAY['20261111_27'] THEN 'SCHEMA27' ELSE 'WRONG' END;" SCHEMA27 || fail 'Требуется уже committed schema27. Миграция не запускается.'
    # Never touch a surviving migration container, even one from another updater.
    local jobs
    jobs=$(docker ps --filter label=sales.schema25.job --format '{{.Names}}' 2>"$BACKUP_DIR/jobs.log") || fail 'Не проверены migration jobs.'
    [[ -z "$jobs" ]] || fail 'Обнаружен активный migration job; сначала отдельный аудит.'
    if [[ "$RECOVERY_PHASE" == bridge ]]; then
        ACTIVE_RECOVERY=1
        cmp -s "$RECOVERY_FILE" "$RECOVERY_CHECKPOINT/schema25-recovery.yml" || fail 'Recovery override отличается от проверенного checkpoint.'
        validate_config "$OVERRIDE" "$BACKUP_DIR/bridge-initial.json" || fail 'Recovery Compose некорректен.'
        python3 - "$RECOVERY_CHECKPOINT/backend.checkpoint.json" "$BACKUP_DIR/bridge-initial.json" <<'PY_BRIDGE' || fail 'Recovery меняет настройки кроме разрешённого maintenance delta.'
import json,sys
b,a=(json.load(open(p)) for p in sys.argv[1:])
if b['recovery_invariants_sha256']!=a['recovery_invariants_sha256'] or not all(a['recovery_contract'].values()):raise SystemExit(1)
PY_BRIDGE
        if ! { healthcheck backend "$EXPECTED_OLD_BACKEND" && verify_alignment backend "$EXPECTED_OLD_BACKEND" && verify_recovery_runtime; }; then fail 'Исходный bridge runtime не подтверждён.'; fi
        if ! { healthcheck frontend "$EXPECTED_OLD_FRONTEND" && verify_alignment frontend "$EXPECTED_OLD_FRONTEND"; }; then fail 'Исходный frontend не подтверждён.'; fi
    else
        # Prior frontend failure: backend is already committed. No backend up/stop.
        [[ ! -e "$RECOVERY_FILE" ]] || cmp -s "$RECOVERY_FILE" "$BACKUP_DIR/neutral-recovery.yml" || fail 'Ожидался нейтральный recovery override.'
        ACTIVE_RECOVERY=0
        if ! { healthcheck backend "$PREPARED_BACKEND_ID" && verify_alignment backend "$PREPARED_BACKEND_ID" && normal_runtime; }; then fail 'Подтверждённый backend27 изменился.'; fi
    fi
    # Candidate uses the exact normal chain, with ONLY its backend image changed.
    ACTIVE_RECOVERY=0
    validate_config "$OVERRIDE" "$BACKUP_DIR/normal-before.json" || fail 'Нормальная Compose-цепочка некорректна.'
    python3 - "$RECOVERY_CHECKPOINT/backend.checkpoint.json" "$BACKUP_DIR/normal-before.json" <<'PY_NORMAL_BASE' || fail 'Normal настройки или сторонние service images изменились относительно checkpoint.'
import json,sys
b,a=(json.load(open(p)) for p in sys.argv[1:])
if b['invariants_sha256']!=a['invariants_sha256']:raise SystemExit(1)
if {k:v for k,v in b['services'].items() if k not in ('backend','frontend')}!={k:v for k,v in a['services'].items() if k not in ('backend','frontend')}:raise SystemExit(1)
PY_NORMAL_BASE
    local actual_frontend
    actual_frontend=$(container_id frontend) || fail 'Frontend runtime отсутствует.'
    [[ "$actual_frontend" == "$EXPECTED_OLD_FRONTEND" || "$actual_frontend" == "$PREPARED_FRONTEND_ID" ]] || fail 'Неизвестный frontend image при продолжении.'
    local candidate="$BACKUP_DIR/backend.candidate.yml"
    [[ ! -e "$candidate" ]] || candidate="$BACKUP_DIR/backend.retry-$GUARD_APPNAME.yml"
    patch_image "$OVERRIDE" "$candidate" backend "$PREPARED_BACKEND_ID" >"$BACKUP_DIR/patch.log" 2>&1 || fail 'Не подготовлен candidate.'
    if ! { validate_config "$candidate" "$BACKUP_DIR/candidate.json" && same_settings "$BACKUP_DIR/normal-before.json" "$BACKUP_DIR/candidate.json" backend; }; then fail 'Изменились параметры normal Compose.'; fi
    # schema helpers refer to this fixed candidate path; reuse only a verified retry.
    if [[ "$candidate" != "$BACKUP_DIR/backend.candidate.yml" ]]; then atomic_copy "$candidate" "$BACKUP_DIR/backend.candidate.yml" || fail 'Candidate не сохранён.'; fi
    NEW_IMAGE_ID=$PREPARED_BACKEND_ID
    # Do not overwrite the earlier extraction on resume.
    if [[ -e "$BACKUP_DIR/rollback-start_backend.py" ]]; then
        [[ "$(sha256sum "$BACKUP_DIR/rollback-start_backend.py")" == "$ROLLBACK_LAUNCHER_SHA  $BACKUP_DIR/rollback-start_backend.py" ]] || fail 'Staged launcher изменён.'
    else
        prepare_candidate_launcher
    fi
    ROLLBACK_LAUNCHER="$RECOVERY_CHECKPOINT/rollback-start_backend.py"
    verify_image_startup "$PREPARED_BACKEND_ID" 1 || fail 'Candidate startup несовместим.'
    logged recovery-imports compose_file "$BACKUP_DIR/backend.candidate.yml" -f "$BACKUP_DIR/run-prebuilt-only.yml" run --rm -T --no-deps --pull never --entrypoint python backend -B -c '__IMPORT_PREFLIGHT__' "$TARGET_REVISION" || fail 'Imports/graph/locks candidate не подтверждены.'
    logged recovery-schema schema_job "$BACKUP_DIR/backend.candidate.yml" check "$TARGET_REVISION" /app/start_backend.py "$ROLLBACK_LAUNCHER_SHA" || fail 'Контракт таблиц/FK/CHECK schema27 не подтверждён.'
    verify_image_startup "$EXPECTED_OLD_BACKEND" 0 || fail 'Rollback image startup несовместим.'
    local bridge_check="$BACKUP_DIR/bridge-check-$GUARD_APPNAME.yml"
    patch_image "$BACKUP_DIR/backend.candidate.yml" "$bridge_check" backend "$EXPECTED_OLD_BACKEND" >"$BACKUP_DIR/bridge-patch.log" 2>&1 || fail 'Не подготовлен old-image check.'
    logged recovery-bridge-schema schema_job "$bridge_check" check "$TARGET_REVISION" /run/sales-schema25-recovery/start_backend.py "$ROLLBACK_LAUNCHER_SHA" || fail 'Bridge27 не подтверждён.'
    recovery_protected recovery-before || fail 'Защищённые настройки отличаются/не подтверждены или история running.'
    assert_guard || fail 'Guard потерян.'
    recovery_state_write "$RECOVERY_PHASE"
    if [[ "$RECOVERY_PHASE" == bridge ]]; then
        # Validate normal candidate against the old NORMAL checkpoint, too. This
        # prevents losing unrelated server-only settings hidden by the bridge.
        same_settings "$RECOVERY_CHECKPOINT/backend.checkpoint.json" "$BACKUP_DIR/candidate.json" backend || fail 'Нормальная цепочка изменилась относительно checkpoint.'
        recovery_state_write bridge
        recovery_state_write switching
        RECOVERY_TOUCHED=1
        atomic_copy "$BACKUP_DIR/backend.candidate.yml" "$OVERRIDE" || fail 'Не записан normal image override.'
        printf 'services: {}\n' >"$BACKUP_DIR/neutral-recovery.yml"
        atomic_copy "$BACKUP_DIR/neutral-recovery.yml" "$RECOVERY_FILE" || fail 'Не нейтрализован maintenance delta.'
        ACTIVE_RECOVERY=1
        validate_config "$OVERRIDE" "$BACKUP_DIR/normal-effective.json" || fail 'Новая effective Compose некорректна.'
        cmp -s "$BACKUP_DIR/candidate.json" "$BACKUP_DIR/normal-effective.json" || fail 'Нейтрализация override меняет effective настройки.'
        if ! { assert_guard && check_memory && check_disk; }; then fail 'Защита потеряна перед переключением.'; fi
        logged recovery-backend-up compose up -d --no-deps --no-build --pull never --force-recreate backend || fail 'Запуск backend27 не подтверждён.'
        if ! { healthcheck backend "$PREPARED_BACKEND_ID" && verify_alignment backend "$PREPARED_BACKEND_ID" && normal_runtime; }; then fail 'Нормальный runtime/health backend27 не подтверждён.'; fi
        if ! { recovery_protected recovery-activated && assert_guard; }; then fail 'Проверка после активации не пройдена.'; fi
        # Durable commit precedes frontend. Any uncertain window remains switching
        # and refuses blind retry; runtime is audited by an operator after SIGKILL.
        recovery_state_write backend_committed
        RECOVERY_PHASE=backend_committed RECOVERY_TOUCHED=0
    fi
    ACTIVE_RECOVERY=1
    [[ -f "$RECOVERY_FILE" ]] || ACTIVE_RECOVERY=0
    BACKEND_IMAGE_ID=$PREPARED_BACKEND_ID BACKEND_COMMITTED=1
    preflight_frontend
    # Existing frontend transaction retains the committed backend on any failure.
    update_service frontend
    if ! { healthcheck backend "$PREPARED_BACKEND_ID" && normal_runtime && recovery_protected recovery-final && assert_guard; }; then fail 'Итоговая проверка backend/settings не пройдена.'; fi
    [[ "$(backend_revision)" == "$TARGET_REVISION" ]] || fail 'Схема изменилась.'
    validate_config "$OVERRIDE" "$BACKUP_DIR/final-with-recovery.json" || fail 'Final Compose не подтверждён.'
    ACTIVE_RECOVERY=0
    validate_config "$OVERRIDE" "$BACKUP_DIR/final-without-recovery.json" || fail 'Compose без recovery не подтверждён.'
    cmp -s "$BACKUP_DIR/final-with-recovery.json" "$BACKUP_DIR/final-without-recovery.json" || fail 'Удаление recovery delta меняет effective Compose.'
    if [[ -e "$RECOVERY_FILE" ]]; then
        # Archive, never discard the guard file; same filesystem atomic rename.
        mv -- "$RECOVERY_FILE" "$RECOVERY_FILE.exit27-$RUN_ID.saved" || fail 'Не архивирован нейтральный override.'
    fi
    if ! { verify_alignment backend "$PREPARED_BACKEND_ID" && verify_alignment frontend "$PREPARED_FRONTEND_ID" && healthcheck frontend "$PREPARED_FRONTEND_ID" && assert_guard; }; then fail 'Окончательное согласование image/YAML не подтверждено.'; fi
    RECOVERY_ORIGINAL_RUN=$(python3 -c 'import sys;print(open(sys.argv[1]).readline().strip()[7:])' "$BACKUP_DIR/marker.original")
    cmp -s "$RECOVERY_BLOCK_FILE" "$BACKUP_DIR/marker.original" || fail 'Marker изменён перед commit.'
    RUN_ID=$RECOVERY_ORIGINAL_RUN
    clear_transition_marker || fail 'Обычный режим подтверждён, но marker не снят.'
    release_guard
    trap - ERR EXIT INT TERM PIPE
    say "УСПЕХ: schema27, backend27/frontend27, normal launcher/lifespan, protected settings и Compose подтверждены. Контрольные точки: $BACKUP_DIR"
    say 'Сохранённые настройки фоновых механизмов не изменены. Историческая сверка не запущена.'
}
recovery_main "$@"
