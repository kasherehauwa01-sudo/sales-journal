import argparse, importlib.util, json, resource, time, tracemalloc
from datetime import date

parser = argparse.ArgumentParser(
    description="Opt-in model-only benchmark, no DB or network"
)
parser.add_argument("--run", action="store_true")
parser.add_argument(
    "--algorithm-file", default="backend/app/services/seasonality.py", dest="path"
)
parser.add_argument("--products", type=int, default=27016)
parser.add_argument("--years", type=int, default=10)
parser.add_argument("--trace", action="store_true")
args = parser.parse_args()
if not args.run or not 1 <= args.products <= 80000 or not 1 <= args.years <= 500:
    parser.error(
        "Explicit --run and bounded synthetic sample required; the model has no year limit"
    )
spec = importlib.util.spec_from_file_location("sample", args.path)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
rows = [
    {
        "year": y,
        "month": m,
        "units": v * (y - 2026 + args.years + 1),
        "positive_lines": 5,
    }
    for y in range(2026 - args.years, 2026)
    for m, v in enumerate([10] * 9 + [70, 90, 70], 1)
]
if args.trace:
    tracemalloc.start()
started = time.perf_counter()
for i in range(args.products):
    result = module.calculate_history(rows, date(2026, 10, 13))
    assert result["pattern"] == "confirmed" and result["years"] == list(
        range(2026 - args.years, 2026)
    )
print(
    json.dumps(
        {
            "algorithm_version": module.ALGORITHM_VERSION,
            "products": args.products,
            "years": args.years,
            "elapsed_ms": (time.perf_counter() - started) * 1000,
            "process_peak_rss_bytes": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
            * 1024,
            "traced_peak_bytes": (
                tracemalloc.get_traced_memory()[1] if args.trace else None
            ),
            "profile_json_bytes": len(json.dumps(result, ensure_ascii=False).encode()),
        }
    )
)
