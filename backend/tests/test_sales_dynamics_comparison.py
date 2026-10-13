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
