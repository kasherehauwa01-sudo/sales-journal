"""Synthetic benchmark: isolated schema in explicitly supplied rfm_test DB only."""
import asyncio
import json
import os
import time
from datetime import date
from uuid import uuid4
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from app.models import Base
from app.services.calltrack_insights import client_insights
from app.services.rfm_snapshot import lookup_client, rebuild_snapshot

PRODUCT_SQL = """SELECT s.id,i.code,i.article,i.name,coalesce(sum(i.quantity),0)
FROM sales s LEFT JOIN sale_items i ON i.sale_id=s.id
WHERE lower(trim(s.client))=lower(trim('Client 42'))
AND s.sale_date BETWEEN '2025-10-15' AND '2026-10-15'
GROUP BY s.id,i.code,i.article,i.name"""

async def main():
    url=os.environ["RFM_TEST_DATABASE_URL"]
    if not url.endswith("/rfm_test"):
        raise SystemExit("Only the dedicated rfm_test database is allowed")
    schema="benchmark_rfm_"+uuid4().hex
    admin=create_async_engine(url)
    async with admin.begin() as conn:
        await conn.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine=create_async_engine(url,connect_args={"server_settings":{"search_path":schema}})
    sessions=async_sessionmaker(engine,expire_on_commit=False)
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
            await conn.execute(text("INSERT INTO import_batches(id,filename,file_size,status,total_rows,processed_rows,added_rows,duplicate_rows,skipped_rows,error_rows) VALUES(1,'benchmark',0,'completed',0,0,0,0,0,0)"))
            await conn.execute(text("""INSERT INTO sales(id,sale_date,document_number,client,department,total_amount,social,fingerprint,import_id)
SELECT g,'2025-04-15'::date+(g%550)::int,g::text,'Client '||(g%12000),'A',((g%99000)+1)::numeric/100,false,g::text,1
FROM generate_series(1,120000) g"""))
            await conn.execute(text("""INSERT INTO sale_items(id,sale_id,name,code,quantity)
SELECT g,((g-1)/4)+1,'Product '||(g%100),'p'||(g%100),0.125 FROM generate_series(1,480000) g"""))
            await conn.execute(text("CREATE INDEX ix_sales_rfm_client_date ON sales(client,sale_date)"))
            await conn.execute(text("CREATE INDEX ix_sales_rfm_date_client ON sales(sale_date,client)"))
            await conn.execute(text("ANALYZE sales")); await conn.execute(text("ANALYZE sale_items"))
            before=(await conn.execute(text("EXPLAIN (ANALYZE,BUFFERS) "+PRODUCT_SQL))).scalars().all()
            await conn.execute(text("CREATE INDEX ix_sales_rfm_normalized_client_date ON sales(lower(trim(client)),sale_date)"))
            await conn.execute(text("ANALYZE sales"))
            after=(await conn.execute(text("EXPLAIN (ANALYZE,BUFFERS) "+PRODUCT_SQL))).scalars().all()
        started=time.perf_counter()
        result=await rebuild_snapshot(engine,as_of=date(2026,10,15))
        rebuild_ms=(time.perf_counter()-started)*1000
        sqls=[]
        def record(conn,cursor,statement,params,context,many): sqls.append(statement)
        event.listen(engine.sync_engine,"before_cursor_execute",record)
        samples=[]
        for _ in range(20):
            async with sessions() as db:
                started=time.perf_counter(); await lookup_client(db,"Client 42")
                samples.append((time.perf_counter()-started)*1000)
        sqls.clear()
        async with sessions() as db:
            started=time.perf_counter(); await client_insights(db,"Client 42")
            endpoint_ms=(time.perf_counter()-started)*1000
        queries=len(sqls); endpoint_sql=list(sqls)
        event.remove(engine.sync_engine,"before_cursor_execute",record)
        await engine.dispose()
        async with sessions() as db:
            restarted=await client_insights(db,"Client 42")
            assert restarted.snapshot.id==result["id"]
        async with engine.connect() as conn:
            plan=(await conn.execute(text("""EXPLAIN (ANALYZE,BUFFERS)
SELECT c.* FROM rfm_snapshot_state st JOIN rfm_snapshots sn ON sn.id=st.active_snapshot_id
LEFT JOIN rfm_snapshot_clients c ON c.snapshot_id=sn.id AND c.client_key='client 42' WHERE st.id=1"""))).scalars().all()
        print(json.dumps({"sales":120000,"sale_items":480000,"clients":result["client_count"],
            "rebuild_ms":rebuild_ms,"lookup_median_ms":sorted(samples)[len(samples)//2],
            "lookup_min_ms":min(samples),"client_insights_ms":endpoint_ms,"client_insights_sql_count":queries,
            "restart_snapshot_preserved":True,"product_plan_before":before,"product_plan_after":after,
            "snapshot_plan":plan,"endpoint_sql":endpoint_sql},indent=2,ensure_ascii=False))
    finally:
        await engine.dispose()
        async with admin.begin() as conn:
            await conn.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        await admin.dispose()

if __name__=="__main__": asyncio.run(main())
