"""Offline, SHA-pinned adaptation of the reviewed schema26 production updater.

Never executes shell, contacts production, edits the source or updates updater26.
A changed input, launcher or migration requires another review.
"""

import argparse
import hashlib
from pathlib import Path

INPUT_SHA256 = "78295c93abcc9c702b1028c41eafb552c94869483f83c99cb9bebe1bdc58e0d8"
LAUNCHER_SHA256 = "62934e47d2377a91238f073cc663d7dc2ea14ec9b404e14993c202548cf411bc"
MIGRATION_SHA256 = "8dc71eacda4f8f7eed74b326e37b87a6efe12f0e47d362ac2bb27dfab3946bd3"
SOURCE = "20261109_26"
TARGET = "20261111_27"

SCHEMA_PAYLOAD = r'''
import hashlib,json,runpy,sys
from pathlib import Path
try:
    action,expected,guard,launcher,checksum,target=sys.argv[1:]
    if target!='20261111_27' or checksum!='__LAUNCHER_SHA__':
        raise RuntimeError('unreviewed target or checksum')
    if hashlib.sha256(Path(launcher).read_bytes()).hexdigest()!=checksum:
        raise RuntimeError('launcher bytes differ')
    module=runpy.run_path(launcher,run_name='reviewed_deployment_launcher')
    if module.get('DEFAULT_REVISION')!=target or module.get('MIGRATION_SOURCES',{}).get(target)!={'20261109_26','20261111_27'}:
        raise RuntimeError('unreviewed launcher transition')
    from app.config import settings
    sa=module['sa'];engine=module['sync_engine'](settings)
    def context(details=False,empty=False):
        with engine.connect() as conn,conn.begin():
            conn.execute(sa.text('SET TRANSACTION READ ONLY'))
            conn.execute(sa.text("SET LOCAL statement_timeout='5s'"))
            conn.execute(sa.text("SET LOCAL lock_timeout='1s'"))
            conn.execute(sa.text('SELECT pg_advisory_xact_lock_shared(:key)'),{'key':module['SCHEMA_LOCK']})
            owned=conn.scalar(sa.text("""
                SELECT count(*) FROM pg_stat_activity a
                WHERE a.application_name=:guard AND a.datname=current_database()
                AND (SELECT count(DISTINCT l.objid) FROM pg_locks l
                     WHERE l.pid=a.pid AND l.locktype='advisory' AND l.classid=0 AND l.objsubid=1
                     AND l.mode='ExclusiveLock' AND l.granted
                     AND l.objid IN (734190221,734190220,734190117,734190271,734190272,734190270))=6
            """),{'guard':guard})
            if owned!=1:raise RuntimeError('deployment guard unavailable on backend database')
            current=module['revision'](conn)
            if current not in ('20261109_26','20261111_27'):raise RuntimeError('unsupported source schema')
            if not details:return current
            protected={}
            for table in ('ftp_config','smtp_config','import_control_config'):
                rows=list(conn.scalars(sa.text('SELECT to_jsonb(t) FROM '+table+' t ORDER BY id LIMIT 1001')))
                if len(rows)>1000:raise RuntimeError('protected metadata bound exceeded')
                protected[table]=rows
            if current=='20261111_27':
                if conn.scalar(sa.text("SELECT EXISTS(SELECT 1 FROM import_history_job WHERE status='running')")):
                    raise RuntimeError('historical reconciliation is running')
                if empty and (conn.scalar(sa.text('SELECT EXISTS(SELECT 1 FROM import_history_job)'))
                              or conn.scalar(sa.text('SELECT EXISTS(SELECT 1 FROM import_history_results)'))):
                    raise RuntimeError('initial history metadata is not empty')
            elif empty:raise RuntimeError('target schema is not committed')
            return hashlib.sha256(json.dumps(protected,sort_keys=True,default=str,separators=(',',':')).encode()).hexdigest()
    try:
        current=context()
        if action=='status':print(current)
        elif action in ('safety','initial-safety'):print(context(True,action=='initial-safety'))
        elif action=='check':
            if expected not in ('20261109_26','20261111_27'):raise RuntimeError('invalid check target')
            module['main'](['--rollback','--check-only','--expected-revision',expected]);context()
        elif action=='migrate':
            if expected!=target or current!='20261109_26':raise RuntimeError('only explicit source26 to target27 migration')
            migration=Path(module['app_root'])/'alembic/versions/20261111_27_import_history.py'
            if hashlib.sha256(migration.read_bytes()).hexdigest()!='__MIGRATION_SHA__':raise RuntimeError('migration bytes differ')
            from alembic.config import Config
            from alembic.script import ScriptDirectory
            cfg=Config();cfg.set_main_option('script_location',str(migration.parent.parent))
            graph=ScriptDirectory.from_config(cfg)
            if graph.get_heads()!=[target] or graph.get_revision(target).down_revision!=current:raise RuntimeError('migration graph differs')
            module['main'](['--migrate','--expected-revision',target])
            if context()!=target:raise RuntimeError('migration not confirmed')
        else:raise RuntimeError('unsupported action')
    finally:engine.dispose()
except Exception:
    print('DEPLOY_SCHEMA_JOB_FAILED',file=sys.stderr);sys.exit(1)
'''.replace(
    "__LAUNCHER_SHA__", LAUNCHER_SHA256
).replace(
    "__MIGRATION_SHA__", MIGRATION_SHA256
)

