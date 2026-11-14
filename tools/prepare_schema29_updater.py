"""SHA-pinned prepared-only schema28→29 adaptation of the reviewed 26→27→28 chain.
No production access; never overwrites inputs or changes prior generators.
"""

import argparse
import hashlib
import re
from pathlib import Path
import prepare_schema28_updater as previous
import prepare_schema28_recovery as previous_recovery
import prepare_schema27_updater as legacy

ROOT = Path(__file__).resolve().parents[1]
SOURCE = "20261112_28"
TARGET = "20261114_29"
INPUT_SHA256 = previous.INPUT_SHA256
LAUNCHER_SHA256 = "8a5dfc4bd27a0e3a618fa5390f4b88c6e610f3ea7e9e93be4368e77823d31935"
MIGRATION_SHA256 = "c6b388b04a574123ea1a669485d429d2afc51125ab301c3df284f3b260af00e4"
RELEASE = "607b1de9ffe120130655f4584b68672aa6ceef27"
BASELINE28_UPDATE_SHA256 = (
    "3450adae185c5d51a0cf3fff1956389a7edfa0055f0b4e349f6b4ee4402ec9c2"
)
BASELINE28_RECOVERY_SHA256 = (
    "5ea6a71f65af29324fb53a1a110a5d4bc7e25745339364fce8973632c4383692"
)


def literal_map(value, mapping):
    return re.sub(
        "|".join(re.escape(k) for k in sorted(mapping, key=len, reverse=True)),
        lambda m: mapping[m[0]],
        value,
    )


def payload():
    # Keep the reviewed read-only guard/protocol and explicit migration machinery.
    code = literal_map(
        previous.payload(),
        {
            previous.SOURCE: SOURCE,
            previous.TARGET: TARGET,
            previous.LAUNCHER_SHA256: LAUNCHER_SHA256,
            previous.MIGRATION_SHA256: MIGRATION_SHA256,
            "20261112_28_sales_control.py": "20261114_29_category_season_snapshots.py",
        },
    )
    start = code.index(
        '            if conn.scalar(sa.text("SELECT EXISTS(SELECT 1 FROM import_history_job'
    )
    end = code.index("            return hashlib.sha256", start)
    code = (
        code[:start]
        + """            if conn.scalar(sa.text("SELECT EXISTS(SELECT 1 FROM import_history_job WHERE status='running')")):
                raise RuntimeError('retired history must not be running')
            protected['sales_control_settings']=list(conn.scalars(sa.text('SELECT to_jsonb(t) FROM sales_control_settings t ORDER BY id LIMIT 1001')))
            if len(protected['sales_control_settings'])>1000:raise RuntimeError('protected metadata bound exceeded')
            if current=='20261114_29':
                if conn.scalar(sa.text("SELECT EXISTS(SELECT 1 FROM category_season_snapshots WHERE status='building')")):
                    raise RuntimeError('category snapshot build must be reviewed before update')
                if empty:
                    state=list(conn.execute(sa.text('SELECT id,active_id,next_due FROM category_season_state')))
                    if len(state)!=1 or tuple(state[0])!=(1,None,None):raise RuntimeError('snapshot control must initialize inactive')
                    for table in ('category_season_snapshots','category_season_facts','category_season_categories','category_season_clients','category_season_memberships','category_season_coverage'):
                        if conn.scalar(sa.text('SELECT EXISTS(SELECT 1 FROM '+table+')')):raise RuntimeError('first fill must not run during release')
            elif empty:raise RuntimeError('target schema not committed')
"""
        + code[end:]
    )
    return code.replace(
        "only explicit source26 to target27 migration",
        "only explicit source28 to target29 migration",
    )


def import_preflight():
    return literal_map(
        legacy.IMPORT_PREFLIGHT,
        {
            legacy.TARGET: TARGET,
            legacy.SOURCE: SOURCE,
            legacy.MIGRATION_SHA256: MIGRATION_SHA256,
            "20261111_27_import_history.py": "20261114_29_category_season_snapshots.py",
            "import app.services.import_history": "import app.services.category_seasonality_snapshot,app.routers.category_seasonality_report,app.models.category_seasonality",
        },
    )


def normal_runtime():
    entry = Path(__file__).with_name("schema27_recovery_entry.sh").read_text()
    return entry[
        entry.index("normal_runtime() {") : entry.index("\nrecovery_protected() {")
    ]


