"""Opt-in local synthetic seed; called ONLY in an owned isolated test schema.
Run tests/test_category_seasonality_postgres.py with CATEGORY_SEASON_BENCHMARK=1.
No production URL or application database is used by this benchmark.
"""

import time
import json
from pathlib import Path
from datetime import date
from sqlalchemy import text
from app.services.catalog_attributes import identity
from app.models import CatalogLookup
from app.services import category_seasonality as service
from app.routers.category_seasonality_report import Request
from app.services.product_analytics_runtime import memory_snapshot
from app.services.report_execution import REPORT_SETTINGS


async def run(engine, factory, cold_reset=None):
    async with engine.begin() as db:
        await db.execute(
            text(
                "INSERT INTO import_batches(id,filename,file_size,status,total_rows,processed_rows,added_rows,duplicate_rows,skipped_rows,error_rows) VALUES(500,'synthetic-benchmark.html',1,'completed',0,0,0,0,0,0)"
            )
        )
        await db.execute(
            text(
                "INSERT INTO catalog_products(product_id,code,article,category_id,category,subcategory,legacy_category,brand,manufacturer,horeca,source_updated_at,synced_at) SELECT p,lpad(p::text,6,'0'),'A'||p,1+(p%20),'Группа '||(p%20),'Раздел '||(p%120),'Раздел '||(p%120),'Бренд '||(p%5),'Завод '||(p%7),false,now(),now() FROM generate_series(1,12000) p"
            )
        )
        # Exact Python normalization retained; no pgcrypto or schema extension.
        for start in range(1, 12001, 500):
            await db.execute(
                CatalogLookup.__table__.insert(),
                [
                    dict(
                        identity=identity(f"{p:06}", f"A{p}"),
                        code=f"{p:06}",
                        article=f"a{p}",
                        product_id=p,
                        status="matched",
                        matched_by="code",
                        cycle=0,
                    )
                    for p in range(start, min(start + 500, 12001))
                ],
            )
        await db.execute(
            text(
                "INSERT INTO sales(id,sale_date,document_number,department,client,total_amount,fingerprint,import_id) SELECT g,date '2023-01-01'+((g-1)%1096)::int,'DOC'||g,'S'||(g%12),'Клиент '||(g%10),100,'benchmark-'||g,500 FROM generate_series(1,120000) g"
            )
        )
        await db.execute(
            text(
                "INSERT INTO sale_items(sale_id,code,article,name,quantity,actual_price) SELECT g,lpad(p::text,6,'0'),'A'||p,'Товар '||p,1+(g%5),20 FROM generate_series(1,120000) g CROSS JOIN generate_series(1,4) j CROSS JOIN LATERAL (SELECT ((g*4+j)%12000)+1 AS p) z"
            )
        )
        await db.execute(
            text(
                "INSERT INTO import_control_receipts(import_id,filename,status,period_method,period_start,period_end,departments) VALUES(500,'synthetic-benchmark.html','success','DECLARED_REPORT_PERIOD','2023-01-01','2025-12-31',CAST(:departments AS json))"
            ),
            dict(departments=json.dumps(["S" + str(i) for i in range(12)])),
        )
        for table in [
            "sales",
            "sale_items",
            "catalog_products",
            "catalog_lookups",
            "import_control_receipts",
        ]:
            await db.execute(text("ANALYZE " + table))

    from datetime import datetime, timezone
    from app.services import category_seasonality_snapshot as snapshots

    before = memory_snapshot()
    started = time.perf_counter()
    published = await snapshots.rebuild(
        engine, manual=True, clock=datetime(2026, 11, 13, tzinfo=timezone.utc)
    )
    build_ms = (time.perf_counter() - started) * 1000
    after = memory_snapshot()
    assert published["status"] == "published"
    async with factory() as db:
        size = await db.scalar(
            text(
                "SELECT sum(pg_total_relation_size(c.oid)) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname=current_schema() AND c.relname LIKE 'category_season_%' AND c.relkind='r'"
            )
        )
        counts = dict(
            (
                await db.execute(
                    text(
                        "SELECT 'facts',count(*) FROM category_season_facts UNION ALL SELECT 'memberships',count(*) FROM category_season_memberships UNION ALL SELECT 'coverage',count(*) FROM category_season_coverage"
                    )
                )
            ).all()
        )
    results = []
    for scope, params in [
        ("wide", Request()),
        (
            "multiple_filters",
            Request(
                stores=["S1", "S2"],
                brands=["Бренд 1", "Бренд 2"],
                manufacturers=["Завод 1", "Завод 2"],
                manager=["M"],
                buyer_type=["Опт"],
            ),
        ),
    ]:
        # Optional reset is injected only by an explicitly opted-in owned local
        # fixture. It clears PostgreSQL shared buffers, not OS filesystem caches.
        if cold_reset:
            await cold_reset()
        for cache in ["postgres_cold" if cold_reset else "first_read", "warm"]:
            async with factory() as db:
                await snapshots.settings_sql(db)
                started = time.perf_counter()
                result = await snapshots.read_report(db, params)
                elapsed = (time.perf_counter() - started) * 1000
                results.append(
                    dict(
                        scope=scope,
                        cache=cache,
                        read_ms=elapsed,
                        categories=len(result["rows"]),
                    )
                )
                assert result["rows"]
    measured = dict(
        sales=120000,
        items=480000,
        products=12000,
        build_ms=build_ms,
        build_result=published,
        relations_bytes=int(size),
        row_counts=counts,
        rss_before=before,
        rss_after=after,
        reads=results,
    )
    output = Path("docs/benchmarks/category_seasonality_snapshot.json")
    output.write_text(json.dumps(measured, ensure_ascii=False, indent=2))
    return measured
