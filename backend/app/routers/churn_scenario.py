"""Admin-only monitoring: no update, launch, approval, delivery or shell endpoint."""
import asyncio
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, StrictInt
from typing import Literal
from urllib.parse import urlsplit
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from app.database import get_db
from app.services.settings_auth import require_settings_admin
from app.services.churn_monitor import status
from app.services.churn_diagnostics import diagnostic
from app.services import churn_schedule
from app.config import settings

router = APIRouter(prefix='/settings/churn-scenario', tags=['Сигнал Риск ухода'],
                   dependencies=[Depends(require_settings_admin)])


@router.get('/status')
async def scenario_status(db=Depends(get_db)):
    try:
        if db.get_bind().dialect.name == 'postgresql':
            await db.execute(text('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY'))
            await db.execute(text("SET LOCAL statement_timeout = '3s'"))
            await db.execute(text("SET LOCAL lock_timeout = '500ms'"))
        async with asyncio.timeout(5):
            result = await status(db)
            return JSONResponse(content=jsonable_encoder(result), headers={'Cache-Control': 'no-store'})
    except (SQLAlchemyError, TimeoutError):
        raise HTTPException(503, detail=diagnostic('DB_STATUS_UNAVAILABLE'), headers={'Cache-Control': 'no-store'}) from None
    finally:
        try:
            await db.rollback()
        except SQLAlchemyError:
            pass


@router.get('/automation')
async def automation_status(db=Depends(get_db)):
    try:
        if db.get_bind().dialect.name == 'postgresql':
            await db.execute(text("SET LOCAL statement_timeout = '3s'"))
        async with asyncio.timeout(5):
            result = await churn_schedule.overview(db)
        return JSONResponse(content=jsonable_encoder(result), headers={'Cache-Control': 'no-store'})
    except (SQLAlchemyError, TimeoutError):
        raise HTTPException(503, 'Состояние постоянной автоматизации недоступно') from None
    finally:
        await db.rollback()


class ControlRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    action: Literal['draft', 'enable', 'disable', 'diagnostic']
    revision: StrictInt = Field(ge=0)
    settings: churn_schedule.ScheduleSettings | None = None


@router.post('/automation')
async def automation_control(payload: ControlRequest, request: Request, db=Depends(get_db)):
    # Cookie auth alone is insufficient for control. No cross-origin simple request.
    origin = request.headers.get('origin')
    public = urlsplit(settings.public_url)
    if (request.headers.get('x-churn-control') != '1'
            or request.headers.get('sec-fetch-site') == 'cross-site'
            or origin and origin != f'{public.scheme}://{public.netloc}'):
        raise HTTPException(403, 'Недопустимый источник запроса')
    try:
        async with asyncio.timeout(5):
            if db.get_bind().dialect.name == 'postgresql':
                await db.execute(text("SET LOCAL lock_timeout = '1s'"))
            if payload.action == 'diagnostic':
                from datetime import datetime, timezone
                row = await churn_schedule.control(db)
                if row.revision != payload.revision:
                    raise ValueError('POLICY_REVISION_CONFLICT')
                job = await churn_schedule.enqueue(db, now=datetime.now(timezone.utc), diagnostic=True)
                result = dict(job_id=job.id, status=job.status)
            else:
                result = await churn_schedule.change(db, payload.action,
                    payload.settings.model_dump() if payload.settings else {}, revision=payload.revision,
                    actor='authenticated-settings-admin')
            await db.commit()
            return result
    except ValueError:
        await db.rollback()
        raise HTTPException(409, 'Политика изменена или требует утверждения оператором. Обновите статус.') from None
    except (SQLAlchemyError, TimeoutError):
        await db.rollback()
        raise HTTPException(503, 'Управление временно недоступно. Состояние не подтверждено; обновите статус.') from None