IMPORT_PREFLIGHT = r"""import hashlib,sys
from pathlib import Path
import app.main,app.seasonality,app.routers.analytics,app.routers.horeca_reports,app.services.scenarios
import app.services.import_history
from app.services.seasonality_worker import SOURCE_LOCK,WORKER_LOCK,CATALOG_LOCK
from app.services.import_control import FTP_LOCK,MAIL_LOCK,CONTROL_LOCK
from alembic.config import Config
from alembic.script import ScriptDirectory
if sys.argv[1]!='20261111_27':raise SystemExit(1)
if (SOURCE_LOCK,WORKER_LOCK,CATALOG_LOCK,FTP_LOCK,MAIL_LOCK,CONTROL_LOCK)!=(734190220,734190221,734190117,734190271,734190272,734190270):raise SystemExit(1)
path=Path('/app/alembic/versions/20261111_27_import_history.py')
if hashlib.sha256(path.read_bytes()).hexdigest()!='__MIGRATION_SHA__':raise SystemExit(1)
cfg=Config();cfg.set_main_option('script_location','/app/alembic')
graph=ScriptDirectory.from_config(cfg)
if graph.get_heads()!=['20261111_27'] or graph.get_revision('20261111_27').down_revision!='20261109_26':raise SystemExit(1)
""".replace(
    "__MIGRATION_SHA__", MIGRATION_SHA256
)


