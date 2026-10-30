"""Admin-only monitoring: no update, launch, approval, delivery or shell endpoint."""
import asyncio
from fastapi import APIRouter, Depends, HTTPException
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError
from app.database import get_db
from app.services.settings_auth import require_settings_admin
from app.services.churn_monitor import status
from app.services.churn_diagnostics import diagnostic

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
