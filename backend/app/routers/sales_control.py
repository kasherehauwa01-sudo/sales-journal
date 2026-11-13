"""Admin-only actual-sales control; never mutates sales."""

import hmac
from app.config import settings
from datetime import date
from typing import Literal
from fastapi import APIRouter, Depends, Query, Request, HTTPException
from pydantic import BaseModel, Field, ConfigDict
from sqlalchemy import select, text
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession
from app.database import get_db
from app.services.settings_auth import require_settings_admin
from app.services import sales_control as service
from app.models.sales_control import SalesControlDelivery

router = APIRouter(
    prefix="/sales-control",
    tags=["Контроль продаж"],
    dependencies=[Depends(require_settings_admin)],
)


def actor(request):
    if (
        settings.mtls_proxy_secret
        and hmac.compare_digest(
            request.headers.get("x-sales-proxy-secret", ""), settings.mtls_proxy_secret
        )
        and request.headers.get("x-sales-mtls-verify") == "SUCCESS"
    ):
        return (
            "settings-admin:certificate:"
            + request.headers.get("x-sales-mtls-serial", "unknown")[:128]
        )
    return "settings-admin:shared-session"


class Pair(BaseModel):
    model_config = ConfigDict(extra="forbid")
    department: str = Field(min_length=1, max_length=512)
    sales_date: date


class Pairs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    items: list[Pair] = Field(min_length=1, max_length=50)
    approve: bool = False


class Confirmation(BaseModel):
    model_config = ConfigDict(extra="forbid")
    approve: bool = False


class NotificationSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool
    send_time: str = Field(pattern=r"^([01][0-9]|2[0-3]):[0-5][0-9]$")
    recipients: list[str] = Field(max_length=100)
    revision: int = Field(ge=1)


@router.get("/missing")
async def missing(
    date_from: date | None = None,
    date_to: date | None = None,
    department: str = Query("", max_length=512),
    search: str = Query("", max_length=100),
    hidden: bool = False,
    page: int = Query(1, ge=1, le=100000),
    sort: Literal["date", "department"] = "date",
    direction: Literal["asc", "desc"] = "desc",
    db: AsyncSession = Depends(get_db),
):
    try:
        await db.execute(
            text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
        )
        return await service.missing(
            db, date_from, date_to, department, search, hidden, page, sort, direction
        )
    except SQLAlchemyError:
        raise HTTPException(
            503, "Проверка продаж временно недоступна. Повторите позже."
        )


@router.get("/departments")
async def departments(db: AsyncSession = Depends(get_db)):
    return await service.departments(db)


@router.post("/exclusions")
async def hide(payload: Pairs, request: Request, db: AsyncSession = Depends(get_db)):
    if not payload.approve:
        raise HTTPException(422, "Нужно подтверждение удаления записей из контроля.")
    return await service.exclusions(
        db, [(x.department, x.sales_date) for x in payload.items], True, actor(request)
    )


@router.delete("/exclusions")
async def restore(payload: Pairs, request: Request, db: AsyncSession = Depends(get_db)):
    if not payload.approve:
        raise HTTPException(422, "Нужно подтверждение восстановления.")
    return await service.exclusions(
        db, [(x.department, x.sales_date) for x in payload.items], False, actor(request)
    )


@router.get("/notification-settings")
async def notification_settings(db: AsyncSession = Depends(get_db)):
    return service.settings_out(await service.settings_row(db))


@router.put("/notification-settings")
async def save_settings(
    payload: NotificationSettings, request: Request, db: AsyncSession = Depends(get_db)
):
    return await service.save_settings(db, payload, actor(request))


@router.get("/notification-status")
async def notification_status(db: AsyncSession = Depends(get_db)):
    row = await db.scalar(
        select(SalesControlDelivery)
        .order_by(
            SalesControlDelivery.checked_at.desc(), SalesControlDelivery.id.desc()
        )
        .limit(1)
    )
    if not row:
        return None
    return {
        k: getattr(row, k)
        for k in (
            "id",
            "sales_date",
            "checked_at",
            "missing_count",
            "status",
            "attempted_at",
            "sent_at",
            "error_code",
            "attempts",
        )
    }


@router.post("/notifications/{identity}/retry")
async def retry(
    identity: int,
    payload: Confirmation,
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    # Reuses a typed confirmation but does not use its selection as recipients/content.
    if not payload.approve:
        raise HTTPException(422, "Подтвердите возможную повторную доставку.")
    await service.mutation_lock(db)
    row = await db.get(SalesControlDelivery, identity)
    cfg = await service.settings_row(db)
    if not cfg.enabled:
        raise HTTPException(409, "Сначала включите уведомления в настройках.")
    if not row or row.status not in ("failed", "uncertain"):
        raise HTTPException(409, "Повтор недоступен.")
    if row.recipients_hash != service.recipient_hash(cfg.recipients):
        raise HTTPException(
            409, "Получатели изменились. Требуется ручное разбирательство."
        )
    row.manual_retry = True
    row.attempts = 0
    row.next_attempt = service.now()
    service.audit(
        db,
        actor(request),
        "retry",
        {"delivery_id": identity, "previous_status": row.status},
    )
    await db.commit()
    return {"queued": True}
