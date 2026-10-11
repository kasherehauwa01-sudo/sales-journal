"""Opt-in synthetic benchmark; refuses production hosts and database names.

PYTHONPATH=backend LOCAL_CATALOG_BENCH_DATABASE_URL=postgresql+asyncpg://...@127.0.0.1:55433/catalog_perf_test \
  python backend/benchmarks/benchmark_local_catalog.py --run

Each measurement is a fresh process. Timings and tracemalloc runs are separate.
"""

import argparse
import asyncio
import gc
import hashlib
import json
import os
from pathlib import Path
import resource
import statistics
import subprocess
import sys
import time
import tracemalloc
import uuid
from datetime import datetime, timezone
from itertools import islice

from sqlalchemy import event, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from app.models import CatalogLookup, CatalogProduct
from app.services import catalog_attributes as attributes
from app.services.product_analytics_runtime import ensure_headroom


async def before(rows, db):
    """Exact pre-optimization local_catalog loop from main 0a4330a."""
    iterator = iter(rows)
    result = {}
    while batch := list(islice(iterator, 250)):
        ensure_headroom()
        keys = {
            attributes.identity(row.get("code"), row.get("article")) for row in batch
        }
        records = (
            await db.execute(
                select(CatalogLookup, CatalogProduct)
                .outerjoin(
                    CatalogProduct,
                    CatalogProduct.product_id == CatalogLookup.product_id,
                )
                .where(CatalogLookup.identity.in_(keys))
            )
        ).all()
        mapped = {lookup.identity: (lookup, product) for lookup, product in records}
        for row in batch:
            key = row["key"]
            pair = mapped.get(
                attributes.identity(row.get("code"), row.get("article")), (None, None)
            )
            info = attributes.projection(*pair)
            previous = result.get(key)
            if previous is not None and (
                previous.get("product_id"),
                previous["catalog_status"],
            ) != (info.get("product_id"), info["catalog_status"]):
                info = {"catalog_status": "ambiguous", "catalog_synced_at": None}
            result[key] = info
    return result


def identifiers(index):
    return f"000{index:06d}", f"ART-{index % 1100:04d}"


def input_rows(count, periods):
    for _ in range(periods):
        for index in range(count):
            code, article = identifiers(index)
            yield {
                "key": "collision:shared" if index in {1, 2} else "code:" + code,
                "code": code,
                "article": article,
            }


def checksum(result):
    digest = hashlib.sha256()
    # Stream tiny individual projections: never serialize the entire result.
    for key, info in result.items():
        digest.update(
            json.dumps(
                [key, info], default=str, sort_keys=True, ensure_ascii=False
            ).encode()
        )
    return digest.hexdigest()


def url():
    value = make_url(os.environ["LOCAL_CATALOG_BENCH_DATABASE_URL"])
    if (
        value.host not in {"localhost", "127.0.0.1"}
        or value.database != "catalog_perf_test"
    ):
        raise ValueError("Only disposable localhost catalog_perf_test is allowed")
    return value


def engine_for(schema):
    return create_async_engine(
        url(),
        pool_size=1,
        max_overflow=0,
        connect_args={
            "server_settings": {"search_path": schema, "statement_timeout": "10000"}
        },
    )


async def prepare(schema, count):
    admin = create_async_engine(url())
    async with admin.begin() as conn:
        await conn.execute(text(f'CREATE SCHEMA "{schema}"'))
    await admin.dispose()
    engine = engine_for(schema)
    stamp = datetime(2026, 9, 30, tzinfo=timezone.utc)
    try:
        async with engine.begin() as conn:
            await conn.run_sync(lambda c: CatalogProduct.__table__.create(c))
            await conn.run_sync(lambda c: CatalogLookup.__table__.create(c))
        for start in range(0, count, 1000):
            products, lookups = [], []
            for index in range(start, min(start + 1000, count)):
                code, article = identifiers(index)
                products.append(
                    {
                        "product_id": index + 1,
                        "code": code,
                        "article": article,
                        "brand": "Бренд " + str(index % 71),
                        "manufacturer": "Завод " + str(index % 83),
                        "category": "Категория " + str(index % 13),
                        "subcategory": "Подкатегория " + str(index % 29),
                        "category_id": index % 13,
                        "legacy_category": "Раздел " + str(index % 31),
                        "material": None if index % 29 == 0 else "Фарфор",
                        "horeca": index % 11 == 0,
                        "image_url": (
                            f"https://images.example.test/000{index:06d}.jpg"
                            if index % 23 == 0
                            else None
                        ),
                        "source_updated_at": stamp,
                        "synced_at": stamp,
                    }
                )
                if index % 17 == 0:
                    continue
                status = {
                    3: "not_found",
                    5: "ambiguous",
                    7: "pending",
                    9: "invalid",
                }.get(index % 53, "matched")
                lookups.append(
                    {
                        "identity": attributes.identity(code, article),
                        "code": code,
                        "article": article,
                        "product_id": index + 1,
                        "status": status,
                        "matched_by": "code" if status == "matched" else None,
                        "cycle": 1,
                        "checked_at": stamp,
                        "synced_at": stamp,
                    }
                )
            async with engine.begin() as conn:
                await conn.execute(CatalogProduct.__table__.insert(), products)
                if lookups:
                    await conn.execute(CatalogLookup.__table__.insert(), lookups)
    finally:
        await engine.dispose()