def adapt(source):
    script = previous.adapt(source).decode()
    if hashlib.sha256(script.encode()).hexdigest() != BASELINE28_UPDATE_SHA256:
        raise ValueError("Reviewed schema28 generator output changed")
    if (
        hashlib.sha256(previous_recovery.prepare(source)).hexdigest()
        != BASELINE28_RECOVERY_SHA256
    ):
        raise ValueError("Reviewed schema28 recovery dependencies changed")

    def replace(before, after, count=1):
        nonlocal script
        if script.count(before) != count:
            raise ValueError(
                "Reviewed schema29 anchor missing/ambiguous: " + before[:80]
            )
        script = script.replace(before, after)

    # Remove whole schema/import payloads before mapping the exact release literals.
    start = script.index("    cat <<'PY_SCHEMA'\n") + len("    cat <<'PY_SCHEMA'\n")
    end = script.index("\nPY_SCHEMA", start)
    script = script[:start] + "__SCHEMA29_PAYLOAD__" + script[end:]
    old_import = (
        literal_map(
            legacy.IMPORT_PREFLIGHT,
            {
                legacy.TARGET: previous.TARGET,
                legacy.SOURCE: previous.SOURCE,
                legacy.MIGRATION_SHA256: previous.MIGRATION_SHA256,
                "20261111_27_import_history.py": "20261112_28_sales_control.py",
                "import app.services.import_history": "import app.services.sales_control",
            },
        )
        .rstrip()
        .replace("'", "'\"'\"'")
    )
    replace(old_import, "__IMPORT29_PAYLOAD__")
    script = literal_map(
        script,
        {
            previous.SOURCE: SOURCE,
            previous.TARGET: TARGET,
            previous.LAUNCHER_SHA256: LAUNCHER_SHA256,
            previous.MIGRATION_SHA256: MIGRATION_SHA256,
            "--migrate-27-to-28": "--migrate-28-to-29",
            "20261112_28_sales_control.py": "20261114_29_category_season_snapshots.py",
            "de696e386ef989217b1c7d96c5f3ff34630be226": RELEASE,
        },
    )
    script = script.replace("__SCHEMA29_PAYLOAD__", payload().strip()).replace(
        "__IMPORT29_PAYLOAD__", import_preflight().rstrip().replace("'", "'\"'\"'")
    )
    replace(
        "        [[ -n \"$reference\" ]] || fail 'Готовый Docker-образ не указан. Сборка и pull запрещены.'",
        "        [[ \"$reference\" =~ ^sha256:[0-9a-f]{64}$ ]] || fail 'Требуется полный immutable sha256:ID готового образа; теги запрещены.'",
    )
    replace(
        '        [[ -n "$resolved" ]] || fail "Не получен идентификатор готового образа $selected."',
        '        [[ "$resolved" == "$reference" ]] || fail "Immutable image ID $selected не совпадает."',
    )
    replace(
        "    ((MIN_AVAILABLE_KB >= 262144)) || fail 'Порог available RAM нельзя снижать ниже 256 МиБ.'",
        "    ((MIN_AVAILABLE_KB >= 262144 && MIN_FREE_KB >= 2097152)) || fail 'Пороги RAM/диска нельзя снижать ниже проверенных 256 МиБ/2 ГиБ.'",
    )
    # Admission checks happen under the guard, before Git/image/config/service writes.
    anchor = "    say 'Worker, source, catalog, FTP, SMTP и контроль импорта заблокированы одной PostgreSQL-сессией.'"
    replace(
        anchor,
        """    if guard_query "SELECT CASE WHEN (SELECT array_agg(version_num::text ORDER BY version_num) FROM alembic_version)=ARRAY['20261112_28'] THEN 'SCHEMA_SOURCE' ELSE 'OTHER' END;" SCHEMA_SOURCE; then
        [[ "$MODE" != --frontend-only && "$MIGRATION_ALLOWED" == 1 ]] || fail 'Для source28 нужен backend и точный --migrate-28-to-29.'
    else
        guard_query "SELECT CASE WHEN (SELECT array_agg(version_num::text ORDER BY version_num) FROM alembic_version)=ARRAY['20261114_29'] THEN 'SCHEMA_TARGET' ELSE 'OTHER' END;" SCHEMA_TARGET || fail 'Фактическая схема не подтверждена.'
        ((MIGRATION_ALLOWED==0)) || fail 'На schema29 повторный миграционный флаг запрещён; при marker нужен recovery.'
    fi
"""
        + anchor,
    )
    replace(
        "recover_schema_backend() {", normal_runtime() + "\nrecover_schema_backend() {"
    )
    replace(
        "        protected_snapshot protected-activated",
        """        normal_runtime || fail 'Backend image/launcher/PID1/lifespan normal не подтверждены.'
        logged activated-schema schema_job "$candidate" check "$TARGET_REVISION" /app/start_backend.py "$ROLLBACK_LAUNCHER_SHA" || fail 'Физическая схема после запуска backend не подтверждена.'
        protected_snapshot protected-activated""",
    )
    # Normal already29 switches need the same durable ambiguity barrier as DDL.
    replace(
        "    # Set the recovery flag BEFORE the atomic replacement, even on a signal window.",
        """    if [[ "$service" == backend ]] && ((MIGRATION_STARTED==0)); then
        write_transition_marker || fail 'Не сохранён marker обычного переключения29.'
    fi
    # Set the recovery flag BEFORE the atomic replacement, even on a signal window.""",
    )
    for before, after in {
        "База уже28:": "База уже29:",
        "Разрешены только схемы27/28.": "Разрешены только схемы28/29.",
        "разрешён только27→28": "разрешён только28→29",
        "проверенный PR181": "проверенный PR186",
        "проверенная миграция28": "проверенная миграция29",
        "schema27-quiesce": "schema29-quiesce",
    }.items():
        script = script.replace(before, after)
    if any(
        x in script
        for x in (
            "__SCHEMA29",
            "__IMPORT29",
            "pg_terminate_backend",
            "alembic downgrade",
            "--allow-build",
        )
    ):
        raise ValueError("Unsafe/unexpanded deployment path")
    return script.encode()


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("source", type=Path)
    p.add_argument("destination", type=Path)
    a = p.parse_args()
    side = a.destination.with_name(a.destination.name + ".sha256")
    if a.source.resolve() in (a.destination.resolve(), side.resolve()) or any(
        x.exists() or x.is_symlink() for x in (a.destination, side)
    ):
        p.error("Never overwrite source/output/checksum")
    data = adapt(a.source.read_bytes())
    with a.destination.open("xb") as f:
        f.write(data)
    digest = hashlib.sha256(data).hexdigest()
    with side.open("x") as f:
        f.write(digest + "  " + a.destination.name + "\n")
    print(f"bytes={len(data)} lines={data.count(bytes([10]))} sha256={digest}")


if __name__ == "__main__":
    main()
