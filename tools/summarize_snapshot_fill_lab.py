"""Offline merge of synthetic full-fill JSON and the owned PG cgroup observer."""

import bisect
import json
import sys
from pathlib import Path


def summarize(root):
    root = Path(root)
    pg = []
    for line in (root / "pg-memory.tsv").read_text().splitlines():
        fields = line.split()
        if len(fields) != 3:
            raise ValueError("invalid_pg_memory_sample")
        pg.append((float(fields[0]), int(fields[1]), int(fields[2])))
    if not pg or any(b[0] < a[0] for a, b in zip(pg, pg[1:])):
        raise ValueError("missing_or_unordered_pg_samples")
    result = dict(status="complete", ready_for_production=False, fills=[])
    first_id = None
    for name in ("first-fill.json", "retained-fill.json"):
        if not (root / name).exists():
            result["status"] = "incomplete"
            continue
        fill = json.loads((root / name).read_text())
        if name == "first-fill.json":
            if fill["prior"]["active_id"] is not None:
                result["status"] = "incomplete"
            first_id = (fill.get("result") or {}).get("id")
        elif fill["prior"]["active_id"] != first_id or first_id is None:
            result["status"] = "incomplete"
        begin, end = fill["cli_begin_boottime"], fill["cli_end_boottime"]
        window = [p for p in pg if begin <= p[0] <= end]
        times = [p[0] for p in pg]
        combined = []
        for sample in fill["resource_samples"]:
            used = sample["memory"]["cgroup_current_bytes"]
            if used is None:
                continue
            pos = bisect.bisect_left(times, sample.get("boottime", begin))
            candidates = pg[max(0, pos - 1) : pos + 1]
            if candidates:
                other = min(
                    candidates, key=lambda p: abs(p[0] - sample.get("boottime", begin))
                )
                if abs(other[0] - sample.get("boottime", begin)) <= 0.75:
                    combined.append(used + other[1])
        gaps = [b[0] - a[0] for a, b in zip(window, window[1:])]
        memory_complete = bool(window and combined) and max(gaps, default=0) <= 1.5
        backend_peak = fill["backend_memory"]["cgroup_peak_bytes"]
        pg_peak = max((p[2] for p in window), default=None)
        entry = dict(
            file=name,
            status=fill["status"],
            cli_wall_seconds=fill["cli_wall_seconds"],
            import_lock_seconds_lower=fill["metrics"].get("import_lock_seconds_lower"),
            import_lock_seconds_upper=fill["metrics"].get("import_lock_seconds_upper"),
            max_cursor_sql_seconds=fill["metrics"]["max_sql_seconds"],
            max_sql_stage=fill["metrics"]["max_sql_stage"],
            backend_cgroup_lifetime_peak_bytes=backend_peak,
            pg_cgroup_lifetime_peak_bytes=pg_peak,
            conservative_separate_lifetime_peak_sum_bytes=(
                None
                if pg_peak is None or backend_peak is None
                else pg_peak + backend_peak
            ),
            combined_memory_sampled_bytes=max(combined, default=None),
            pg_sampling_max_gap_seconds=max(gaps, default=None),
            disk_growth_sampled_bytes=fill["disk_growth_sampled_bytes"],
            exact_disk_peak_measured=False,
            memory_sampling_complete=memory_complete,
            analyze_executed=fill["analyze_executed"],
            previous_snapshot_retained=fill["previous_snapshot_retained"],
            next_due_valid=fill["next_due_valid"],
            totals=fill["totals"],
        )
        result["fills"].append(entry)
        if (
            fill["status"] != "complete"
            or not memory_complete
            or fill["observer_error"]
            or not fill.get("resource_measurement_complete", False)
        ):
            result["status"] = "incomplete"
    result["scope"] = (
        "PG kernel cgroup peak includes startup and preceding fill since seed restart; "
        "backend peak covers that job lifetime. Sum is conservative, not simultaneous RSS. "
        "Combined sampled estimate aligns clocks within0.75s; disk is sampled allocated PGDATA "
        "including TEMP, WAL, old/new snapshots, NOT an exact peak or a production resource guarantee."
    )
    return result


if __name__ == "__main__":
    root = Path(sys.argv[1])
    try:
        result = summarize(root)
    except (OSError, ValueError, KeyError, TypeError):
        result = dict(
            status="incomplete",
            error="invalid_or_missing_measurements",
            ready_for_production=False,
        )
    (root / "full-fill-summary.json").write_text(json.dumps(result, indent=2))
    print(json.dumps(result, indent=2))
    if result["status"] != "complete":
        raise SystemExit(1)
