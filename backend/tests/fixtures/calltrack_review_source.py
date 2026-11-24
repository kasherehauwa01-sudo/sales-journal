"""CI-only FastAPI Sales Journal source for full PHP/MariaDB review E2E."""
import asyncio
import os
from datetime import date
from decimal import Decimal
from sqlalchemy import insert, update, text
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker
from sqlalchemy.pool import NullPool
from fastapi import FastAPI, Header, HTTPException
from app.models import Base, Sale, ImportBatch
from app.routers.analytics_delivery import router
from app.database import get_db
from app.config import settings

raw = os.environ["CALLTRACK_HISTORY_TEST_DATABASE_URL"]
url = make_url(raw)
assert url.host == "127.0.0.1" and url.database == "calltrack_history_test"
SCHEMA = "e2e_history_lab"
engine = create_async_engine(raw, poolclass=NullPool,
    connect_args={"server_settings":{"search_path":SCHEMA}})
factory = async_sessionmaker(engine, expire_on_commit=False)
settings.calltrack_integration_token = "synthetic-token"

async def prepare():
    admin = create_async_engine(raw, poolclass=NullPool)
    try:
        async with admin.begin() as db:
            await db.execute(text("DROP SCHEMA IF EXISTS "+SCHEMA+" CASCADE"))
            await db.execute(text("CREATE SCHEMA "+SCHEMA))
        async with engine.begin() as db:
            await db.run_sync(lambda c: Base.metadata.create_all(c, tables=[
                ImportBatch.__table__, Sale.__table__]))
        async with factory() as db:
            await db.execute(insert(ImportBatch).values(
                id=1,filename="synthetic",file_size=1,status="completed"))
            await db.execute(insert(Sale), [
                dict(id=i,sale_date=date(2026,10,1),document_number=f"РЕА-{i}",
                     client="ООО РАДУГА" if i%2 else "  ооо  радуга ",
                     department="Авиаторов",total_amount=Decimal("10.20"),
                     fingerprint=f"synthetic-{i}",import_id=1)
                for i in range(1,56)
            ])
            await db.commit()
    finally:
        await admin.dispose()

asyncio.run(prepare())
app = FastAPI()
app.include_router(router, prefix="/api")
async def dependency():
    async with factory() as db:
        yield db
app.dependency_overrides[get_db] = dependency

@app.post("/__synthetic_mutation")
async def synthetic_mutation(x_test_secret: str | None=Header(None)):
    if x_test_secret != "ci-local-only" : raise HTTPException(403)
    async with factory() as db:
        await db.execute(update(Sale).where(Sale.id==2)
            .values(total_amount=Decimal("10.21")))
        await db.commit()
    return {"ok":True}

if __name__=="__main__":
    import uvicorn
    uvicorn.run(app,host="127.0.0.1",port=18080,log_level="warning")