def adapt(source):
    if hashlib.sha256(source).hexdigest() != INPUT_SHA256:
        raise ValueError("Unreviewed updater26 bytes: SHA256 differs")
    script = source.decode("utf-8")

    def replace(before, after, count=1):
        nonlocal script
        if script.count(before) != count:
            raise ValueError("Audited anchor missing or ambiguous: " + before[:100])
        script = script.replace(before, after)

    def region(start, end, content):
        nonlocal script
        if script.count(start) != 1 or script.count(end) != 1:
            raise ValueError("Ambiguous audited region")
        left, right = script.index(start), script.index(end)
        if right <= left:
            raise ValueError("Invalid audited region")
        script = script[:left] + content.rstrip() + "\n" + script[right:]

    replace("ALLOW_BUILD=0\n", "")
    replace(
        "[--migrate-24-to-25|--migrate-25-to-26] [--allow-build]",
        "[--migrate-26-to-27]",
    )
    replace(
        "if target not in (None,'20261030_25','20261109_26'): raise SystemExit(1)",
        "if target not in (None,'20261111_27'): raise SystemExit(1)",
    )
    replace(
        "revisions=[target] if target else ['20261030_25','20261109_26']",
        "revisions=[target] if target else ['20261109_26','20261111_27']",
    )
    region(
        "schema_python() {",
        "schema_job() {",
        "schema_python() {\n    cat <<'PY_SCHEMA'\n"
        + SCHEMA_PAYLOAD.strip()
        + "\nPY_SCHEMA\n}\n",
    )
    replace(
        "    assert_guard\n}\nassert_guard() {",
        "    guard_query \"SELECT CASE WHEN pg_try_advisory_lock(734190270) THEN 'CONTROL_OK' ELSE 'BUSY' END;\" CONTROL_OK || return 1\n    assert_guard\n}\nassert_guard() {",
    )
    replace(
        "objid IN (734190221,734190220,734190117,734190271,734190272)",
        "objid IN (734190221,734190220,734190117,734190271,734190272,734190270)",
    )
    replace(")=5 THEN 'GUARD_OK'", ")=6 THEN 'GUARD_OK'")
    replace("all five application locks", "all six application locks")
    replace(
        "Получение всех пяти блокировок для отката.",
        "Получение всех шести блокировок для отката.",
    )
    replace(
        "Worker, source, catalog, FTP и SMTP заблокированы одной PostgreSQL-сессией.",
        "Worker, source, catalog, FTP, SMTP и контроль импорта заблокированы одной PostgreSQL-сессией.",
    )
    replace(
        "if target not in ('20261030_25','20261109_26') or target not in values.get('SUPPORTED_REVISIONS',()): raise SystemExit(1)\nif target not in values.get('MIGRATION_SOURCES',{}): raise SystemExit(1)",
        "if target!='20261111_27' or target not in values.get('SUPPORTED_REVISIONS',()): raise SystemExit(1)\nif values.get('MIGRATION_SOURCES',{}).get(target)!={'20261109_26','20261111_27'}: raise SystemExit(1)\nimport hashlib\nif hashlib.sha256(data).hexdigest()!='"
        + LAUNCHER_SHA256
        + "': raise SystemExit(1)",
    )
    replace(
        '[[ "$current" == 20261022_24 || "$current" == 20261030_25 || "$current" == 20261109_26 ]]',
        '[[ "$current" == 20261109_26 || "$current" == 20261111_27 ]]',
    )
    replace("downgrade и24→26 запрещены.", "разрешён только26→27, downgrade запрещён.")
    replace(
        "    printf 'schema_before=%s\\n' \"$SCHEMA_BEFORE\"",
        '    if [[ "$current" == "$TARGET_REVISION" && -n "$MIGRATION_STEP" ]]; then\n        fail \'База уже27: миграционный флаг запрещён; проверьте actual state и используйте обычное обновление без DDL.\'\n    fi\n    printf \'schema_before=%s\\n\' "$SCHEMA_BEFORE"',
    )
    replace(
        "    ROLLBACK_READY=1\n}",
        """    logged protected-before schema_job "$BACKUP_DIR/backend.candidate.yml" safety '' /app/start_backend.py "$ROLLBACK_LAUNCHER_SHA" \\
        || fail 'Историческая сверка активна или не подтверждена сохранность параметров импорта.'
    ROLLBACK_READY=1
}""",
    )
    # Prepared images only: remove the complete build path, not just its CLI flag.
    region(
        '    if [[ -n "$prepared_id" ]]; then',
        '    NEW_IMAGE_ID=$(image_id "$new_tag")',
        """    [[ -n "$prepared_id" ]] || fail 'Требуется заранее подготовленный Docker-образ; сборка запрещена.'
    say "$service: готовый образ закрепляется под $new_tag без сборки."
    logged "prepared-$service-tag" docker image tag "$prepared_id" "$new_tag" || fail 'Не удалось закрепить prepared image.'
""",
    )
    region(
        "        logged imports compose_file",
        "        prepare_schema_rollback\n",
        """        logged imports compose_file "$candidate" run --rm --no-deps --pull never --entrypoint python backend -B -c \\
            """
        + "'"
        + IMPORT_PREFLIGHT.rstrip().replace("'", "'\"'\"'")
        + "'"
        + """ "$TARGET_REVISION" \\
            || fail 'Python/locks/проверенная миграция27 несовместимы; переключение не начиналось.'
""",
    )
    replace(
        'if [[ "$SCHEMA_BEFORE" == 20261030_25 && "$TARGET_REVISION" == 20261109_26 ]]; then',
        'if [[ "$SCHEMA_BEFORE" == 20261109_26 && "$TARGET_REVISION" == 20261111_27 ]]; then',
    )
    replace(
        "# Old25 workers do not know the new FTP/SMTP locks. Quiesce them",
        "# Stop all in-process workers before26→27 DDL. Quiesce them",
    )
    replace(
        "logged schema26-quiesce compose stop backend",
        "logged schema27-quiesce compose stop backend",
    )
    replace(
        '    if [[ "$SCHEMA_BEFORE" != "$TARGET_REVISION" ]]; then\n',
        '    if [[ "$SCHEMA_BEFORE" != "$TARGET_REVISION" ]]; then\n',
        count=1,
    )
    # After DDL but BEFORE activation, enforce an empty new history and unchanged configs.
    replace(
        '        logged schema schema_job "$candidate" check',
        """        local safety_action=safety
        [[ "$SCHEMA_BEFORE" == "$TARGET_REVISION" ]] || safety_action=initial-safety
        logged protected-after schema_job "$candidate" "$safety_action" '' /app/start_backend.py "$ROLLBACK_LAUNCHER_SHA" \\
            || fail 'Историческая сверка/сохранность параметров после DDL не подтверждена.'
        cmp -s "$BACKUP_DIR/protected-before.log" "$BACKUP_DIR/protected-after.log" \\
            || fail 'Параметры FTP/SMTP/уведомлений изменились: требуется ручная проверка, значения не откатываются вслепую.'
        logged schema schema_job "$candidate" check""",
    )
    replace(
        "             'rollback-start_backend.py','release.txt','images.txt'):",
        "             'rollback-start_backend.py','release.txt','images.txt','protected-before.log'):",
    )
    region(
        "            --allow-build)\n",
        "            *) usage; exit 2 ;;",
        """            --migrate-26-to-27)
                ((MIGRATION_ALLOWED == 0)) || { usage; exit 2; }
                MIGRATION_ALLOWED=1; MIGRATION_STEP='20261109_26:20261111_27' ;;
""",
    )
    replace(
        """        if [[ -z "$reference" ]]; then
            ((ALLOW_BUILD == 1)) || fail 'ОШИБКА: Готовый Docker-образ не указан. Сборка на VPS запрещена по умолчанию.'
            continue
        fi""",
        """        [[ -n "$reference" ]] || fail 'Готовый Docker-образ не указан. Сборка и pull запрещены.' """,
    )
    replace(
        """    if ((ALLOW_BUILD)) && [[ "$PROJECT_DIR" == /var/www/html/vr/sales ]]; then
        fail "Сборка на production FirstVDS запрещена; укажите готовые образы."
    fi
""",
        "",
    )
    replace("mkdir sleep chmod sha256sum;", "mkdir sleep chmod sha256sum cmp;")
    replace(
        "    say 'Worker, source, catalog, FTP, SMTP и контроль импорта заблокированы одной PostgreSQL-сессией.'",
        "    guard_query \"SELECT CASE WHEN (SELECT array_agg(version_num::text ORDER BY version_num) FROM alembic_version) IN (ARRAY['20261109_26'],ARRAY['20261111_27']) THEN 'SCHEMA_SUPPORTED' ELSE 'UNSUPPORTED' END;\" SCHEMA_SUPPORTED || fail 'Разрешены только схемы26/27.'\n    say 'Worker, source, catalog, FTP, SMTP и контроль импорта заблокированы одной PostgreSQL-сессией.'",
    )
    replace(
        '    check_git_clean\n    validate_config "$OVERRIDE" "$BACKUP_DIR/after-pull.json"',
        '    check_git_clean\n    logged git-release git merge-base --is-ancestor c2eabb69e85d547c25bfcfd2bdbf89497fa035d8 HEAD || fail \'main не содержит проверенный PR177; обновление запрещено.\'\n    validate_config "$OVERRIDE" "$BACKUP_DIR/after-pull.json"',
    )
    # Compose run has no --no-build flag: a transient image loss must NEVER build.
    # Only these one-off jobs get an extra build-reset overlay. Active config stays byte-identical.
    replace(
        '    cp -p "$OVERRIDE" "$BACKUP_DIR/original.yml"',
        '    cp -p "$OVERRIDE" "$BACKUP_DIR/original.yml"\n    printf \'services:\\n  backend:\\n    build: !reset null\\n\' >"$BACKUP_DIR/run-prebuilt-only.yml"',
    )
    replace(
        '    compose_file "$file" run --rm -T',
        '    compose_file "$file" -f "$BACKUP_DIR/run-prebuilt-only.yml" run --rm -T',
    )
    replace(
        '        logged imports compose_file "$candidate" run --rm',
        '        logged imports compose_file "$candidate" -f "$BACKUP_DIR/run-prebuilt-only.yml" run --rm',
    )
    replace(
        "'images.txt','protected-before.log'):",
        "'images.txt','protected-before.log','run-prebuilt-only.yml'):",
    )
    replace(
        "    # This tiny critical section is the commit boundary.",
        """    if [[ "$service" == backend ]]; then
        logged protected-activated schema_job "$candidate" "$safety_action" '' /app/start_backend.py "$ROLLBACK_LAUNCHER_SHA" \\
            || fail 'История/параметры после запуска backend не подтверждены.'
        cmp -s "$BACKUP_DIR/protected-before.log" "$BACKUP_DIR/protected-activated.log" \\
            || fail 'Параметры изменились при переключении; требуется ручная проверка.'
    fi
    # This tiny critical section is the commit boundary.""",
    )
    if (
        "__LAUNCHER_SHA__" in script
        or "__MIGRATION_SHA__" in script
        or "ALLOW_BUILD" in script
        or "--migrate-25-to-26" in script
        or "--migrate-24-to-25" in script
    ):
        raise ValueError("Legacy build/migration path survived adaptation")
    return ("\n".join(line.rstrip() for line in script.splitlines()) + "\n").encode()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    sidecar = args.destination.with_name(args.destination.name + ".sha256")
    if args.source.resolve() in (args.destination.resolve(), sidecar.resolve()):
        parser.error("Never overwrite the input updater26")
    if (
        args.destination.exists()
        or sidecar.exists()
        or args.destination.is_symlink()
        or sidecar.is_symlink()
    ):
        parser.error("Destination/checksum must not already exist")
    try:
        result = adapt(args.source.read_bytes())
    except ValueError as error:
        parser.error(str(error))
    with args.destination.open("xb") as output:
        output.write(result)
    checksum = hashlib.sha256(result).hexdigest()
    with sidecar.open("x") as output:
        output.write(checksum + "  " + args.destination.name + "\n")
    print(f"bytes={len(result)} lines={result.count(bytes([10]))} sha256={checksum}")


if __name__ == "__main__":
    main()
