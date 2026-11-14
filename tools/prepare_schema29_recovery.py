"""Dedicated schema29 maintenance exit; no DDL, fill or normal updater main."""

import argparse, hashlib
from pathlib import Path
import prepare_schema29_updater as update
import prepare_schema27_updater as legacy


def prepare(source):
    prefix = update.adapt(source).decode().rsplit('\nmain "$@"', 1)[0]
    prefix = prefix[: prefix.index("\nmain() {")]
    for unused in ("MIGRATION_ALLOWED=0\n", "BRANCH=main\n", "MODE=all\n"):
        prefix = prefix.replace(unused, "", 1)
    start = prefix.index("        elif action=='migrate':")
    end = prefix.index("        else:raise RuntimeError('unsupported action')", start)
    prefix = prefix[:start] + prefix[end:]
    prefix = prefix.replace(
        "update_service() {",
        'update_service() {\n    [[ "$1" == frontend ]] || return 1',
        1,
    )
    entry = Path(__file__).with_name("schema27_recovery_entry.sh").read_text()
    start = entry.index("normal_runtime() {")
    end = entry.index("\nrecovery_protected() {", start)
    entry = entry[:start] + entry[end:]  # Shared exact normal runtime implementation.
    entry = entry.replace(
        "__IMPORT_PREFLIGHT__",
        update.import_preflight().rstrip().replace("'", "'\"'\"'"),
    )
    entry = update.literal_map(
        entry,
        {
            legacy.TARGET: update.TARGET,
            legacy.SOURCE: update.SOURCE,
            legacy.LAUNCHER_SHA256: update.LAUNCHER_SHA256,
            legacy.MIGRATION_SHA256: update.MIGRATION_SHA256,
            "20261111_27_import_history.py": "20261114_29_category_season_snapshots.py",
            "schema27": "schema29",
            "bridge27": "bridge29",
            "launcher27": "launcher29",
            "backend27": "backend29",
            "frontend27": "frontend29",
            "Backend27": "Backend29",
            "Bridge27": "Bridge29",
            "SCHEMA27": "SCHEMA29",
            "schema26": "schema28",
            "exit27": "exit29",
        },
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
    print(f"bytes={len(data)} lines={data.count(bytes([10]))} sha256={digest}")


if __name__ == "__main__":
    main()
