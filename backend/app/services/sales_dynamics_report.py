from datetime import date, timedelta


def effective_period(date_from: date, date_to: date, period_kind: str, today: date) -> tuple[date, date, str | None]:
    """Исключает сегодняшний день: данные продаж поступают только на следующий день."""
    if period_kind in {"current_week", "current_month", "custom"} and date_to >= today:
        effective_to = today - timedelta(days=1)
        if effective_to < date_from:
            return date_from, effective_to, "За сегодняшний день данных нет. В выбранном периоде пока нет загруженных данных."
        return date_from, effective_to, f"За сегодняшний день данных нет. Отчет построен за период {date_from:%d.%m.%Y} — {effective_to:%d.%m.%Y}."
    return date_from, date_to, None


def previous_period(date_from: date, date_to: date, period_kind: str) -> tuple[date, date]:
    """Возвращает календарный период сравнения для выбранного быстрого периода."""
    if period_kind == "current_week":
        return date_from - timedelta(days=7), date_to - timedelta(days=7)
    if period_kind == "previous_week":
        start = date_from - timedelta(days=7)
        return start, start + timedelta(days=6)
    if period_kind == "current_month":
        previous_end = date_from - timedelta(days=1)
        previous_start = previous_end.replace(day=1)
        comparison_end = previous_start.replace(day=min(date_to.day, previous_end.day))
        return previous_start, comparison_end
    if period_kind == "previous_month":
        first = date_from.replace(day=1)
        previous_end = first - timedelta(days=1)
        return previous_end.replace(day=1), previous_end
    length = (date_to - date_from).days + 1
    return date_from - timedelta(days=length), date_from - timedelta(days=1)


def default_grouping(date_from: date, date_to: date) -> str:
    days = (date_to - date_from).days + 1
    return "day" if days <= 45 else "week" if days <= 180 else "month"


def metric_comparison(current: float, previous: float) -> dict:
    difference = current - previous
    return {
        "current": current,
        "previous": previous,
        "difference": difference,
        "change_percent": None if previous == 0 else difference / previous * 100,
    }


def calculated_metrics(revenue: float, sales_count: int, items_count: float) -> dict[str, float]:
    return {
        "revenue": revenue,
        "sales_count": sales_count,
        "items_count": items_count,
        "average_check": revenue / sales_count if sales_count else 0,
        "average_items_per_sale": items_count / sales_count if sales_count else 0,
    }


def align_chart_points(current_points: list[dict], previous_points: list[dict]) -> list[dict]:
    """Сопоставляет предыдущие точки только с фактическими точками текущего периода."""
    result = []
    for index, current in enumerate(current_points):
        previous = previous_points[index] if index < len(previous_points) else None
        result.append({
            "index": index + 1,
            "period": current["period"],
            "previous_period": previous["period"] if previous else None,
            "current": current,
            "previous": previous,
        })
    return result
