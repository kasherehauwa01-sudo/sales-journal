import re

ANALYTICAL_NORMALIZATION_VERSION = 1
# Explicit whitespace alphabet shared with PostgreSQL (including NBSP).
NAME_WHITESPACE = r"[\u0009-\u000d \u0085\u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000]+"
NAME_WHITESPACE_SQL = "[\t\n\v\f\r \u0085\u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000]+"


def normalize_phone(value: object) -> str | None:
    """Приводит телефон к формату, который используется при импорте продаж."""
    if value in (None, ""):
        return None
    digits = re.sub(r"\D", "", str(value).split(".")[0])
    return "+7" + digits[-10:] if len(digits) >= 10 else digits or None


def normalize_client_name(value: object, *, version: int | None = None) -> str | None:
    """Нормализует имя только для точного, а не нечёткого сопоставления."""
    if version is not None:
        if version != ANALYTICAL_NORMALIZATION_VERSION:
            raise ValueError("Unsupported analytical normalization version")
        normalized = re.sub(NAME_WHITESPACE, " ", str(value or "")).strip(" ").lower()
        return normalized or None
    normalized = str(value or "").strip().casefold()
    return normalized or None


def analytical_name_sql(column):
    """SQL is authoritative for persisted v1 keys; legacy SQL keys stay unchanged."""
    from sqlalchemy import func
    return func.lower(func.trim(func.regexp_replace(column, NAME_WHITESPACE_SQL, " ", "g")))
