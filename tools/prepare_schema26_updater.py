"""Deterministically adapt the audited external updater; never execute it.

The full production script remains outside Git. A different input needs a new
review, not a fuzzy patch or a global replacement of revision identifiers.
"""

import argparse
import hashlib
from pathlib import Path

INPUT_SHA256 = "09673fad4822ea9287e7839738549e05d289e21f0444d439df5876901c47099d"


def adapt(source):
    if hashlib.sha256(source).hexdigest() != INPUT_SHA256:
        raise ValueError("Unreviewed updater bytes: input SHA256 differs")
    script = source.decode("utf-8")

    def replace(before, after, count=1):
        nonlocal script
        if script.count(before) != count:
            raise ValueError("Audited anchor is missing or ambiguous: " + before[:60])
        script = script.replace(before, after)

    def region(start, end, content):
        nonlocal script
        left = script.index(start)
        right = script.index(end, left)
        script = script[:left] + content.rstrip() + "\n" + script[right:]

    replace(
        "MIGRATION_ALLOWED=0\n",
        "MIGRATION_ALLOWED=0\nMIGRATION_STEP=''\nTARGET_REVISION=''\n",
    )
    replace(
        "[--migrate-24-to-25] [--allow-build]",
        "[--migrate-24-to-25|--migrate-25-to-26] [--allow-build]",
    )
    replace(
        "import copy,hashlib,json,sys\nd=json.load(sys.stdin)",
        """import copy,hashlib,json,sys
target=sys.argv[1] if len(sys.argv)>1 and sys.argv[1] else None
if target not in (None,'20261030_25','20261109_26'): raise SystemExit(1)
revisions=[target] if target else ['20261030_25','20261109_26']
d=json.load(sys.stdin)""",
    )
    replace(
        "for suffix in ([],['--expected-revision','20261030_25'])]",
        "for suffix in ([[]]+[['--expected-revision',r] for r in revisions])]",
    )
    replace(
        "b.get('command')==['--rollback','--expected-revision','20261030_25']",
        "b.get('command') in [['--rollback','--expected-revision',r] for r in revisions]",
    )
    replace(
        'python3 -c "$(config_metadata_python)" >',
        'python3 -c "$(config_metadata_python)" "$TARGET_REVISION" >',
    )
    replace(
        "action, expected, guard, launcher, checksum = sys.argv[1:]",
        "action, expected, guard, launcher, checksum, target = sys.argv[1:]",
    )
    replace(
        "if module.get('DEFAULT_REVISION') != '20261030_25':",
        "if target not in ('20261030_25','20261109_26') or module.get('DEFAULT_REVISION') != target:",
    )
    replace(
        "module['main'](['--migrate','--expected-revision','20261030_25'])\n            if context() != '20261030_25': raise RuntimeError('migration not confirmed')",
        "if expected != target: raise RuntimeError('migration target differs')\n            module['main'](['--migrate','--expected-revision',target])\n            if context() != target: raise RuntimeError('migration not confirmed')",
    )
    replace(
        '"$GUARD_APPNAME" "$launcher" "$checksum"',
        '"$GUARD_APPNAME" "$launcher" "$checksum" "$TARGET_REVISION"',
    )
    replace(
        'for s in ([],["--expected-revision","20261030_25"])',
        'for s in ([],["--expected-revision",sys.argv[3]])',
    )
    replace(
        '"$BACKUP_DIR/candidate.json" "$candidate" 2>',
        '"$BACKUP_DIR/candidate.json" "$candidate" "$TARGET_REVISION" 2>',
    )

    replace(
        "    assert_guard\n}\nassert_guard()",
        "    guard_query \"SELECT CASE WHEN pg_try_advisory_lock(734190271) THEN 'FTP_OK' ELSE 'BUSY' END;\" FTP_OK || return 1\n    guard_query \"SELECT CASE WHEN pg_try_advisory_lock(734190272) THEN 'MAIL_OK' ELSE 'BUSY' END;\" MAIL_OK || return 1\n    assert_guard\n}\nassert_guard()",
    )
    replace(
        "objid IN (734190221,734190220,734190117)",
        "objid IN (734190221,734190220,734190117,734190271,734190272)",
        count=2,
    )
    replace(")=3 THEN 'GUARD_OK'", ")=5 THEN 'GUARD_OK'")
    replace("734190272))=3", "734190272))=5")
    replace(
        "say 'Worker, source и catalog заблокированы одной PostgreSQL-сессией.'",
        "say 'Worker, source, catalog, FTP и SMTP заблокированы одной PostgreSQL-сессией.'",
    )
    replace("all three application locks", "all five application locks")
    replace(
        "SAME live three-lock PostgreSQL session",
        "SAME live five-lock PostgreSQL session",
    )
    replace(
        "Получение всех трёх блокировок для отката.",
        "Получение всех пяти блокировок для отката.",
    )
    replace(
        "compatible entrypoint/mount and prove future restarts also use launcher25.",
        "compatible entrypoint/mount and prove future restarts use the bridge launcher.",
    )
    replace(
        "Активен schema25 recovery override.",
        "Активен schema recovery override (историческое имя schema25).",
    )
    replace(
        'python3 - "$1" "$2" <<\'PY_SETTINGS\'',
        'python3 - "$1" "$2" "${3:-$CURRENT_SERVICE}" <<\'PY_SETTINGS\'',
    )
    replace(
        "    if json.load(a)['invariants_sha256'] != json.load(b)['invariants_sha256']:\n        raise SystemExit(1)",
        "    before,after=json.load(a),json.load(b)\n    if before['invariants_sha256'] != after['invariants_sha256']: raise SystemExit(1)\n    service=sys.argv[3]\n    if service not in ('backend','frontend'): raise SystemExit(1)\n    if {k:v for k,v in before['services'].items() if k!=service}!={k:v for k,v in after['services'].items() if k!=service}: raise SystemExit(1)",
    )
    replace(
        'same_settings "$BACKUP_DIR/frontend.preflight.json" "$BACKUP_DIR/frontend.candidate-preflight.json"',
        'same_settings "$BACKUP_DIR/frontend.preflight.json" "$BACKUP_DIR/frontend.candidate-preflight.json" frontend',
    )
    # Extract from the actual immutable image, not whichever checkout git pulled.
    # The later Compose schema job verifies the SAME bytes under effective mounts.
    extraction = r"""
prepare_candidate_launcher() {
    ROLLBACK_LAUNCHER="$BACKUP_DIR/rollback-start_backend.py"
    logged launcher-extract docker run --rm --pull never --network none --read-only \
        --cap-drop ALL --security-opt no-new-privileges --memory 128m --cpus 1 --pids-limit 32 \
        --entrypoint python "$NEW_IMAGE_ID" -B -c \
        'import base64; from pathlib import Path; data=Path("/app/start_backend.py").read_bytes(); assert 0<len(data)<=262144; print(base64.b64encode(data).decode("ascii"))' \
        || fail 'Не удалось прочитать launcher из immutable candidate image.'
    TARGET_REVISION=$(python3 - "$BACKUP_DIR/launcher-extract.log" "$ROLLBACK_LAUNCHER" 2>"$BACKUP_DIR/launcher-validation.log" <<'PY_LAUNCHER'
import ast,base64,os,sys
from pathlib import Path
encoded=Path(sys.argv[1]).read_bytes()
if len(encoded)>400000: raise SystemExit(1)
data=base64.b64decode(encoded.strip(),validate=True)
if not 0<len(data)<=262144: raise SystemExit(1)
tree=ast.parse(data)
values={}
for node in tree.body:
    if isinstance(node,ast.Assign) and len(node.targets)==1 and isinstance(node.targets[0],ast.Name):
        if node.targets[0].id in ('DEFAULT_REVISION','SUPPORTED_REVISIONS','MIGRATION_SOURCES'):
            values[node.targets[0].id]=ast.literal_eval(node.value)
target=values.get('DEFAULT_REVISION')
if target not in ('20261030_25','20261109_26') or target not in values.get('SUPPORTED_REVISIONS',()): raise SystemExit(1)
if target not in values.get('MIGRATION_SOURCES',{}): raise SystemExit(1)
with open(sys.argv[2],'xb') as output: output.write(data)
os.chmod(sys.argv[2],0o644)
print(target)
PY_LAUNCHER
    ) || fail 'Неизвестный или некорректный candidate launcher; переключение запрещено.'
    ROLLBACK_LAUNCHER_SHA=$(sha256sum "$ROLLBACK_LAUNCHER")
    ROLLBACK_LAUNCHER_SHA=${ROLLBACK_LAUNCHER_SHA%% *}
    printf 'target_revision=%s\nrollback_launcher_sha256=%s\n' "$TARGET_REVISION" "$ROLLBACK_LAUNCHER_SHA" >>"$BACKUP_DIR/release.txt"
    validate_config "$BACKUP_DIR/backend.candidate.yml" "$BACKUP_DIR/candidate.json" || fail 'Candidate Compose несовместим с целевой схемой.'
}
"""
    script = script.replace(
        "\nprepare_schema_rollback() {",
        "\n" + extraction + "\nprepare_schema_rollback() {",
        1,
    )
    left = script.index(
        '    ROLLBACK_LAUNCHER="$BACKUP_DIR/rollback-start_backend.py"',
        script.index("prepare_schema_rollback() {"),
    )
    right = script.index("    current=$(backend_revision)", left)
    script = script[:left] + script[right:]
    replace(
        '[[ "$current" == 20261022_24 || "$current" == 20261030_25 ]] || fail \'Разрешены только схемы24 и25.\'',
        '[[ "$current" == 20261022_24 || "$current" == 20261030_25 || "$current" == 20261109_26 ]] || fail \'Неизвестная фактическая схема PostgreSQL.\'',
    )
    replace(
        """    if [[ "$current" == 20261022_24 ]] && ((!MIGRATION_ALLOWED)); then
        fail 'База24: требуется отдельно согласованный запуск с --migrate-24-to-25. Автоматическая миграция запрещена.'
    fi""",
        '''    if [[ -n "$MIGRATION_STEP" && "${MIGRATION_STEP##*:}" != "$TARGET_REVISION" ]]; then
        fail 'Флаг миграции не соответствует целевой версии образа.'
    fi
    if [[ "$current" != "$TARGET_REVISION" ]]; then
        [[ "$MIGRATION_STEP" == "$current:$TARGET_REVISION" ]] || fail 'Нужен отдельный точный флаг миграции; downgrade и24→26 запрещены.'
    fi
    printf 'schema_before=%s\\n' "$SCHEMA_BEFORE" >>"$BACKUP_DIR/release.txt"''',
    )
    replace(
        '"$ROLLBACK_TAG" "$ROLLBACK_LAUNCHER" <<\'PY_RECOVERY\'',
        '"$ROLLBACK_TAG" "$ROLLBACK_LAUNCHER" "$TARGET_REVISION" <<\'PY_RECOVERY\'',
    )
    replace(
        "path,image,launcher=sys.argv[1:]", "path,image,launcher,target=sys.argv[1:]"
    )
    replace(
        "'command':['--rollback','--expected-revision','20261030_25'],",
        "'command':['--rollback','--expected-revision',target],",
    )
    replace(
        'c.get("Cmd")==["--rollback","--expected-revision","20261030_25"]',
        'c.get("Cmd")==["--rollback","--expected-revision",sys.argv[2]]',
    )
    replace(
        '\' "$ROLLBACK_LAUNCHER" \\\n        2>"$BACKUP_DIR/recovery-runtime-check.log"',
        '\' "$ROLLBACK_LAUNCHER" "$TARGET_REVISION" \\\n        2>"$BACKUP_DIR/recovery-runtime-check.log"',
    )
    replace("recover_schema25_backend()", "recover_schema_backend()")
    replace(
        "            recover_schema25_backend\n", "            recover_schema_backend\n"
    )
    replace(
        'if [[ "$current" == 20261022_24 ]]; then',
        'if [[ "$current" == "$SCHEMA_BEFORE" && "$current" != "$TARGET_REVISION" ]]; then',
    )
    replace(
        '[[ "$current" == 20261030_25 ]] || return 1',
        '[[ "$current" == "$TARGET_REVISION" ]] || return 1',
    )
    replace(
        "check 20261030_25 \\\n        /run/sales-schema25-recovery",
        'check "$TARGET_REVISION" \\\n        /run/sales-schema25-recovery',
    )
    replace(
        "say 'Backend восстановлен старым образом с launcher25; схема25 и данные сохранены.'",
        'say "Backend восстановлен старым образом с bridge launcher; схема $TARGET_REVISION и данные сохранены."',
    )
    replace(
        """        verify_image_startup "$NEW_IMAGE_ID" 1 || fail 'Candidate startup не соответствует проверяемому launcher25; legacy Alembic/command overrides запрещены.' """.rstrip(),
        """        prepare_candidate_launcher
        verify_image_startup "$NEW_IMAGE_ID" 1 || fail 'Candidate startup не соответствует целевой схеме; legacy Alembic/command overrides запрещены.' """.rstrip(),
    )
    replace(
        'if [[ "$SCHEMA_BEFORE" == 20261022_24 ]]; then',
        'if [[ "$SCHEMA_BEFORE" != "$TARGET_REVISION" ]]; then',
    )
    replace(
        'MIGRATION_CONTAINER="sales-schema25-migrate-$RUN_ID"',
        'MIGRATION_CONTAINER="sales-schema${TARGET_REVISION##*_}-migrate-$RUN_ID"',
    )
    replace(
        "say 'Явный переход PostgreSQL24 →25: rollback image и launcher предварительно проверены.'",
        'say "Явный переход PostgreSQL $SCHEMA_BEFORE → $TARGET_REVISION: rollback image и launcher предварительно проверены."',
    )
    replace(
        "            MIGRATION_STARTED=1\n            logged schema-migration",
        '            MIGRATION_STARTED=1\n            if [[ "$SCHEMA_BEFORE" == 20261030_25 && "$TARGET_REVISION" == 20261109_26 ]]; then\n                # Old25 workers do not know the new FTP/SMTP locks. Quiesce them\n                # only after all staging/preflights/checkpoints, before DDL.\n                validate_config "$OVERRIDE" "$BACKUP_DIR/rollback-config.json" || fail \'Не удалось проверить исходный YAML перед maintenance stop.\'\n                SWITCH_STARTED=1\n                logged schema26-quiesce compose stop backend || fail \'Не подтверждена остановка старого backend перед миграцией.\'\n            fi\n            logged schema-migration',
    )
    replace(
        "migrate 20261030_25 /app/start_backend.py",
        'migrate "$TARGET_REVISION" /app/start_backend.py',
    )
    replace(
        "check 20261030_25 /app/start_backend.py",
        'check "$TARGET_REVISION" /app/start_backend.py',
    )
    replace(
        "fail 'Read-only проверка candidate25 не пройдена.'",
        "fail 'Read-only проверка candidate не пройдена.'",
    )
    replace(
        "check 20261030_25 \\\n            /run/sales-schema25-recovery",
        'check "$TARGET_REVISION" \\\n            /run/sales-schema25-recovery',
    )
    replace(
        "fail 'Совместимость старого image на схеме25 не подтверждена.'",
        "fail 'Совместимость старого image через bridge на целевой схеме не подтверждена.'",
    )
    replace(
        "MIGRATION_ALLOWED=1 ;;",
        """MIGRATION_ALLOWED=1; MIGRATION_STEP='20261022_24:20261030_25' ;;
            --migrate-25-to-26)
                ((MIGRATION_ALLOWED == 0)) || { usage; exit 2; }
                MIGRATION_ALLOWED=1; MIGRATION_STEP='20261030_25:20261109_26' ;;""",
    )
    replace(
        "    PROJECT_DIR=$(pwd -P)\n",
        '    PROJECT_DIR=$(pwd -P)\n    if ((ALLOW_BUILD)) && [[ "$PROJECT_DIR" == /var/www/html/vr/sales ]]; then\n        fail "Сборка на production FirstVDS запрещена; укажите готовые образы."\n    fi\n',
    )
    replace(
        "                logged schema26-quiesce compose stop backend || fail 'Не подтверждена остановка старого backend перед миграцией.'\n",
        "                logged schema26-quiesce compose stop backend || fail 'Не подтверждена остановка старого backend перед миграцией.'\n                [[ \"$(docker inspect -f '{{.State.Running}}' sales-journal-backend 2>\"$BACKUP_DIR/quiesce-state.log\")\" == false ]] || fail 'Старый backend всё ещё работает; миграция запрещена.'\n                [[ \"$(container_id backend)\" == \"$OLD_IMAGE_ID\" ]] || fail 'Образ backend изменился до миграции.'\n",
    )
    replace(
        "import sys; sys.exit((SOURCE_LOCK, WORKER_LOCK, CATALOG_LOCK) != (734190220, 734190221, 734190117))'",
        'import sys,importlib; extra=importlib.import_module("app.services.import_control") if sys.argv[1]=="20261109_26" else None; sys.exit((SOURCE_LOCK, WORKER_LOCK, CATALOG_LOCK) != (734190220, 734190221, 734190117) or (extra is not None and (extra.FTP_LOCK, extra.MAIL_LOCK) != (734190271,734190272)))\' "$TARGET_REVISION"',
    )
    replace(
        '        if ! healthy_once "$CURRENT_SERVICE" "$OLD_IMAGE_ID"; then\n            assert_guard || return 1\n            # A CLI error does not prove that Docker failed to restore the image.\n            # Always inspect the resulting container and probe its health below.\n            logged rollback-up compose up -d --no-deps --no-build --pull never \\\n                --force-recreate "$CURRENT_SERVICE" || true\n        fi',
        '        # A failed/interrupted CLI may have left a stop/start RPC in flight.\n        # Once a switch was requested, always establish a new known container.\n        assert_guard || return 1\n        logged rollback-up compose up -d --no-deps --no-build --pull never \\\n            --force-recreate "$CURRENT_SERVICE" || true',
    )
    replace(
        "Do not recreate a still-healthy previous container after a failed up.",
        "Only this requested switch may be recreated; HTTP200 can be transient during stop.",
    )
    replace("OLD_IMAGE_ID=''\n", "OLD_IMAGE_ID=''\nOLD_CONTAINER_ID=''\n")
    replace(
        '    OLD_IMAGE_ID=$(container_id "$service") || fail "Не найден предыдущий контейнер $service."',
        '    OLD_IMAGE_ID=$(container_id "$service") || fail "Не найден предыдущий контейнер $service."\n    OLD_CONTAINER_ID=$(docker inspect -f \'{{.Id}}\' "sales-journal-$service" 2>"$BACKUP_DIR/container-identity.log") || fail "Не найден runtime container ID."\n    [[ -n "$OLD_CONTAINER_ID" ]] || fail "Пустой runtime container ID."',
    )
    replace(
        '        healthcheck "$CURRENT_SERVICE" "$OLD_IMAGE_ID" || return 1',
        '        [[ "$(docker inspect -f \'{{.Id}}\' "sales-journal-$CURRENT_SERVICE" 2>"$BACKUP_DIR/rollback-identity.log")" != "$OLD_CONTAINER_ID" ]] || return 1\n        healthcheck "$CURRENT_SERVICE" "$OLD_IMAGE_ID" || return 1',
    )
    replace(
        "    local configured resolved\n",
        "    local configured resolved current_container\n",
    )
    replace(
        '        [[ "$(docker inspect -f \'{{.Id}}\' "sales-journal-$CURRENT_SERVICE" 2>"$BACKUP_DIR/rollback-identity.log")" != "$OLD_CONTAINER_ID" ]] || return 1\n',
        '        current_container=$(docker inspect -f \'{{.Id}}\' "sales-journal-$CURRENT_SERVICE" 2>"$BACKUP_DIR/rollback-identity.log") || return 1\n        [[ -n "$current_container" && "$current_container" != "$OLD_CONTAINER_ID" ]] || return 1\n',
    )
    # Preserve historical recovery path/owner label so old barriers remain effective.
    return "\n".join(line.rstrip() for line in script.splitlines()).encode() + b"\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    if args.source.resolve() == args.destination.resolve():
        parser.error("Never overwrite the supplied production script")
    try:
        result = adapt(args.source.read_bytes())
    except ValueError as error:
        parser.error(str(error))
    with args.destination.open("xb") as output:
        output.write(result)
    checksum = hashlib.sha256(result).hexdigest()
    args.destination.with_name(args.destination.name + ".sha256").write_text(
        checksum + "  " + args.destination.name + "\n"
    )
    print(f"bytes={len(result)} lines={result.count(bytes([10]))} sha256={checksum}")


if __name__ == "__main__":
    main()
