"""Opt-in, synthetic only: no database, external HTTP, or application startup."""

import argparse
import asyncio
from datetime import date
import hashlib
import json
import resource
import subprocess
import sys
import time
import tracemalloc
import tempfile
from pathlib import Path
from app.services import product_analytics_runtime as runtime

from app.routers import promotion_candidates_report as current
from app.services import vrcatalog
from app.services.product_analytics_cache import _bounded_size
from app.services.product_analytics_runtime import heavy_operation, memory_snapshot
from benchmarks import promotion_baseline as before


def sales_rows(count):
    for i in range(count):
        yield {
            "key": f"code:{i:07}",
            "code": f"{i:07}",
            "article": f"A{i:07}",
            "name": f"Товар {i}",
            "units": 60.0,
            "revenue": float(i + 60),
            "checks": 3,
            "average_price": 10.0,
            "units_30": float(i % 5),
            "units_previous_30": 10.0,
            "units_60_90": 15.0,
            "revenue_30": 40.0,
            "revenue_previous_30": 100.0,
            "first_sale": date(2026, 1, 1),
            "last_sale": date(2026, 8, 1),
            "stores": 2,
            "analysis_days": 273,
        }


def catalog_payload(products):
    # Rich batch-info response: many nested fields not used by this report.
    return [
        {
            "code": p["code"],
            "article": p["article"],
            "brand": "Бренд",
            "manufacturer": "Завод",
            "category": f"Категория {int(p['code']) % 12}",
            "stocks": [{"quantity": 20, "warehouse": "Авиаторов Зал+Склад" if j == 0 else f"Склад {j}"} for j in range(5)],
            "prices": [{"price": j, "name": f"Цена {j}"} for j in range(8)],
            "properties": [
                {"name": f"Свойство {j}", "value": f"{p['code']}-{j}-" + "x" * 100}
                for j in range(16)
            ],
            "image_url": "https://catalog.test/" + p["code"] + ".jpg",
        }
        for p in products
    ]


async def sample(variant, count, trace, excel=False):
    module = before if variant == "before" else current
    calls = 0

    async def sales(*args):
        return list(sales_rows(count))

    async def clients(*args):
        return None

    def integration(payload):
        nonlocal calls
        calls += 1
        return catalog_payload(payload["products"])

    module._sales = sales
    module.get_sales_filter_clients = clients
    vrcatalog._integration_batch = integration
    vrcatalog.catalog_info_cache.clear()
    vrcatalog._catalog_info_sizes.clear()
    vrcatalog._catalog_info_bytes = 0
    request = current.Request(
        use_seasonality=False, date_from=date(2026, 1, 1), date_to=date(2026, 9, 30)
    )
    if trace:
        tracemalloc.start()
    rss_before = memory_snapshot()["rss_bytes"]
    started = time.perf_counter()
    async with heavy_operation("synthetic_benchmark"):
        rows, available = await module._dataset(request, None)
        if excel:
            response = await runtime.run_cpu(module._excel, rows)
    elapsed = (time.perf_counter() - started) * 1000
    peak_rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024
    traced_peak = tracemalloc.get_traced_memory()[1] if trace else None
    if trace:
        tracemalloc.stop()
    digest = hashlib.sha256()
    for row in rows:
        digest.update(
            json.dumps(
                {key: value for key, value in row.items() if key != "seasonality"},
                ensure_ascii=False,
                sort_keys=True,
                default=str,
            ).encode()
        )
    return {
        "variant": variant,
        "excel": excel,
        "rows": len(rows),
        "available": available,
        "http_batches": calls,
        "elapsed_ms": elapsed,
        "rss_before_bytes": rss_before,
        "process_peak_rss_bytes": peak_rss,
        "traced_peak_bytes": traced_peak,
        "cache_charged_bytes": vrcatalog._catalog_info_bytes,
        "cache_deep_bytes": _bounded_size(
            (vrcatalog.catalog_info_cache, vrcatalog._catalog_info_sizes), 1024**3
        ),
        "checksum": digest.hexdigest(),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--rows", type=int, default=5000)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--variant", choices=["before", "after"])
    parser.add_argument("--trace", action="store_true")
    parser.add_argument("--no-trace", action="store_true")
    parser.add_argument("--excel", action="store_true")
    args = parser.parse_args()
    if not args.run or not 1 <= args.rows <= 30000 or not 1 <= args.repeats <= 3:
        parser.error("Explicit --run and bounded --rows/--repeats required")
    if args.variant:
        # Isolate the synthetic admission file from other local test processes.
        with tempfile.TemporaryDirectory(prefix="promotion-benchmark-") as directory:
            runtime.LOCK_PATH = str(Path(directory) / "analytics.lock")
            print(
                json.dumps(
                    asyncio.run(sample(args.variant, args.rows, args.trace, args.excel))
                )
            )
        return
    samples = []
    for variant in ("before", "after"):
        for trace in [False] * args.repeats + ([] if args.no_trace else [True]):
            command = [
                sys.executable,
                __file__,
                "--run",
                "--rows",
                str(args.rows),
                "--variant",
                variant,
            ]
            if trace:
                command.append("--trace")
            if args.excel:
                command.append("--excel")
            samples.append(json.loads(subprocess.check_output(command, text=True)))
    assert len({item["checksum"] for item in samples}) == 1
    print(json.dumps({"rows": args.rows, "samples": samples}, indent=2))


if __name__ == "__main__":
    main()
