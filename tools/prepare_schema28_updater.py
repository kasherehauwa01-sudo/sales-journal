"""Reviewed schema27→28 adaptation: new payload, frozen contracts, unchanged release guards."""

import argparse, hashlib, re
from pathlib import Path
import prepare_schema27_updater as previous

ROOT = Path(__file__).resolve().parents[1]
SOURCE = "20261111_27"
TARGET = "20261112_28"
# Updated only after launcher/migration freeze and native PG tests.
LAUNCHER_SHA256 = "1281639978dfc816c17e3be5d2c16e11c0d5153c4549f633ff4306f47f7260f0"
MIGRATION_SHA256 = "199133c7be2f81fbe40f5082edb9f732d71a1087764eb268a8d4249f15011bc2"
INPUT_SHA256 = previous.INPUT_SHA256


def payload():
    code = previous.SCHEMA_PAYLOAD
    # Simultaneous literal mapping of the reviewed transition. The history safety
    # block is then REPLACED: new metadata has a different initialization contract.
    mapping = {
        "20261109_26": SOURCE,
        "20261111_27": TARGET,
        previous.LAUNCHER_SHA256: LAUNCHER_SHA256,
        previous.MIGRATION_SHA256: MIGRATION_SHA256,
        "20261111_27_import_history.py": "20261112_28_sales_control.py",
    }
    code = re.sub(
        "|".join(re.escape(k) for k in sorted(mapping, key=len, reverse=True)),
        lambda m: mapping[m[0]],
        code,
    )
    code = code.replace(
        "            if not details:return current",
        """            if not conn.scalar(sa.text("SELECT EXISTS(SELECT 1 FROM pg_index i WHERE i.indexrelid=to_regclass('ix_sales_department_date_control') AND i.indrelid='sales'::regclass AND i.indisvalid AND i.indisready AND i.indpred IS NULL AND i.indexprs IS NULL AND i.indnkeyatts=2 AND pg_get_indexdef(i.indexrelid,1,true)='department' AND pg_get_indexdef(i.indexrelid,2,true)='sale_date')")):
                raise RuntimeError('Prepare valid sales-control index before service stop/migration')
            if not details:return current""",
    )
    start = code.index("            if current=='20261112_28':")
    end = code.index("            return hashlib.sha256", start)
    code = (
        code[:start]
        + """            if conn.scalar(sa.text("SELECT EXISTS(SELECT 1 FROM import_history_job WHERE status='running')")):
                raise RuntimeError('retired history must be paused/completed before deployment')
            if current=='20261112_28':
                protected['sales_control_settings']=list(conn.scalars(sa.text('SELECT to_jsonb(t) FROM sales_control_settings t ORDER BY id')))
                if empty and (conn.scalar(sa.text('SELECT enabled FROM sales_control_settings WHERE id=1')) is not False or any(conn.scalar(sa.text('SELECT EXISTS(SELECT 1 FROM '+t+')')) for t in ('sales_control_exclusions','sales_control_audit','sales_control_deliveries'))):
                    raise RuntimeError('sales control must initialize disabled without automatic work')
            else:
                if empty:raise RuntimeError('target schema not committed')
                protected['sales_control_settings']=[dict(id=1,enabled=False,send_time='09:00',recipients=[],not_before=None,revision=1)]
"""
        + code[end:]
    )
    return code


def adapt(source):
    script = previous.adapt(source).decode()

    def replace(before, after, count=1):
        nonlocal script
        if script.count(before) != count:
            raise ValueError(
                "Reviewed schema28 anchor missing/ambiguous: " + before[:80]
            )
        script = script.replace(before, after)

    # Replace entire database payload, not arithmetic revision substitutions.
    start = script.index("    cat <<'PY_SCHEMA'\n") + len("    cat <<'PY_SCHEMA'\n")
    end = script.index("\nPY_SCHEMA", start)
    script = script[:start] + "__SCHEMA28_PAYLOAD__" + script[end:]
    # Deployment literal pair and pins outside the replaced payload.
    mapping = {
        "20261109_26": SOURCE,
        "20261111_27": TARGET,
        previous.LAUNCHER_SHA256: LAUNCHER_SHA256,
        previous.MIGRATION_SHA256: MIGRATION_SHA256,
        "20261111_27_import_history.py": "20261112_28_sales_control.py",
        "--migrate-26-to-27": "--migrate-27-to-28",
    }
    script = re.sub(
        "|".join(re.escape(k) for k in sorted(mapping, key=len, reverse=True)),
        lambda m: mapping[m[0]],
        script,
    )
    script = script.replace("__SCHEMA28_PAYLOAD__", payload().strip())
    replace("import app.services.import_history", "import app.services.sales_control")
    replace(
        "c2eabb69e85d547c25bfcfd2bdbf89497fa035d8",
        "de696e386ef989217b1c7d96c5f3ff34630be226",
    )
    # Operator messages must describe the actual target; historical filenames for
    # recovery/barriers remain deliberately unchanged for old-updater recognition.
    script = (
        script.replace("База уже27:", "База уже28:")
        .replace("Разрешены только схемы26/27.", "Разрешены только схемы27/28.")
        .replace("разрешён только26→27", "разрешён только27→28")
        .replace("проверенный PR177", "проверенный PR181")
        .replace("проверенная миграция27", "проверенная миграция28")
    )
    if "pg_terminate_backend" in script or "alembic downgrade" in script:
        raise ValueError("Unsafe schema action")
    return script.encode()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    side = args.destination.with_name(args.destination.name + ".sha256")
    if args.source.resolve() in (args.destination.resolve(), side.resolve()) or any(
        p.exists() or p.is_symlink() for p in (args.destination, side)
    ):
        parser.error("Never overwrite source/existing output/checksum")
    data = adapt(args.source.read_bytes())
    with args.destination.open("xb") as f:
        f.write(data)
    digest = hashlib.sha256(data).hexdigest()
    with side.open("x") as f:
        f.write(digest + "  " + args.destination.name + "\n")
    print(f"bytes={len(data)} lines={data.count(bytes([10]))} sha256={digest}")


if __name__ == "__main__":
    main()
