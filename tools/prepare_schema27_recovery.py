"""Offline generator: reviewed updater primitives plus a schema27-only recovery entrypoint."""

import argparse
import hashlib
from pathlib import Path
from prepare_schema27_updater import adapt, IMPORT_PREFLIGHT


def prepare(source):
    updater = adapt(source).decode()
    prefix, invocation = updater.rsplit('\nmain "$@"', 1)
    if invocation.strip():
        raise ValueError("Unexpected updater trailer")
    # Remove the normal updater entrypoint; additionally deny every DDL action in
    # the embedded job and restrict the shared service transaction to frontend.
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

    return (
        prefix
        + "\n"
        + Path(__file__)
        .with_name("schema27_recovery_entry.sh")
        .read_text()
        .replace(
            "__IMPORT_PREFLIGHT__", IMPORT_PREFLIGHT.rstrip().replace("'", "'\"'\"'")
        )
    ).encode()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    sidecar = args.destination.with_name(args.destination.name + ".sha256")
    if args.source.resolve() in (args.destination.resolve(), sidecar.resolve()):
        parser.error("Never overwrite the source")
    if any(p.exists() or p.is_symlink() for p in (args.destination, sidecar)):
        parser.error("Destination/checksum already exists")
    result = prepare(args.source.read_bytes())
    with args.destination.open("xb") as out:
        out.write(result)
    checksum = hashlib.sha256(result).hexdigest()
    with sidecar.open("x") as out:
        out.write(checksum + "  " + args.destination.name + "\n")
    print(f"bytes={len(result)} lines={result.count(bytes([10]))} sha256={checksum}")


if __name__ == "__main__":
    main()
