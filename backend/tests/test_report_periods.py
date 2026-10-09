from datetime import date

from app.services.report_periods import comparison_period, previous_period, previous_year_period


def test_previous_year_handles_leap_day_and_year_boundary():
    assert previous_year_period(date(2024, 2, 29), date(2024, 3, 2)) == (date(2023, 2, 28), date(2023, 3, 2))
    assert previous_year_period(date(2026, 12, 29), date(2027, 1, 4)) == (date(2025, 12, 29), date(2026, 1, 4))


def test_previous_period_for_day_week_and_custom_range():
    assert previous_period(date(2026, 10, 8), date(2026, 10, 8)) == (date(2026, 10, 7), date(2026, 10, 7))
    assert previous_period(date(2026, 10, 5), date(2026, 10, 11)) == (date(2026, 9, 28), date(2026, 10, 4))
    assert previous_period(date(2026, 10, 1), date(2026, 10, 8)) == (date(2026, 9, 23), date(2026, 9, 30))


def test_previous_calendar_month_and_year():
    assert previous_period(date(2026, 3, 1), date(2026, 3, 31), "month") == (date(2026, 2, 1), date(2026, 2, 28))
    assert previous_period(date(2026, 1, 1), date(2026, 12, 31), "year") == (date(2025, 1, 1), date(2025, 12, 31))
    assert comparison_period(date(2026, 3, 1), date(2026, 3, 31), "previous_year") == (date(2025, 3, 1), date(2025, 3, 31))
