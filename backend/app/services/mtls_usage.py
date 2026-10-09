"""Редкое обновление времени использования проверенного mTLS-сертификата."""
import time
from datetime import datetime,timedelta,timezone

from sqlalchemy import or_,update

from app.config import settings
from app.database import SessionLocal
from app.models import AccessDevice

seen:dict[str,float]={}

async def record_mtls_usage(headers) -> None:
    if not settings.mtls_proxy_secret or headers.get("x-sales-proxy-secret")!=settings.mtls_proxy_secret:return
    if headers.get("x-sales-mtls-verify")!="SUCCESS":return
    serial=(headers.get("x-sales-mtls-serial") or "").strip()
    if not serial:return
    now=time.monotonic()
    if now-seen.get(serial,0)<600:return
    stamp=datetime.now(timezone.utc)
    async with SessionLocal() as db:
        await db.execute(update(AccessDevice).where(AccessDevice.serial_number==serial,AccessDevice.status=="active",
            or_(AccessDevice.last_used_at.is_(None),AccessDevice.last_used_at<stamp-timedelta(minutes=10))).values(last_used_at=stamp))
        await db.commit()
    seen[serial]=now
