import logging
import secrets
import time

from fastapi import APIRouter, Depends, HTTPException
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database import get_db
from app.schemas.calltrack import CalltrackSaleDetail, CalltrackSalesRequest, CalltrackSalesResponse
from app.services.calltrack_integration import AmbiguousClientReference, find_sales_for_calltrack
from app.services.sale_details import load_sale_detail


router = APIRouter(prefix="/integrations/calltrack", tags=["Интеграция Calltrack"])
log = logging.getLogger(__name__)
bearer = HTTPBearer(auto_error=False)


def require_calltrack_token(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
):
    """Проверяет server-to-server token, не раскрывая его значение."""
    expected = settings.calltrack_integration_token
    supplied = credentials.credentials if credentials and credentials.scheme.lower() == "bearer" else ""
    if not expected or not supplied or not secrets.compare_digest(supplied, expected):
        raise HTTPException(401, "Недействительный integration token", headers={"WWW-Authenticate": "Bearer"})


@router.post("/client-sales", response_model=CalltrackSalesResponse, dependencies=[Depends(require_calltrack_token)])
async def client_sales(payload: CalltrackSalesRequest, db: AsyncSession = Depends(get_db)):
    started = time.monotonic()
    try:
        items = await find_sales_for_calltrack(db, payload)
    except AmbiguousClientReference as exc:
        raise HTTPException(422, str(exc)) from exc
    except Exception:
        log.exception(
            "Ошибка Calltrack batch: clients=%s period=%s..%s",
            len(payload.clients), payload.date_from, payload.date_to,
        )
        raise
    log.info(
        "Calltrack batch: clients=%s period=%s..%s sales=%s duration_ms=%s",
        len(payload.clients), payload.date_from, payload.date_to, len(items),
        round((time.monotonic() - started) * 1000),
    )
    return CalltrackSalesResponse(items=items)


@router.get("/sales/{sale_id}", response_model=CalltrackSaleDetail, dependencies=[Depends(require_calltrack_token)])
async def sale_detail(sale_id: int, db: AsyncSession = Depends(get_db)):
    try:
        sale = await load_sale_detail(db, sale_id)
    except Exception:
        log.exception("Ошибка Calltrack detail: sale_id=%s", sale_id)
        raise
    if not sale:
        raise HTTPException(404, "Продажа не найдена")
    return CalltrackSaleDetail.model_validate(sale.model_dump())
