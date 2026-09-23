from datetime import date, timedelta

from app.services.sales_dynamics_report import metric_comparison


METRIC_KEYS = ("revenue", "checks", "average_check", "items", "items_per_check", "average_discount")


def comparable_period(date_from: date, date_to: date) -> tuple[date, date]:
    days = (date_to - date_from).days + 1
    return date_from - timedelta(days=days), date_from - timedelta(days=1)


def calculated_store_metrics(
    revenue: float,
    checks: int,
    items: float,
    discount_sum: float,
    discount_count: int,
) -> dict[str, float]:
    return {
        "revenue": revenue,
        "checks": checks,
        "average_check": revenue / checks if checks else 0,
        "items": items,
        "items_per_check": items / checks if checks else 0,
        "average_discount": discount_sum / discount_count if discount_count else 0,
        "discount_count": discount_count,
    }


def compare_metrics(current: dict[str, float], previous: dict[str, float]) -> dict:
    return {key: metric_comparison(current.get(key, 0), previous.get(key, 0)) for key in METRIC_KEYS}


def merge_store_rows(current: list[dict], previous: list[dict]) -> list[dict]:
    current_by = {(row.get("store") or "Без подразделения"): row for row in current}
    previous_by = {(row.get("store") or "Без подразделения"): row for row in previous}
    return [
        {"store": store, "metrics": compare_metrics(current_by.get(store, {}), previous_by.get(store, {}))}
        for store in sorted(current_by.keys() | previous_by.keys())
    ]
