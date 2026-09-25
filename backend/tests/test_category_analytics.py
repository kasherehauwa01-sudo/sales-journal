from datetime import date

from app.services.category_analytics import UNCATEGORIZED, aggregate_categories, chart_structure, comparable_period, percent_change, report_summary


def item(category, revenue, units, checks):
    return {"category": category, "revenue": revenue, "units": units, "checks": len(set(checks))}


def test_comparable_period_has_the_same_length():
    assert comparable_period(date(2026, 9, 1), date(2026, 9, 22)) == (date(2026, 8, 10), date(2026, 8, 31))


def test_category_share_average_check_and_distinct_checks():
    rows = aggregate_categories([item("Посуда", 1000, 5, [1, 2, 3]), item("Текстиль", 1000, 5, [1])], [])
    dishes = next(row for row in rows if row["category"] == "Посуда")
    assert dishes["current_share"] == 50
    assert dishes["current_checks"] == 3
    assert dishes["current_average_check"] == 1000 / 3


def test_changes_share_points_and_contribution_are_calculated():
    rows = aggregate_categories([item("Посуда", 1200, 4, [1]), item("Декор", 800, 2, [2])], [item("Посуда", 600, 2, [3]), item("Декор", 400, 1, [4])])
    dishes = next(row for row in rows if row["category"] == "Посуда")
    assert dishes["revenue_change"] == 600
    assert dishes["revenue_change_percent"] == 100
    assert dishes["share_change_pp"] == 0
    assert dishes["contribution_percent"] == 60


def test_new_disappeared_and_uncategorized_categories_are_kept():
    rows = aggregate_categories([item(None, 300, 1, [1]), item("Новая", 200, 1, [2])], [item("Исчезла", 400, 2, [3])])
    by_name = {row["category"]: row for row in rows}
    assert by_name[UNCATEGORIZED]["current_revenue"] == 300
    assert by_name["Новая"]["previous_revenue"] == 0
    assert by_name["Новая"]["revenue_change_percent"] is None
    assert by_name["Исчезла"]["current_revenue"] == 0
    assert by_name["Исчезла"]["revenue_change_percent"] == -100


def test_summary_uses_global_distinct_check_count():
    rows = aggregate_categories([item("A", 100, 1, [1, 2]), item("B", 200, 2, [2, 3])], [])
    summary = report_summary(rows, current_checks=3, previous_checks=0)
    assert summary["checks"]["current"] == 3
    assert summary["average_check"]["current"] == 100
    assert summary["categories"]["current"] == 2


def test_zero_previous_value_and_zero_total_change_are_safe():
    assert percent_change(10, 0) is None
    rows = aggregate_categories([item("A", 150, 1, [1]), item("B", 50, 1, [2])], [item("A", 100, 1, [3]), item("B", 100, 1, [4])])
    assert all(row["contribution_percent"] is None for row in rows)


def test_structure_keeps_uncategorized_outside_other():
    current = [item(f"Категория {index}", 100-index, 1, [index]) for index in range(11)] + [item(None, 1, 1, [20])]
    structure = chart_structure(aggregate_categories(current, []), limit=10)
    assert any(row["category"] == UNCATEGORIZED for row in structure)
    assert any(row["category"] == "Прочие" for row in structure)
