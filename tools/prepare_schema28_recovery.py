"""Offline schema28 maintenance exit, reusing reviewed durable recovery transaction."""

import argparse, hashlib, re
from pathlib import Path
import prepare_schema28_updater as update
import prepare_schema27_updater as legacy


def prepare(source):
    prefix = update.adapt(source).decode().rsplit('\nmain "$@"', 1)[0]
    prefix = prefix[: prefix.index("\nmain() {")]
    for unused in ("MIGRATION_ALLOWED=0\n", "BRANCH=main\n", "MODE=all\n"):
        prefix = prefix.replace(unused, "", 1)
    begin = prefix.index("        elif action=='migrate':")
    end = prefix.index("        else:raise RuntimeError('unsupported action')", begin)
    prefix = prefix[:begin] + prefix[end:]
    prefix = prefix.replace(
        "update_service() {",
        'update_service() {\n    [[ "$1" == frontend ]] || return 1',
        1,
    )
    entry = Path(__file__).with_name("schema27_recovery_entry.sh").read_text()
    entry = entry.replace(
        "__IMPORT_PREFLIGHT__", legacy.IMPORT_PREFLIGHT.rstrip().replace("'", "'\"'\"'")
    )
    mapping = {
        "20261111_27": update.TARGET,
        "20261109_26": update.SOURCE,
        legacy.LAUNCHER_SHA256: update.LAUNCHER_SHA256,
        legacy.MIGRATION_SHA256: update.MIGRATION_SHA256,
        "20261111_27_import_history.py": "20261112_28_sales_control.py",
        "import app.services.import_history": "import app.services.sales_control",
    }
    entry = re.sub(
        "|".join(re.escape(k) for k in sorted(mapping, key=len, reverse=True)),
        lambda m: mapping[m[0]],
        entry,
    )
    entry = (
        entry.replace("schema27", "schema28")
        .replace("bridge27", "bridge28")
        .replace("launcher27", "launcher28")
        .replace("backend27", "backend28")
        .replace("frontend27", "frontend28")
        .replace("schema26", "schema27")
    )
    return (prefix + "\n" + entry).encode()


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
    data = prepare(a.source.read_bytes())
    digest = hashlib.sha256(data).hexdigest()
    with a.destination.open("xb") as f:
        f.write(data)
    with side.open("x") as f:
        f.write(digest + "  " + a.destination.name + "\n")
    print("sha256=" + digest)


if __name__ == "__main__":
    main()
