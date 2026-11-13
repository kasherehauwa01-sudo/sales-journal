"""Isolated localhost PostgreSQL benchmark: indexed equality vs legacy OR join."""

import json, os, resource, time, uuid
import sqlalchemy as sa


def main():
    url = sa.engine.make_url(os.environ["CATEGORY_BENCHMARK_DATABASE_URL"])
    assert url.host in ("localhost", "127.0.0.1")
    engine = sa.create_engine(url)
    schema = "category_join_" + uuid.uuid4().hex
    out = {"items": 50000, "mapping_rows": 10000, "jit": False, "work_mem": "4MB"}
    with engine.connect().execution_options(isolation_level="AUTOCOMMIT") as db:
        db.execute(sa.text("CREATE SCHEMA " + schema))
        try:
            db.execute(sa.text("SET search_path TO " + schema))
            for sql in (
                "SET jit=off",
                "SET work_mem='4MB'",
                "SET maintenance_work_mem='16MB'",
                "SET max_parallel_maintenance_workers=0",
                "SET max_parallel_workers_per_gather=0",
                "SET statement_timeout='60s'",
                "CREATE TABLE sale_items(code text,article text,quantity numeric,actual_price numeric)",
                "CREATE TEMP TABLE category_map(code text,article text,category text,lookup_key text)",
                "INSERT INTO category_map SELECT CASE WHEN i%4=0 THEN NULL ELSE lpad(i::text,7,'0') END,'a'||i,'Category '||(i%20),CASE WHEN i%4=0 THEN 'a:a'||i ELSE 'c:'||lpad(i::text,7,'0') END FROM generate_series(1,10000)i",
                "INSERT INTO sale_items SELECT CASE WHEN (i%10000+1)%4=0 THEN '' ELSE ' '||lpad((i%10000+1)::text,7,'0')||' ' END,'a'||(i%10000+1),1.25,12.34 FROM generate_series(1,50000)i",
                "CREATE INDEX ON category_map(code)",
                "CREATE INDEX ON category_map(article)",
                "CREATE INDEX ON category_map(lookup_key)",
                "ANALYZE sale_items",
                "ANALYZE category_map",
            ):
                db.execute(sa.text(sql))
            joins = {
                "before": "(m.code IS NOT NULL AND lower(trim(s.code))=m.code) OR ((s.code IS NULL OR trim(s.code)='') AND m.code IS NULL AND lower(trim(s.article))=m.article)",
                "after": "CASE WHEN s.code IS NOT NULL AND trim(s.code)<>'' THEN 'c:'||lower(trim(s.code)) ELSE 'a:'||lower(trim(s.article)) END=m.lookup_key",
            }
            reference = None
            for name, join in joins.items():
                query = (
                    "SELECT m.category,sum(s.quantity*s.actual_price),sum(s.quantity) FROM sale_items s LEFT JOIN category_map m ON "
                    + join
                    + " GROUP BY m.category ORDER BY m.category"
                )
                result = list(db.execute(sa.text(query)))
                if reference is None:
                    reference = result
                else:
                    assert result == reference, "Result mismatch"
                plans = [
                    db.scalar(sa.text("EXPLAIN(ANALYZE,BUFFERS,FORMAT JSON) " + query))[
                        0
                    ]
                    for _ in range(3)
                ]
                out[name] = {
                    "execution_ms": [p["Execution Time"] for p in plans],
                    "plan": plans[-1],
                    "python_peak_rss_kib": resource.getrusage(
                        resource.RUSAGE_SELF
                    ).ru_maxrss,
                }
            print(json.dumps(out, indent=2))
        finally:
            db.rollback()
            db.execute(sa.text("DROP SCHEMA " + schema + " CASCADE"))
    engine.dispose()


if __name__ == "__main__":
    main()
