from calendar import monthrange
from datetime import date, timedelta


def previous_year_period(start: date, end: date) -> tuple[date, date]:
    """Возвращает аналогичный диапазон прошлого года, безопасно для 29 февраля."""
    def shift(value: date) -> date:
        year = value.year - 1
        return value.replace(year=year, day=min(value.day, monthrange(year, value.month)[1]))
    return shift(start), shift(end)


def previous_period(start: date, end: date, period_kind: str = "custom") -> tuple[date, date]:
    """Возвращает непосредственно предшествующий календарный период."""
    if period_kind in {"current_month", "previous_month", "month"} and start.day == 1 and end.day == monthrange(end.year, end.month)[1]:
        previous_end = start - timedelta(days=1)
        return previous_end.replace(day=1), previous_end
    if period_kind in {"year", "current_year", "previous_year"} and start.month == start.day == 1 and end.month == 12 and end.day == 31:
        return date(start.year - 1, 1, 1), date(start.year - 1, 12, 31)
    days = (end - start).days + 1
    return start - timedelta(days=days), start - timedelta(days=1)


def comparison_period(start: date, end: date, mode: str, period_kind: str = "custom") -> tuple[date, date]:
    if mode == "previous_year":
        return previous_year_period(start, end)
    if mode == "previous_period":
        return previous_period(start, end, period_kind)
    raise ValueError("Неизвестный режим сравнения")
