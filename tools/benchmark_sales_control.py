"""LOCAL synthetic SQL benchmark. Own temporary schema; no production URL allowed."""

import argparse
import json
import os
import resource
import sys
import time
import uuid
from pathlib import Path
import sqlalchemy as sa

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from app.services.sales_control import GRID


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--rows", type=int, default=1200000)
    a = p.parse_args()
    url = sa.engine.make_url(os.environ["SALES_BENCHMARK_DATABASE_URL"])
    if url.host not in ("127.0.0.1", "localhost") or not 10000 <= a.rows <= 2000000:
        p.error("Only localhost and bounded synthetic rows allowed")
    engine = sa.create_engine(url)
    schema = "sales_bench_" + uuid.uuid4().hex
    results = {"synthetic_rows": a.rows}
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as db:
        db.execute(sa.text("CREATE SCHEMA " + schema))
        try:
            db.execute(sa.text("SET search_path TO " + schema))
            db.execute(sa.text("SET statement_timeout='120s'"))
            db.execute(sa.text("SET max_parallel_workers_per_gather=0"))
            db.execute(sa.text("SET jit=off"))
            db.execute(sa.text("SET work_mem='2MB'"))
            db.execute(sa.text("SET maintenance_work_mem='16MB'"))
            db.execute(sa.text("SET max_parallel_maintenance_workers=0"))
            db.execute(
                sa.text(
                    "CREATE TABLE sales(department varchar(512) NOT NULL,sale_date date NOT NULL)"
                )
            )
            db.execute(
                sa.text(
                    "CREATE TABLE sales_control_exclusions(department varchar(512),sales_date date,PRIMARY KEY(department,sales_date))"
                )
            )
            db.execute(
                sa.text(
                    "INSERT INTO sales SELECT 'Synthetic '||(i%12), DATE '2024-01-01'+((i/12)%1000)::int FROM generate_series(1,:n) i"
                ),
                {"n": a.rows},
            )
            db.execute(sa.text("ANALYZE sales"))
            query = sa.text(
                "EXPLAIN (ANALYZE,BUFFERS,FORMAT JSON) "
                + GRID
                + "SELECT count(*) FROM missing"
            )
            params = dict(
                start="2026-09-27",
                end="2026-10-26",
                department="",
                search="",
                hidden=False,
            )
            for label in ("before", "after"):
                if label == "after":
                    t = time.perf_counter()
                    db.execute(
                        sa.text(
                            "CREATE INDEX CONCURRENTLY ix_sales_department_date_control ON sales(department,sale_date)"
                        )
                    )
                    results["index_build_ms"] = (time.perf_counter() - t) * 1000
                    results["index_bytes"] = db.scalar(
                        sa.text(
                            "SELECT pg_relation_size('ix_sales_department_date_control')"
                        )
                    )
                samples = []
                for _ in range(3):
                    plan = db.scalar(query, params)[0]
                    samples.append(plan["Execution Time"])
                results[label] = {
                    "execution_ms": samples,
                    "plan": plan,
                    "client_peak_rss_kib": resource.getrusage(
                        resource.RUSAGE_SELF
                    ).ru_maxrss,
                    "postgres_backend_context_bytes_after": db.scalar(
                        sa.text(
                            "SELECT CAST(sum(total_bytes) AS bigint) FROM pg_backend_memory_contexts"
                        )
                    ),
                }
                print(
                    json.dumps({"phase": label, "execution_ms": samples}),
                    file=sys.stderr,
                    flush=True,
                )
            print(json.dumps(results, indent=2))
        finally:
            db.rollback()
            with engine.connect().execution_options(
                isolation_level="AUTOCOMMIT"
            ) as cleanup:
                cleanup.execute(sa.text("DROP SCHEMA " + schema + " CASCADE"))
    engine.dispose()


if __name__ == "__main__":
    main()
