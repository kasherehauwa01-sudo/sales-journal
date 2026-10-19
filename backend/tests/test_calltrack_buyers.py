import asyncio
import os
from datetime import date
from types import SimpleNamespace

import pytest
from sqlalchemy import event, text
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from pydantic import ValidationError

from app.schemas.calltrack import CalltrackBuyersRequest
from app.services import calltrack_buyers as service


def test_query_uses_sales_grouping_and_scoped_bound_names():
    q = service.buyers_query(manager="One", managers={"alpha": "One", "beta": "Two"}, search="+7 (989) 282-98-42")
    compiled = q.compile(dialect=postgresql.dialect())
    sql = str(compiled)
    assert "GROUP BY" in sql and "HAVING" in sql and "ANY" in sql
    assert compiled.params["buyer_scope_names"] == ["alpha"]
    assert "sale_items" not in sql


def test_invalid_paging_and_filters():
    for kwargs in [{"page_size":101}, {"page":0}, {"activity":"forged"}, {"client_key":""}]:
        with pytest.raises(ValidationError):
            CalltrackBuyersRequest(**kwargs)


def test_manager_key_preserves_exact_name_tokens():
    assert service.manager_key("One Two") == service.manager_key(" two ONE ")
    assert service.manager_key("One") != service.manager_key("Someone")


@pytest.mark.skipif(not os.getenv("TEST_BUYERS_DATABASE_URL"), reason="Disposable PostgreSQL fixture only")
def test_real_postgres_buyer_paging_search_scope_activity_and_no_n_plus_one(monkeypatch):
    async def run():
        engine = create_async_engine(os.environ["TEST_BUYERS_DATABASE_URL"])
        queries=[]
        event.listen(engine.sync_engine,"before_cursor_execute",lambda c,cu,s,p,ctx,m:queries.append(s))
        async with engine.connect() as conn:
            assert (await conn.scalar(text("SELECT current_database()"))) == "calltrack_buyers_test"
            await conn.execute(text("CREATE TEMP TABLE sales(id BIGINT PRIMARY KEY,client TEXT,phone TEXT,sale_date DATE)"))
            await conn.execute(text("INSERT INTO sales VALUES(1,'Alpha','+7 (989) 282-98-42','2026-10-18'),(2,'Alpha','+79892829842','2026-10-19'),(3,'Beta','89990000000','2026-10-17'),(4,'Shared A','89991112233','2026-10-01'),(5,'Shared B','89991112233','2026-10-02'),(6,'','89991112244','2026-10-01')"))
            await conn.execute(text("INSERT INTO sales SELECT 100+n,'Buyer '||lpad(n::text,6,'0'),'9'||lpad(n::text,9,'0'),'2026-01-01'::date FROM generate_series(1,100000) n"))
            async def managers():return {"alpha":"One","beta":"Two","no purchases":"One"}
            monkeypatch.setattr(service,"get_client_managers",managers)
            async with AsyncSession(bind=conn) as db:
                queries.clear();data=await service.load_buyers(db,CalltrackBuyersRequest())
                assert data["total"]==100004 and len(data["items"])==50
                assert data["items"][0]["client_key"]=="alpha"
                assert len(queries)==3,queries
                first={r["client_key"] for r in data["items"]}
                second=await service.load_buyers(db,CalltrackBuyersRequest(page=2))
                assert first.isdisjoint(r["client_key"] for r in second["items"])
                one=await service.load_buyers(db,CalltrackBuyersRequest(manager="One"))
                assert one["total"]==1 and one["items"][0]["name"]=="Alpha"
                assert one["items"][0]["last_purchase_at"]==date(2026,10,19)
                for search in ["aLpHa","+7 (989) 282-98-42","9892829842","89892829842"]:
                    result=await service.load_buyers(db,CalltrackBuyersRequest(search=search))
                    assert result["total"]==1 and result["items"][0]["client_key"]=="alpha"
                    assert result["items"][0]["purchase_count"]==2
                empty=await service.load_buyers(db,CalltrackBuyersRequest(manager="Unknown"));assert empty["total"]==0
                shared=await service.load_buyers(db,CalltrackBuyersRequest(client_key="shared a"));assert shared["items"][0]["unique_phones"]==[]
                for mode,expected in [('today',1),('yesterday',0),('7',2),('30',4),('inactive',100000)]:
                    q=service.buyers_query(activity=mode,today=date(2026,10,19)).subquery()
                    from sqlalchemy import select,func
                    count=await db.scalar(select(func.count()).select_from(q));assert count==expected,(mode,count)
                print('100004 buyers, page_size50, 3 SQL queries; no raw sales/items hydration')
        await engine.dispose()
    asyncio.run(run())
