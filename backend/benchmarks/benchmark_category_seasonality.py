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


async def run(engine, factory):
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
    results = []
    for name, filters in [
        ("wide", {}),
        (
            "multiple_filters",
            dict(
                stores=["S1", "S2"],
                brands=["Бренд 1", "Бренд 2"],
                manufacturers=["Завод 1", "Завод 2"],
                manager=["Менеджер"],
                buyer_type=["Опт"],
            ),
        ),
    ]:
        async with factory() as db:
            for sql in REPORT_SETTINGS:
                await db.execute(text(sql))
            await db.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ"))
            data = Request(years=[2023, 2024, 2025], **filters)
            captured = []
            original = service.aggregate

            async def measured(*args):
                r, q = await original(*args)
                captured.append(q)
                return r, q

            service.aggregate = measured
            before = memory_snapshot()
            started = time.perf_counter()
            try:
                result = await service.build(db, data, date(2026, 11, 13))
            finally:
                service.aggregate = original
            elapsed = (time.perf_counter() - started) * 1000
            after = memory_snapshot()
            from sqlalchemy.dialects import postgresql
            compiled = captured[0].compile(dialect=postgresql.dialect(paramstyle="named"), compile_kwargs={"render_postcompile": True})
            plan = (await db.execute(text("EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) " + str(compiled)), compiled.params)).scalar_one()[0]
            results.append(
                dict(
                    scope=name,
                    sales=120000,
                    items=480000,
                    catalog_products=12000,
                    years=[2023, 2024, 2025],
                    rows=len(result["rows"]),
                    full_ms=elapsed,
                    rss_before=before,
                    rss_after=after,
                    plan=plan,
                )
            )
            assert result["rows"]
            await db.rollback()
    output = Path("docs/benchmarks/category_seasonality.json")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(results, ensure_ascii=False, indent=2))
    return results
