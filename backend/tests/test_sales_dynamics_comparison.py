from datetime import date
import pytest
from app.services.sales_dynamics_report import dynamics_comparison_period


@pytest.mark.parametrize(
    "start,end,kind,expected",
    [
        (
            date(2026, 10, 1),
            date(2026, 10, 12),
            "current_month",
            (date(2026, 9, 1), date(2026, 9, 12)),
        ),
        (
            date(2026, 10, 5),
            date(2026, 10, 7),
            "current_week",
            (date(2026, 9, 28), date(2026, 9, 30)),
        ),
        (
            date(2026, 9, 1),
            date(2026, 9, 30),
            "custom",
            (date(2026, 8, 1), date(2026, 8, 31)),
        ),
        (
            date(2026, 3, 1),
            date(2026, 3, 31),
            "previous_month",
            (date(2026, 2, 1), date(2026, 2, 28)),
        ),
        (
            date(2026, 1, 1),
            date(2026, 1, 12),
            "current_month",
            (date(2025, 12, 1), date(2025, 12, 12)),
        ),
        (
            date(2026, 9, 10),
            date(2026, 9, 12),
            "custom",
            (date(2026, 9, 7), date(2026, 9, 9)),
        ),
    ],
)
def test_previous_calendar_or_custom_period(start, end, kind, expected):
    assert dynamics_comparison_period(start, end, "previous_period", kind) == expected


def test_previous_year_leap_day():
    assert dynamics_comparison_period(
        date(2024, 2, 1), date(2024, 2, 29), "previous_year", "custom"
    ) == (date(2023, 2, 1), date(2023, 2, 28))


def test_chart_groupings_preserve_totals_and_weighted_averages():
    from datetime import datetime
    from decimal import Decimal
    from types import SimpleNamespace
    from app.services.sales_dynamics_report import chart_groupings

    rows = [
        SimpleNamespace(
            period=datetime(2025, 12, 31),
            revenue=Decimal("0.10"),
            sales_count=1,
            items_count=Decimal("2"),
        ),
        SimpleNamespace(
            period=datetime(2026, 1, 1),
            revenue=Decimal("0.20"),
            sales_count=2,
            items_count=Decimal("4"),
        ),
    ]
    charts = chart_groupings(rows)
    assert len(charts["day"]) == 2
    assert len(charts["week"]) == 1
    assert len(charts["month"]) == 2
    week = charts["week"][0]
    assert week["period"].isoformat() == "2025-12-29"
    assert week["revenue"] == 0.3
    assert week["sales_count"] == 3
    assert week["average_check"] == 0.3 / 3
    assert week["average_items_per_sale"] == 2
    assert chart_groupings([]) == {"day": [], "week": [], "month": []}
