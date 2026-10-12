"""Opt-in synthetic benchmark: no DB, network, application startup or raw history."""

import argparse
import asyncio
from datetime import date
import hashlib
import json
import resource
import subprocess
import sys
import time
from types import SimpleNamespace
import tracemalloc
from app.services.seasonality import calculate, identity
from app.services.seasonality_storage import attach
from app.models import SeasonalityState, SeasonalityProfile


def observations():
    return [
        {
            "year": year,
            "month": month,
            "units": units,
            "positive_lines": 5,
            "first_sale": date(year, month, 1),
            "last_sale": date(year, month, 28),
        }
        for year in (2024, 2025)
        for month, units in enumerate([10] * 9 + [70, 90, 70], 1)
    ]


def rows(count):
    for index in range(count):
        yield {
            "key": f"code:{index:07}",
            "units_30": 1,
            "stock": 100,
            "status": "Кандидат",
            "reasons": ["Продажи падают"],
            "stop_factors": [],
        }


async def sample(stage, count, trace):
    if trace:
        tracemalloc.start()
    started = time.perf_counter()
    digest = hashlib.sha256()
    queries = 0
    max_batch = 0
    if stage == "calculate":
        for index in range(count):
            result = calculate(observations(), 2024)
            digest.update(json.dumps(result, sort_keys=True).encode())
    else:
        profile = calculate(observations(), 2024)

        class Database:
            async def get(self, model, key):
                return SimpleNamespace(
                    active_generation=1,
                    generation=1,
                    rebuild_required=False,
                    first_year=2024,
                    algorithm_version=1,
                    phase="idle",
                    source_revision=0,
                    job_revision=0,
                    coverage=(1 << 24) - 1,
                    last_success=None,
                )

            async def scalar(self, query):
                return None

            async def execute(self, query):
                nonlocal queries, max_batch
                queries += 1
                params = query.compile().params
                keys = next(
                    value for key, value in params.items() if key.startswith("identity")
                )
                max_batch = max(max_batch, len(keys))
                records = [
                    SimpleNamespace(
                        identity=key, product_key=key_to_product[key], data=profile
                    )
                    for key in keys
                ]
                return SimpleNamespace(all=lambda: records)

        records = list(rows(count))
        # Stub SQL driver storage stays outside the measured result lifetime in
        # real DB; here it stores only keys, never profiles for all products.
        key_to_product = {identity(row["key"]): row["key"] for row in records}
        await attach(records, Database(), {"min_stock": 1}, date(2026, 10, 12))
        for row in records:
            digest.update(json.dumps(row, sort_keys=True).encode())
    elapsed = (time.perf_counter() - started) * 1000
    peak = tracemalloc.get_traced_memory()[1] if trace else None
    if trace:
        tracemalloc.stop()
    return {
        "stage": stage,
        "products": count,
        "elapsed_ms": elapsed,
        "process_peak_rss_bytes": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        * 1024,
        "traced_peak_bytes": peak,
        "sql_queries": queries,
        "max_read_batch": max_batch,
        "checksum": digest.hexdigest(),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--products", type=int, default=27016)
    parser.add_argument("--stage", choices=["calculate", "attach"])
    parser.add_argument("--trace", action="store_true")
    args = parser.parse_args()
    if not args.run or not 1 <= args.products <= 80000:
        parser.error("Explicit --run, bounded --products required")
    if args.stage:
        print(json.dumps(asyncio.run(sample(args.stage, args.products, args.trace))))
        return
    result = []
    for stage in ("calculate", "attach"):
        for trace in (False, False, False, True):
            command = [
                sys.executable,
                __file__,
                "--run",
                "--products",
                str(args.products),
                "--stage",
                stage,
            ] + (["--trace"] if trace else [])
            result.append(json.loads(subprocess.check_output(command, text=True)))
    for stage in ("calculate", "attach"):
        assert (
            len({sample["checksum"] for sample in result if sample["stage"] == stage})
            == 1
        )
    print(json.dumps({"products": args.products, "samples": result}, indent=2))


if __name__ == "__main__":
    main()