async def measure(args):
    engine = engine_for(args.schema)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    queries = 0

    def count(*_):
        nonlocal queries
        queries += 1

    try:
        async with factory() as db:
            await attributes.begin_catalog_snapshot(db)
            await db.execute(text("SELECT 1"))
            event.listen(engine.sync_engine, "before_cursor_execute", count)
            gc.collect()
            rss_before = int(
                Path("/proc/self/statm").read_text().split()[1]
            ) * os.sysconf("SC_PAGE_SIZE")
            if args.trace:
                tracemalloc.start()
            function = before
            if args.variant != "before":
                attributes.READ_BATCH_SIZE = int(args.variant)
                function = attributes.local_catalog
            started = time.perf_counter()
            result = await function(input_rows(args.rows, args.periods), db)
            elapsed = (time.perf_counter() - started) * 1000
            peak_rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss * 1024
            traced_peak = tracemalloc.get_traced_memory()[1] if args.trace else None
            if args.trace:
                tracemalloc.stop()
            event.remove(engine.sync_engine, "before_cursor_execute", count)
            return {
                "variant": args.variant,
                "traced": args.trace,
                "elapsed_ms": elapsed,
                "sql_queries": queries,
                "input_rows": args.rows * args.periods,
                "unique_identities": args.rows,
                "result_keys": len(result),
                "rss_before_bytes": rss_before,
                "process_peak_rss_bytes": peak_rss,
                "tracemalloc_peak_bytes": traced_peak,
                "checksum": checksum(result),
            }
    finally:
        await engine.dispose()


async def clean(schema):
    engine = create_async_engine(url())
    try:
        async with engine.begin() as conn:
            await conn.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
    finally:
        await engine.dispose()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", action="store_true")
    parser.add_argument("--rows", type=int, default=36706)
    parser.add_argument("--periods", type=int, default=2)
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument("--variant", choices=("before", "500", "1000"))
    parser.add_argument("--schema")
    parser.add_argument("--trace", action="store_true")
    args = parser.parse_args()
    if not args.run:
        parser.error(
            "Benchmark is opt-in: pass --run and a disposable local database URL"
        )
    if (
        not 1 <= args.rows <= 50000
        or not 1 <= args.periods <= 2
        or not 1 <= args.repeats <= 5
    ):
        parser.error("Synthetic limits exceeded")
    if args.variant:
        if (
            not args.schema
            or not args.schema.startswith("catalog_bench_")
            or not args.schema.replace("_", "").isalnum()
        ):
            parser.error("Invalid synthetic schema")
        print(json.dumps(asyncio.run(measure(args))))
        return
    schema = "catalog_bench_" + uuid.uuid4().hex
    samples = []
    try:
        asyncio.run(prepare(schema, args.rows))
        for variant in ("before", "500", "1000"):
            for repeat in range(args.repeats + 1):
                command = [
                    sys.executable,
                    __file__,
                    "--run",
                    "--variant",
                    variant,
                    "--schema",
                    schema,
                    "--rows",
                    str(args.rows),
                    "--periods",
                    str(args.periods),
                ]
                if repeat == args.repeats:
                    command.append("--trace")
                sample = json.loads(subprocess.check_output(command, text=True))
                samples.append(sample)
        assert (
            len({sample["checksum"] for sample in samples}) == 1
        ), "Restored projections differ"
        summary = []
        for variant in ("before", "500", "1000"):
            timings = [
                s for s in samples if s["variant"] == variant and not s["traced"]
            ]
            memory = next(s for s in samples if s["variant"] == variant and s["traced"])
            summary.append(
                {
                    "variant": variant,
                    "median_ms": statistics.median(s["elapsed_ms"] for s in timings),
                    "min_ms": min(s["elapsed_ms"] for s in timings),
                    "max_ms": max(s["elapsed_ms"] for s in timings),
                    "sql_queries": timings[0]["sql_queries"],
                    "peak_rss_bytes": max(s["process_peak_rss_bytes"] for s in timings),
                    "tracemalloc_peak_bytes": memory["tracemalloc_peak_bytes"],
                }
            )
        print(json.dumps({"summary": summary, "samples": samples}, indent=2))
    finally:
        asyncio.run(clean(schema))


if __name__ == "__main__":
    main()
