"""Fast snapshot reads only; never fallback to raw sales or external directories."""

from pydantic import BaseModel, Field, ConfigDict
from fastapi import APIRouter, Depends
from app.database import get_db
from app.services.report_execution import REPORT_SETTINGS
from sqlalchemy.exc import DBAPIError
import asyncio, time
from app.services.product_analytics_runtime import ensure_headroom
from fastapi import HTTPException


async def get_snapshot_db(db=Depends(get_db)):
    try:
        for statement in REPORT_SETTINGS:
            await db.execute(text(statement.replace("60s", "5s")))
        await db.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ"))
        deadline = time.monotonic() + 1.0
        while not await db.scalar(text("SELECT pg_try_advisory_xact_lock(734190292)")):
            if time.monotonic() >= deadline:
                raise HTTPException(
                    503, "Снимок занят другим запросом. Повторите загрузку."
                )
            await asyncio.sleep(0.05)
        ensure_headroom()
        yield db
    except DBAPIError:
        raise HTTPException(
            503, "Снимок временно недоступен. Повторите загрузку."
        ) from None
    finally:
        await db.rollback()


from app.services.category_seasonality_snapshot import read_report, metadata
from sqlalchemy import text

router = APIRouter(prefix="/reports/category-seasonality", tags=["Отчеты"])


class Request(BaseModel):
    model_config = ConfigDict(extra="forbid")
    years: list[int] = Field(default_factory=list, max_length=200)
    stores: list[str] = Field(default_factory=list, max_length=200)
    categories: list[str] = Field(default_factory=list, max_length=5000)
    brands: list[str] = Field(default_factory=list, max_length=1000)
    manufacturers: list[str] = Field(default_factory=list, max_length=1000)
    manager: list[str] = Field(default_factory=list, max_length=200)
    buyer_type: list[str] = Field(default_factory=list, max_length=200)
    snapshot_id: str | None = Field(None, max_length=36)


@router.get("/options")
async def options(db=Depends(get_snapshot_db)):
    active, status = await metadata(db)
    return {**status, **(active.options if active else {})}


@router.post("")
async def overview(data: Request, db=Depends(get_snapshot_db)):
    return await read_report(db, data)


@router.post("/detail")
async def detail(data: Request, db=Depends(get_snapshot_db)):
    if (
        not data.snapshot_id
        or len(data.categories) != 1
        or not data.categories[0].startswith("[")
    ):
        raise HTTPException(422, "Для детализации нужен снимок и одна подкатегория.")
    return await read_report(db, data, include_actual=True)
