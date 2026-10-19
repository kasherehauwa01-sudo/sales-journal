import asyncio
from decimal import Decimal as D
import os

import pytest
from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from app.schemas.calltrack import CalltrackCategoryDetailsRequest
from app.services import calltrack_category_details as service


def row(key, revenue, quantity="1", code=None):
    return dict(key=key,revenue=D(revenue),quantity=D(quantity),name=f"Product {key}",code=code or str(key),article=f"A{key}")


def test_exact_revenue_negative_returns_missing_subcategory_and_product_aliases():
    rows=[row(1,"10.005"),row(2,"-2.001"),row(3,"5"),row(4,"100")]
    catalog={1:dict(catalog_category="Category",section="Sub",product_id=1),
             2:dict(catalog_category="Category",section="Sub",product_id=1),
             3:dict(catalog_category="Category"),4:dict(catalog_category="Other",section="Sub")}
    groups,total,quantity=service.aggregate_details(rows,catalog,"Category")
    assert total==D("13.004") and quantity==3
    assert groups["Sub"]["revenue"]==D("8.004")
    assert groups["Без подкатегории"]["revenue"]==5
    products,total,_=service.aggregate_details(rows,catalog,"Category","Sub")
    assert len(products)==1 and total==D("8.004")
    missing,total,_=service.aggregate_details([row(5,"2")],{},"Без категории")
    assert missing["Без подкатегории"]["revenue"]==2
    assert not service.aggregate_details(rows,catalog,"Missing")[0]


def test_names_and_paths_are_bounded():
    for args in [dict(names=[],category="x"),dict(names=[" "],category="x"),dict(names=["x"],category=""),dict(names=["x"],category="x",subcategory="")]:
        with pytest.raises(ValidationError):CalltrackCategoryDetailsRequest(**args)


@pytest.mark.skipif(not os.getenv("TEST_BUYERS_DATABASE_URL"),reason="Disposable PostgreSQL only")
def test_real_postgres_combines_clients_before_top_10_and_top_20(monkeypatch):
    async def run():
        engine=create_async_engine(os.environ["TEST_BUYERS_DATABASE_URL"])
        async with engine.connect() as conn:
            assert await conn.scalar(text("SELECT current_database()"))=="calltrack_buyers_test"
            await conn.execute(text("CREATE TEMP TABLE sales(id BIGINT PRIMARY KEY,client TEXT)"))
            await conn.execute(text("CREATE TEMP TABLE sale_items(id BIGINT PRIMARY KEY,sale_id BIGINT,code TEXT,article TEXT,name TEXT,quantity NUMERIC,actual_price NUMERIC)"))
            await conn.execute(text("INSERT INTO sales VALUES(1,'One'),(2,'Two'),(3,'Not selected')"))
            await conn.execute(text("INSERT INTO sale_items SELECT n,CASE WHEN n%2=0 THEN 1 ELSE 2 END,n::text,'A'||n,'Product '||n,1,n FROM generate_series(1,1100) n"))
            await conn.execute(text("INSERT INTO sale_items VALUES(2000,3,'2000','X','Outside',1,999999)"))
            calls=[]
            async def catalog(rows,db):
                calls.append(len(rows))
                return {r['key']:dict(catalog_category='Category',section='Sub '+str(int(r['code'])%12),product_id=int(r['code'])) for r in rows}
            monkeypatch.setattr(service,'local_catalog',catalog)
            async with AsyncSession(bind=conn) as db:
                request=CalltrackCategoryDetailsRequest(names=['One','Two','One'],category='Category')
                result=await service.category_details(db,request)
                assert len(result['items'])==10 and result['total_count']==12
                assert result['parent_revenue']==D(1100*1101//2)
                assert max(calls)<=1000 and len(calls)==2
                assert result['items']==sorted(result['items'],key=lambda r:(-r['revenue'],r['name'],r['key']))
                request=CalltrackCategoryDetailsRequest(names=['One','Two'],category='Category',subcategory='Sub 1')
                products=await service.category_details(db,request)
                assert len(products['items'])==20 and products['total_count']>20
                assert all(int(r['code'])%12==1 for r in products['items'])
                assert products['items']==sorted(products['items'],key=lambda r:(-r['revenue'],r['name'],r['key']))
                empty=await service.category_details(db,CalltrackCategoryDetailsRequest(names=['One'],category='Missing'))
                assert empty['items']==[] and empty['parent_revenue']==0
        await engine.dispose()
    asyncio.run(run())
