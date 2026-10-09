"""Криптографические примитивы сессии настроек без web-зависимостей."""
import base64
import hashlib
import hmac
import json
import secrets
import time

SESSION_TTL_SECONDS = 8 * 60 * 60
SCRYPT_N = 2**14


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _unb64(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def hash_password(password: str, salt: bytes | None = None) -> str:
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=SCRYPT_N, r=8, p=1, dklen=32)
    return f"scrypt:{SCRYPT_N}:8:1:{_b64(salt)}:{_b64(digest)}"


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, n, r, p, salt, expected = encoded.split(":", 5)
        if algorithm != "scrypt": return False
        actual = hashlib.scrypt(password.encode(), salt=_unb64(salt), n=int(n), r=int(r), p=int(p), dklen=32)
        return hmac.compare_digest(actual, _unb64(expected))
    except (ValueError, TypeError):
        return False


def create_session(secret: str, now: int | None = None) -> str:
    current = now if now is not None else int(time.time())
    payload = _b64(json.dumps({"exp": current + SESSION_TTL_SECONDS}, separators=(",", ":")).encode())
    signature = _b64(hmac.new(secret.encode(), payload.encode(), hashlib.sha256).digest())
    return f"{payload}.{signature}"


def verify_session(token: str, secret: str, now: int | None = None) -> bool:
    try:
        payload, signature = token.split(".", 1)
        expected = _b64(hmac.new(secret.encode(), payload.encode(), hashlib.sha256).digest())
        data = json.loads(_unb64(payload))
        current = now if now is not None else int(time.time())
        return hmac.compare_digest(signature, expected) and int(data["exp"]) >= current
    except (ValueError, TypeError, KeyError, json.JSONDecodeError):
        return False
