from datetime import date, timedelta


def effective_period(
    date_from: date, date_to: date, period_kind: str, today: date
) -> tuple[date, date, str | None]:
    """Исключает сегодняшний день: данные продаж поступают только на следующий день."""
    if (
        period_kind in {"current_week", "current_month", "year", "custom"}
        and date_to >= today
    ):
        effective_to = today - timedelta(days=1)
        if effective_to < date_from:
            return (
                date_from,
                effective_to,
                "За сегодняшний день данных нет. В выбранном периоде пока нет загруженных данных.",
            )
        return (
            date_from,
            effective_to,
            f"За сегодняшний день данных нет. Отчет построен за период {date_from:%d.%m.%Y} — {effective_to:%d.%m.%Y}.",
        )
    return date_from, date_to, None


def previous_period(
    date_from: date, date_to: date, period_kind: str
) -> tuple[date, date]:
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


def calculated_metrics(
    revenue: float, sales_count: int, items_count: float
) -> dict[str, float]:
    return {
        "revenue": revenue,
        "sales_count": sales_count,
        "items_count": items_count,
        "average_check": revenue / sales_count if sales_count else 0,
        "average_items_per_sale": items_count / sales_count if sales_count else 0,
    }


def align_chart_points(
    current_points: list[dict], previous_points: list[dict]
) -> list[dict]:
    """Сопоставляет предыдущие точки только с фактическими точками текущего периода."""
    result = []
    for index, current in enumerate(current_points):
        previous = previous_points[index] if index < len(previous_points) else None
        result.append(
            {
                "index": index + 1,
                "period": current["period"],
                "previous_period": previous["period"] if previous else None,
                "current": current,
                "previous": previous,
            }
        )
    return result


def dynamics_comparison_period(start, end, mode, period_kind):
    from calendar import monthrange
    from app.services.report_periods import comparison_period

    if period_kind == "year":
        return comparison_period(start, end, "previous_year", period_kind)
    if mode == "previous_period":
        if period_kind in {"current_month", "previous_month"}:

            def shift(value):
                month = value.month - 1 or 12
                year = value.year - (value.month == 1)
                return value.replace(
                    year=year,
                    month=month,
                    day=min(value.day, monthrange(year, month)[1]),
                )

            if start.day == 1 and end.day == monthrange(end.year, end.month)[1]:
                return comparison_period(start, end, mode, "month")
            return shift(start), shift(end)
        if period_kind in {"current_week", "previous_week"}:
            return start - timedelta(days=7), end - timedelta(days=7)
        if (
            period_kind == "custom"
            and start.year == end.year
            and start.month == end.month
            and start.day == 1
            and end.day == monthrange(end.year, end.month)[1]
        ):
            return comparison_period(start, end, mode, "month")
    return comparison_period(start, end, mode, period_kind)


def chart_groupings(rows):
    """Aggregate daily SQL totals before float conversion; never average averages."""
    result = {}
    for grouping in ("day", "week", "month"):
        buckets = {}
        for row in rows:
            day = row.period.date()
            bucket = (
                day - timedelta(days=day.weekday())
                if grouping == "week"
                else day.replace(day=1) if grouping == "month" else day
            )
            totals = buckets.setdefault(bucket, [0, 0, 0])
            totals[0] += row.revenue or 0
            totals[1] += row.sales_count or 0
            totals[2] += row.items_count or 0
        result[grouping] = [
            {
                "period": bucket,
                **calculated_metrics(
                    float(values[0]), int(values[1]), float(values[2])
                ),
            }
            for bucket, values in sorted(buckets.items())
        ]
    return result
