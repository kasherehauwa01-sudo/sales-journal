"""Isolated source-history benchmark. NEVER connect to production or non-local DB."""
import asyncio
import json
import os
import time
from uuid import uuid4
from sqlalchemy import text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool
from app.models import Base, ImportBatch, Sale
from app.services.calltrack_identity_history import scan

async def main():
    raw = os.environ["CALLTRACK_HISTORY_TEST_DATABASE_URL"]
    url = make_url(raw)
    assert url.host == "127.0.0.1" and url.database == "calltrack_history_test", "Test DB only"
    size = int(os.environ.get("CALLTRACK_HISTORY_BENCH_ROWS", "100000"))
    assert 1000 <= size <= 1000000
    schema = "bench_" + uuid4().hex
    admin = create_async_engine(raw, poolclass=NullPool)
    engine = create_async_engine(raw, poolclass=NullPool,
        connect_args={"server_settings": {"search_path":schema}})
    try:
        async with admin.begin() as conn:
            await conn.execute(text("CREATE SCHEMA " + schema))
        async with engine.begin() as conn:
            await conn.run_sync(lambda c: Base.metadata.create_all(c, tables=[
                ImportBatch.__table__, Sale.__table__]))
            await conn.execute(text("""
                INSERT INTO import_batches(id, filename, file_size, status, total_rows, processed_rows, added_rows, duplicate_rows, skipped_rows, error_rows)
                VALUES (1, 'synthetic benchmark', 1, 'completed', 0, 0, 0, 0, 0, 0)
            """))
            # Populate real Sale header columns only with synthetic values.
            await conn.execute(text("""
                INSERT INTO sales(id,sale_date,document_number,client,department,
                                  total_amount,fingerprint,import_id,social)
                SELECT i, DATE '2026-01-01' + (i % 300),
                       'R-' || i,
                       CASE WHEN i % 100 = 0 THEN 'ООО РАДУГА' ELSE 'Клиент ' || i END,
                       'Авиаторов', 10.20, 'test-' || i, 1, false
                FROM generate_series(1,:count) i
            """), {"count":size})
            await conn.execute(text("ANALYZE sales"))
        async with engine.connect() as conn:
            plan = await conn.execute(text("""
               EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON)
               SELECT id FROM sales
               WHERE department='Авиаторов'
               AND lower(trim(regexp_replace(client,
                   '[\\t\\n\\v\\f\\r \u0085\u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000]+',
                   ' ', 'g')))='ооо радуга'
               ORDER BY id
            """))
            data=plan.scalar()
            if isinstance(data,str): data=json.loads(data)
            print("EXPLAIN_summary",json.dumps({
                "execution_ms":data[0]["Execution Time"],
                "plan":data[0]["Plan"]["Node Type"]},ensure_ascii=False))
        async with engine.connect() as conn:
            from sqlalchemy.ext.asyncio import AsyncSession
            async with AsyncSession(bind=conn) as db:
                started=time.perf_counter()
                summary,items,next_after = await scan(db, "ооо радуга", after=0, page_size=50)
                elapsed=time.perf_counter()-started
                assert summary["sale_count"] == size//100
                assert len(items)==50 and next_after is not None
                print("BENCH_RESULT",json.dumps({"total_rows":size,
                    "matched_sales":summary["sale_count"],"scan_seconds":round(elapsed,3),
                    "page_items":len(items),"hash_prefix":summary["history_hash"][:12]}))
                assert elapsed < 60, "Full history request exceeded existing timeout"
    finally:
        await engine.dispose()
        async with admin.begin() as c:
            await c.execute(text("DROP SCHEMA IF EXISTS "+schema+" CASCADE"))
        await admin.dispose()

if __name__=="__main__": asyncio.run(main())
