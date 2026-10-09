import time
from collections import defaultdict

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field

from app.config import settings
from app.services.settings_auth import COOKIE_NAME, ensure_auth_configured
from app.services.settings_auth_crypto import SESSION_TTL_SECONDS, create_session, verify_password

router = APIRouter(prefix="/settings/auth", tags=["Настройки"])
failed_attempts: dict[str, list[float]] = defaultdict(list)


class LoginRequest(BaseModel):
    password: str = Field(min_length=1, max_length=1024)


@router.post("/login")
async def login(data: LoginRequest, request: Request, response: Response):
    ensure_auth_configured()
    address = request.client.host if request.client else "unknown"; now = time.monotonic()
    failed_attempts[address] = [value for value in failed_attempts[address] if now - value < 900]
    if len(failed_attempts[address]) >= 5:
        raise HTTPException(429, "Слишком много попыток. Повторите через 15 минут")
    if not verify_password(data.password, settings.settings_admin_password_hash):
        failed_attempts[address].append(now)
        raise HTTPException(401, "Неверный пароль")
    failed_attempts.pop(address, None)
    response.set_cookie(
        COOKIE_NAME, create_session(settings.settings_admin_session_secret),
        max_age=SESSION_TTL_SECONDS, httponly=True, secure=settings.settings_admin_cookie_secure,
        samesite="strict", path=f"{settings.base_path.rstrip('/')}/api",
    )
    return {"authenticated": True}


@router.get("/session")
async def session(request: Request):
    from app.services.settings_auth import require_settings_admin
    await require_settings_admin(request)
    return {"authenticated": True}


@router.post("/logout", status_code=204)
async def logout(response: Response):
    response.delete_cookie(COOKIE_NAME, path=f"{settings.base_path.rstrip('/')}/api")
