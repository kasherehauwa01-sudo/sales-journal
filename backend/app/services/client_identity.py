import re


def normalize_phone(value: object) -> str | None:
    """Приводит телефон к формату, который используется при импорте продаж."""
    if value in (None, ""):
        return None
    digits = re.sub(r"\D", "", str(value).split(".")[0])
    return "+7" + digits[-10:] if len(digits) >= 10 else digits or None


def normalize_client_name(value: object) -> str | None:
    """Нормализует имя только для точного, а не нечёткого сопоставления."""
    normalized = str(value or "").strip().casefold()
    return normalized or None
