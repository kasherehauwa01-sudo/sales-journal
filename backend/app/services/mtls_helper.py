"""Клиент минимально привилегированного CA-helper через Unix socket."""
import asyncio
import hashlib
import hmac
import json

from app.config import settings


class MtlsHelperError(RuntimeError): pass


def signed_request(payload: dict, secret: str) -> bytes:
    raw=json.dumps(payload,sort_keys=True,separators=(",",":"),ensure_ascii=False).encode()
    signature=hmac.new(secret.encode(),raw,hashlib.sha256).hexdigest()
    return json.dumps({"payload":payload,"signature":signature},ensure_ascii=False,separators=(",",":")).encode()+b"\n"


async def helper_call(action: str, **values):
    if not settings.mtls_helper_secret:raise MtlsHelperError("CA-helper не настроен")
    try:
        reader,writer=await asyncio.wait_for(asyncio.open_unix_connection(str(settings.mtls_helper_socket)),10)
        writer.write(signed_request({"action":action,**values},settings.mtls_helper_secret));await writer.drain()
        response=json.loads((await asyncio.wait_for(reader.readline(),120)).decode())
        writer.close();await writer.wait_closed()
    except Exception as exc:raise MtlsHelperError(f"CA-helper недоступен: {exc}") from exc
    if not response.get("ok"):raise MtlsHelperError(response.get("error") or "Ошибка CA-helper")
    return response.get("data") or {}
