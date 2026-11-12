"""Pure synthetic RFM scoring benchmark; never reads DB/API/env credentials."""

import argparse
from datetime import date
import hashlib
import json
import resource
import subprocess
import sys
import time
import tracemalloc
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def child(variant, count, baseline):
    if variant == "before":
        source = subprocess.check_output(
            ["git", "show", baseline + ":backend/app/services/rfm.py"],
            cwd=ROOT,
            text=True,
        )
        module = types.ModuleType("baseline_rfm")
        sys.modules[module.__name__] = module
        exec(compile(source, "reviewed_baseline_rfm", "exec"), module.__dict__)
    else:
        sys.path.insert(0, str(ROOT / "backend"))
        from app.services import rfm as module
    end = date(2026, 11, 12)
    rows = [
        dict(
            client_key=str(i),
            recency_days=i % 365,
            frequency=i % 101 + 1,
            monetary=i + 0.13,
            first_purchase=date(2020, 1, 1),
            cycle_overdue=(i % 365) / 90,
        )
        for i in range(count)
    ]
    tracemalloc.start()
    started = time.perf_counter()
    result, boundaries = module.enrich(rows, end, module.SegmentSettings())
    elapsed = time.perf_counter() - started
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    digest = hashlib.sha256(
        json.dumps([result, boundaries], sort_keys=True, default=str).encode()
    ).hexdigest()
    return dict(
        variant=variant,
        clients=count,
        elapsed_ms=round(elapsed * 1000, 3),
        scoring_peak_traced_bytes=peak,
        process_peak_rss_bytes=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        * 1024,
        result_sha256=digest,
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--clients", type=int, default=40400)
    parser.add_argument("--baseline-ref", default="9908c49")
    parser.add_argument("--child", choices=["before", "after"])
    args = parser.parse_args()
    if not 1 <= args.clients <= 100000:
        parser.error("Bounded synthetic dataset required")
    if args.child:
        print(json.dumps(child(args.child, args.clients, args.baseline_ref)))
        return
    results = []
    for variant in ("before", "after"):
        result = subprocess.run(
            [
                sys.executable,
                str(Path(__file__).resolve()),
                "--clients",
                str(args.clients),
                "--baseline-ref",
                args.baseline_ref,
                "--child",
                variant,
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        results.append(json.loads(result.stdout))
    if results[0]["result_sha256"] != results[1]["result_sha256"]:
        raise RuntimeError("Scoring results differ")
    print(json.dumps({"results": results, "identical": True}, indent=2))


if __name__ == "__main__":
    main()
