"""Administrator-only durable commands. No long task runs in the web process."""

from urllib.parse import urlsplit
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from typing import Literal
from app.config import settings
from app.database import get_db
from app.services.settings_auth import require_settings_admin
from app.services import catalog_sync_control as control

router = APIRouter(
    prefix="/settings/catalog-sync",
    tags=["Синхронизация каталога"],
    dependencies=[Depends(require_settings_admin)],
)


class StartIn(BaseModel):
    mode: Literal["discovery", "refresh"] = "discovery"


def check_origin(request: Request):
    origin = request.headers.get("origin")
    public = urlsplit(settings.public_url)
    allowed = set(settings.origins) | {f"{public.scheme}://{public.netloc}"}
    if origin is not None and origin not in allowed:
        raise HTTPException(403, "Недопустимый источник команды")


@router.get("/status")
async def status(db=Depends(get_db)):
    return await control.status(db)


@router.post("/start", status_code=202)
async def start(data: StartIn, request: Request, db=Depends(get_db)):
    check_origin(request)
    return await control.start(db, data.mode)


@router.post("/pause", status_code=202)
async def pause(request: Request, db=Depends(get_db)):
    check_origin(request)
    return await control.pause(db)
