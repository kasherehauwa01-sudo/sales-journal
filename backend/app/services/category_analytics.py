from datetime import date, timedelta

from app.services.sales_dynamics_report import metric_comparison

UNCATEGORIZED = "Без категории"


def categorized_only(rows: list[dict]) -> list[dict]:
    """Исключает техническую группу несопоставленных товаров из отчёта."""
    excluded = {"без категории", "без категорий"}
    return [row for row in rows if str(row.get("category") or "").strip().casefold() not in excluded]


def comparable_period(start: date, end: date) -> tuple[date, date]:
    days = (end - start).days + 1
    return start - timedelta(days=days), start - timedelta(days=1)


def percent_change(current: float, previous: float) -> float | None:
    return None if previous == 0 else (current - previous) / previous * 100


def aggregate_categories(current: list[dict], previous: list[dict]) -> list[dict]:
    """Объединяет уже агрегированные SKU; строки продаж здесь не обрабатываются."""
    totals: dict[str, dict] = {}
    for period, rows in (("current", current), ("previous", previous)):
        for row in rows:
            category = row.get("category") or UNCATEGORIZED
            target = totals.setdefault(category, {
                "category": category, "current_revenue": 0.0, "previous_revenue": 0.0,
                "current_units": 0.0, "previous_units": 0.0,
                "current_checks": 0, "previous_checks": 0, "products": [],
            })
            target[f"{period}_revenue"] += float(row.get("revenue", 0))
            target[f"{period}_units"] += float(row.get("units", 0))
            target[f"{period}_checks"] += int(row.get("checks", 0))
            target["products"].append(row)
    current_total = sum(row["current_revenue"] for row in totals.values())
    previous_total = sum(row["previous_revenue"] for row in totals.values())
    total_change = current_total - previous_total
    result = []
    for row in totals.values():
        current_checks, previous_checks = row["current_checks"], row["previous_checks"]
        current_share = row["current_revenue"] / current_total * 100 if current_total else 0
        previous_share = row["previous_revenue"] / previous_total * 100 if previous_total else 0
        change = row["current_revenue"] - row["previous_revenue"]
        row.update(
            current_checks=current_checks, previous_checks=previous_checks,
            current_average_check=row["current_revenue"] / current_checks if current_checks else 0,
            previous_average_check=row["previous_revenue"] / previous_checks if previous_checks else 0,
            current_share=current_share, previous_share=previous_share,
            share_change_pp=current_share - previous_share, revenue_change=change,
            revenue_change_percent=percent_change(row["current_revenue"], row["previous_revenue"]),
            contribution_percent=change / total_change * 100 if total_change else None,
            contribution="Рост" if change > 0 else "Падение" if change < 0 else "Без изменений",
        )
        result.append(row)
    return sorted(result, key=lambda row: row["current_revenue"], reverse=True)


def report_summary(rows: list[dict], current_checks: int, previous_checks: int) -> dict:
    current_revenue = sum(row["current_revenue"] for row in rows)
    previous_revenue = sum(row["previous_revenue"] for row in rows)
    current_units = sum(row["current_units"] for row in rows)
    previous_units = sum(row["previous_units"] for row in rows)
    current_categories = sum(row["current_revenue"] != 0 for row in rows)
    previous_categories = sum(row["previous_revenue"] != 0 for row in rows)
    return {
        "revenue": metric_comparison(current_revenue, previous_revenue),
        "units": metric_comparison(current_units, previous_units),
        "checks": metric_comparison(current_checks, previous_checks),
        "average_check": metric_comparison(current_revenue / current_checks if current_checks else 0, previous_revenue / previous_checks if previous_checks else 0),
        "categories": metric_comparison(current_categories, previous_categories),
    }


def chart_structure(rows: list[dict], limit: int = 10) -> list[dict]:
    # «Без категории» остаётся в таблице для контроля сопоставления, но не
    # является категорией CatalogVR и потому не показывается сектором диаграммы.
    active = [row for row in categorized_only(rows) if row["current_revenue"] > 0]
    visible = active[:limit]
    others = active[limit:]
    if others:
        visible.append({"category": "Прочие", "current_revenue": sum(row["current_revenue"] for row in others), "current_share": sum(row["current_share"] for row in others)})
    return [{"category": row["category"], "revenue": row["current_revenue"], "share": row["current_share"]} for row in visible]
