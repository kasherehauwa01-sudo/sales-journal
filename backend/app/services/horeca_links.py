"""Versioned HoReCa links: preserve previously issued opaque tokens unchanged."""
import secrets
from datetime import datetime, timedelta, timezone

HORECA_LINK_LIFETIME = timedelta(hours=240)
# Previous token_urlsafe(32) values cannot contain '.', so no legacy collision.
HORECA_LINK_PREFIX = "h1."
HORECA_LINK_VALIDITY_TEXT = "Ссылка действует 10 суток (240 часов) с момента создания."


def create_horeca_link() -> tuple[str, datetime]:
    return HORECA_LINK_PREFIX + secrets.token_urlsafe(32), datetime.now(timezone.utc)


def horeca_link_expires_at(token: str, created_at: datetime) -> datetime | None:
    if not token.startswith(HORECA_LINK_PREFIX):
        if "." in token:
            raise ValueError("Unsupported HoReCa link version")
        # Legacy links had no expiry. Do not change already sent links retroactively.
        return None
    if not isinstance(created_at, datetime):
        raise ValueError("Missing HoReCa link creation time")
    # PostgreSQL uses timestamptz. Treat a driver returning a naive UTC value
    # explicitly as UTC, never as the host's local timezone.
    utc_created = created_at.replace(tzinfo=timezone.utc) if created_at.tzinfo is None else created_at.astimezone(timezone.utc)
    return utc_created + HORECA_LINK_LIFETIME
