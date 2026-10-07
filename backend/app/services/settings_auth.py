"""Парольная защита административного раздела настроек."""
from fastapi import HTTPException, Request

from app.config import settings
from app.services.settings_auth_crypto import verify_session

COOKIE_NAME = "sales_settings_admin"


def ensure_auth_configured() -> None:
    if not settings.settings_admin_password_hash or len(settings.settings_admin_session_secret) < 32:
        raise HTTPException(503, "Пароль администратора раздела настроек не настроен")


async def require_settings_admin(request: Request) -> None:
    ensure_auth_configured()
    token = request.cookies.get(COOKIE_NAME, "")
    if not verify_session(token, settings.settings_admin_session_secret):
        raise HTTPException(401, "Требуется пароль администратора")
